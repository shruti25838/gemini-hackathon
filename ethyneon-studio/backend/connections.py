from dataclasses import dataclass, field
import re
from typing import Any, Literal
from uuid import uuid4

import httpx

from backend.config import SHOPIFY_STORE_DOMAIN
from backend.errors import ConnectionError


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_REVISION = "2026-05-20"
SHOP_MCP_URL = "https://mcp.shop.com/"
SHOP_MCP_VERSION = "2026-07-28"
SHOPIFY_PRODUCT_ID = r"^gid://shopify/(p|Product)/[A-Za-z0-9_-]+$"
SHOPIFY_VARIANT_ID = r"^gid://shopify/(v|ProductVariant)/[A-Za-z0-9_-]+$"
INTERACTION_ID = r"^[A-Za-z0-9_-]+$"
ShopTool = Literal["search_catalog", "get_product", "lookup_catalog"]


def invalid_response(provider: str) -> ConnectionError:
    return ConnectionError(
        502, "invalid_provider_response", f"{provider} returned an invalid response.",
        details={"provider": provider},
    )


def interaction_output_text(response: dict[str, Any]) -> str:
    for step in reversed(response.get("steps", [])):
        if step.get("type") == "model_output":
            output = "".join(block["text"] for block in step.get("content", []) if block.get("type") == "text")
            if output:
                return output
    return ""


async def request_object(
    http: httpx.AsyncClient, provider: str, method: str, url: str, *,
    retry_safe: bool = False, **kwargs: Any,
) -> dict[str, Any]:
    """Request a JSON object without exposing upstream bodies or credentials in errors."""
    try:
        response = await http.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise ConnectionError(
            504, "provider_timeout", f"{provider} timed out.", retryable=retry_safe,
            details={"provider": provider},
        ) from exc
    except httpx.RequestError as exc:
        raise ConnectionError(
            502, "provider_unreachable", f"Could not reach {provider}.", retryable=retry_safe,
            details={"provider": provider},
        ) from exc

    upstream = response.status_code
    if not response.is_success:
        status, code, message = {
            400: (400, "provider_invalid_request", "rejected the request"),
            401: (502, "provider_authentication_failed", "rejected the configured credentials"),
            403: (502, "provider_access_denied", "denied access"),
            404: (404, "provider_not_found", "could not find the requested resource"),
            429: (429, "provider_rate_limited", "reached its rate limit"),
        }.get(upstream, (502, "provider_request_failed", "could not complete the request"))
        raise ConnectionError(
            status, code, f"{provider} {message}.",
            retryable=retry_safe and (upstream in {408, 429} or upstream >= 500),
            details={"provider": provider, "upstream_status": upstream},
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise invalid_response(provider) from exc
    if not isinstance(body, dict):
        raise invalid_response(provider)
    return body


@dataclass
class GeminiConnection:
    http: httpx.AsyncClient
    api_key: str = field(default="", repr=False)

    async def _request(self, method: str, resource: str, **kwargs: Any) -> dict[str, Any]:
        if not self.api_key:
            raise ConnectionError(503, "gemini_not_configured", "Configure GEMINI_API_KEY on the server.")
        headers = {"x-goog-api-key": self.api_key}
        if resource.startswith("interactions"):
            headers["Api-Revision"] = GEMINI_REVISION
        return await request_object(
            self.http, "Gemini", method, f"{GEMINI_URL}/{resource}", headers=headers,
            retry_safe=method == "GET" or resource == "decisions",
            **kwargs,
        )

    async def interact(self, payload: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        options = {"timeout": timeout} if timeout else {}
        return await self._request("POST", "interactions", json=payload, **options)

    async def decide(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._request("POST", "decisions", json={**payload, "model": "gemini-decision-1"})
        if not isinstance(result.get("answers"), dict):
            raise invalid_response("Gemini Decisions API")
        return result

    async def start_managed_run(
        self,
        *,
        input: str | list[dict[str, Any]],
        model: str,
        max_total_tokens: int,
        previous_interaction_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "agent": "antigravity-preview-09-2026",
            "input": input,
            "environment": "remote",
            "background": True,
            "agent_config": {
                "type": "antigravity", "model": model, "max_total_tokens": max_total_tokens,
            },
            "tools": [{"type": "code_execution"}],
        }
        if previous_interaction_id:
            payload["previous_interaction_id"] = previous_interaction_id
        result = await self.interact(payload)
        if not isinstance(result.get("id"), str) or not re.fullmatch(INTERACTION_ID, result["id"]):
            raise invalid_response("Gemini Managed Agents")
        return result

    async def get_interaction(self, interaction_id: str) -> dict[str, Any]:
        if not re.fullmatch(INTERACTION_ID, interaction_id):
            raise ConnectionError(422, "invalid_interaction_id", "Provide a valid Gemini interaction ID.")
        return await self._request("GET", f"interactions/{interaction_id}")


@dataclass
class ShopifyConnection:
    http: httpx.AsyncClient
    shops: tuple[str, ...] = ()

    async def _call(self, name: ShopTool, catalog: dict[str, Any]) -> dict[str, Any]:
        if self.shops:
            catalog = {**catalog, "filters": {**catalog.get("filters", {}), "shops": list(self.shops)}}
        request_id = uuid4().hex
        body = await request_object(
            self.http, "Shop MCP", "POST", SHOP_MCP_URL, retry_safe=True,
            headers={
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": SHOP_MCP_VERSION,
                "Mcp-Method": "tools/call",
                "Mcp-Name": name,
            },
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": {"catalog": catalog},
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": SHOP_MCP_VERSION,
                        "io.modelcontextprotocol/clientInfo": {"name": "ethyneon", "version": "3.0.0"},
                        "io.modelcontextprotocol/clientCapabilities": {},
                    },
                },
            },
        )
        if body.get("id") != request_id:
            raise invalid_response("Shop MCP")
        if body.get("error"):
            raise ConnectionError(502, "shop_rpc_error", "Shop MCP rejected the catalog call.")
        result = body.get("result")
        if not isinstance(result, dict):
            raise invalid_response("Shop MCP")
        if result.get("isError"):
            raise ConnectionError(502, "shop_catalog_error", "Shop MCP could not complete the catalog lookup.")
        document = result.get("structuredContent")
        if not isinstance(document, dict):
            raise invalid_response("Shop MCP")
        return document

    def _shopify_variants(self, product: Any, exact_id: str | None = None) -> dict[str, Any] | None:
        if not isinstance(product, dict) or not re.fullmatch(SHOPIFY_PRODUCT_ID, str(product.get("id"))):
            return None
        variants = product.get("variants")
        if not isinstance(variants, list):
            return None

        accepted = []
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            variant_id = variant.get("id")
            seller = variant.get("seller")
            domain = str(seller.get("domain", "")).lower() if isinstance(seller, dict) else ""
            if not re.fullmatch(SHOPIFY_VARIANT_ID, str(variant_id)) or not SHOPIFY_STORE_DOMAIN.fullmatch(domain):
                continue
            if (self.shops and domain not in self.shops) or (exact_id and variant_id != exact_id):
                continue
            accepted.append(variant)
        return {**product, "variants": accepted} if accepted else None

    async def search_catalog(self, catalog: dict[str, Any]) -> dict[str, Any]:
        document = await self._call("search_catalog", catalog)
        products = document.get("products")
        if not isinstance(products, list):
            raise invalid_response("Shop MCP")
        return {
            **document,
            "products": [verified for item in products if (verified := self._shopify_variants(item))],
        }

    async def get_product(self, catalog: dict[str, Any]) -> dict[str, Any]:
        document = await self._call("get_product", catalog)
        product = self._shopify_variants(document.get("product"))
        if product is None:
            raise ConnectionError(404, "shop_product_not_found", "No verified Shopify product matches the request.")
        return {**document, "product": product}

    async def lookup_variant(self, variant_id: str, context: dict[str, str]) -> dict[str, Any]:
        document = await self._call("lookup_catalog", {"ids": [variant_id], "context": context})
        products = document.get("products")
        if not isinstance(products, list):
            raise invalid_response("Shop MCP")
        exact_matches = [
            verified for item in products if (verified := self._shopify_variants(item, exact_id=variant_id))
        ]
        if not exact_matches:
            raise ConnectionError(404, "shop_variant_not_found", "The exact Shopify variant could not be verified.")
        return {**document, "products": exact_matches}
