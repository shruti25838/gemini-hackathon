"""Shop MCP client. Discovery only, never a purchase.

Shop MCP is a public catalog across many merchants. Its results are labelled
discovery on screen and can never reach a cart: the purchasable path is the
configured development store through the Storefront API.

The protocol details here are not documented on the official pages. They were
established by probing the live server on 2026-11-21 and the working recipe is
recorded in docs/SHOP_MCP_NOTES.md. In short:

  POST https://mcp.shop.com/      (the root, not /mcp and not /sse)
  stateless: there is no initialize handshake
  headers   MCP-Protocol-Version: 2026-07-28
            Mcp-Method: tools/list | tools/call
            Mcp-Name: <tool name>        (required on tools/call only)
  params    _meta carries io.modelcontextprotocol/protocolVersion and
            io.modelcontextprotocol/clientCapabilities on every request

The allowlist below is the safety boundary. The server exposes 22 tools, 11 of
which create carts, start or complete checkouts, or touch saved payment
methods. This client can only ever name the four read-only ones, and
call_tool refuses anything else before a request is built.

Phase 0 provides the transport, the allowlist and the error shape. Phase 1
adds normalisation into CatalogOffer, the ttl-based cache and the dimension
extractor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

# ---------------------------------------------------------------------------
# The allowlist. Nothing else may ever be called.
# ---------------------------------------------------------------------------

ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "search_catalog",
        "lookup_catalog",
        "get_product",
        "get_checkout",
    }
)

# Named explicitly so the test suite can assert each one is refused, and so a
# reader can see exactly what is being kept out.
PURCHASE_AND_PAYMENT_TOOLS: frozenset[str] = frozenset(
    {
        "create_cart",
        "update_cart",
        "create_checkout",
        "update_checkout",
        "complete_checkout",
        "create_spend_permission",
        "create_spend_request",
        "get_spend_request",
        "update_spend_request",
        "cancel_spend_request",
        "complete_spend_request",
        "get_account",
    }
)

# The remaining tools the server exposes. Not purchase paths, but not needed
# for this build either, so they stay off the allowlist.
OTHER_SERVER_TOOLS: frozenset[str] = frozenset(
    {
        "get_cart",
        "search_locations",
        "lookup_locations",
        "ask_business",
        "search_orders",
        "get_order",
    }
)

ALL_KNOWN_SERVER_TOOLS = ALLOWED_TOOLS | PURCHASE_AND_PAYMENT_TOOLS | OTHER_SERVER_TOOLS

DEFAULT_ENDPOINT = "https://mcp.shop.com/"
DEFAULT_PROTOCOL_VERSION = "2026-07-28"

# What the server advertised on tools/list when this was written. The live
# value is preferred; this is only the fallback.
FALLBACK_CACHE_TTL_MS = 3_600_000


class ShopMcpToolNotAllowed(Exception):
    """Raised before any request is built, when a caller names a blocked tool."""

    def __init__(self, tool: str) -> None:
        if tool in PURCHASE_AND_PAYMENT_TOOLS:
            why = (
                "it creates a cart, starts or completes a checkout, or touches "
                "saved payment details"
            )
        elif tool in OTHER_SERVER_TOOLS:
            why = "it is not on the allowlist for this build"
        else:
            why = "it is not a known Shop MCP tool"
        super().__init__(
            f"Shop MCP tool {tool!r} is refused: {why}. "
            f"Allowed: {', '.join(sorted(ALLOWED_TOOLS))}."
        )
        self.tool = tool


class ShopMcpError(Exception):
    """A transport or protocol failure talking to the Shop MCP server."""

    def __init__(self, message: str, *, code: Optional[int] = None, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ShopMcpResult:
    """One raw tool result, before normalisation into CatalogOffer."""

    tool: str
    structured_content: dict[str, Any]
    ttl_ms: Optional[int]
    cache_scope: Optional[str]
    raw: dict[str, Any]


def assert_tool_allowed(tool: str) -> None:
    """The single checkpoint. Call this before building any request."""
    if tool not in ALLOWED_TOOLS:
        raise ShopMcpToolNotAllowed(tool)


def build_headers(method: str, tool: Optional[str], protocol_version: str) -> dict[str, str]:
    """The non-standard headers this server requires.

    Omitting Mcp-Method returns -32020. Omitting Mcp-Name on a tools/call
    returns -32020. Neither is mentioned in the published skill pages.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": protocol_version,
        "Mcp-Method": method,
    }
    if method == "tools/call":
        if tool is None:
            raise ValueError("tools/call requires a tool name for the Mcp-Name header")
        assert_tool_allowed(tool)
        headers["Mcp-Name"] = tool
    return headers


def build_payload(
    method: str,
    protocol_version: str,
    *,
    tool: Optional[str] = None,
    arguments: Optional[dict[str, Any]] = None,
    request_id: int = 1,
) -> dict[str, Any]:
    """The JSON-RPC body, including the _meta block this server requires.

    Without _meta the server answers -32602: "request _meta is missing or has
    malformed required fields".
    """
    params: dict[str, Any] = {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": protocol_version,
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    }
    if method == "tools/call":
        if tool is None:
            raise ValueError("tools/call requires a tool name")
        assert_tool_allowed(tool)
        params["name"] = tool
        params["arguments"] = arguments or {}
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def parse_result(tool: str, body: dict[str, Any]) -> ShopMcpResult:
    """Unwrap a JSON-RPC response into a ShopMcpResult, or raise."""
    if "error" in body:
        error = body["error"]
        raise ShopMcpError(
            f"Shop MCP error {error.get('code')}: {error.get('message')}",
            code=error.get("code"),
            retryable=error.get("code") not in (-32602, -32020),
        )
    result = body.get("result")
    if not isinstance(result, dict):
        raise ShopMcpError("Shop MCP response had no result object", retryable=False)
    if result.get("isError"):
        raise ShopMcpError(f"Shop MCP tool {tool} reported an error", retryable=False)
    return ShopMcpResult(
        tool=tool,
        structured_content=result.get("structuredContent") or {},
        ttl_ms=result.get("ttlMs"),
        cache_scope=result.get("cacheScope"),
        raw=result,
    )


class ShopMcpClient:
    """Minimal stateless client over the Shop MCP streamable HTTP endpoint.

    The httpx import is local to the request method on purpose, so importing
    this module stays cheap and the allowlist can be unit tested with no
    network dependency present.
    """

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.endpoint = endpoint
        self.protocol_version = protocol_version
        self.timeout_seconds = timeout_seconds

    def _post(self, method: str, tool: Optional[str], payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        headers = build_headers(method, tool, self.protocol_version)
        try:
            response = httpx.post(
                self.endpoint,
                headers=headers,
                content=json.dumps(payload),
                timeout=self.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            raise ShopMcpError(f"Shop MCP request failed: {exc!r}", retryable=True) from exc

        if response.status_code != 200:
            raise ShopMcpError(
                f"Shop MCP returned HTTP {response.status_code}",
                code=response.status_code,
                retryable=response.status_code >= 500,
            )
        return response.json()

    def list_tools(self) -> dict[str, Any]:
        """Read-only. Used by the check script to prove the server is reachable."""
        payload = build_payload("tools/list", self.protocol_version)
        return self._post("tools/list", None, payload)

    def call_tool(self, tool: str, arguments: dict[str, Any]) -> ShopMcpResult:
        """The only way to invoke a tool. Refuses anything off the allowlist."""
        assert_tool_allowed(tool)
        payload = build_payload(
            "tools/call", self.protocol_version, tool=tool, arguments=arguments
        )
        body = self._post("tools/call", tool, payload)
        return parse_result(tool, body)

    def search_catalog(
        self,
        query: str,
        *,
        limit: int = 10,
        filters: Optional[dict[str, Any]] = None,
    ) -> ShopMcpResult:
        catalog: dict[str, Any] = {"query": query, "pagination": {"limit": limit}}
        if filters:
            catalog["filters"] = filters
        return self.call_tool("search_catalog", {"catalog": catalog})

    def get_product(self, product_id: str) -> ShopMcpResult:
        return self.call_tool("get_product", {"catalog": {"id": product_id}})
