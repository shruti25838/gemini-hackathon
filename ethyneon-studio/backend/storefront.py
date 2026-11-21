"""Product search against the connected development store's Storefront API.

Studio's discovery was Shop MCP, which searches the public Shop catalog across
every merchant. That is the wrong source for a demo that can only buy from one
store: a shopper could search, place a product in the room, reach checkout, and
only then be told the thing is view-only. Searching the store we can actually
sell from means everything on screen is something the shopper can buy.

The documents this returns use the same shape Shop MCP returns, so every
consumer (`studio.catalog_offer`, `scene.similar`, `checkout._verify`,
`product_models.generate`) is unchanged. The shape is:

    {"products": [{"id", "title", "description", "media": [...],
                   "variants": [{"id", "seller": {"domain"}, "price",
                                 "availability": {"available"}, "url",
                                 "options", "media"}]}]}

Shop MCP stays reachable through `CatalogConnection.discovery` and is still the
fallback when no Storefront store is configured, so an unconfigured Studio
behaves exactly as it did before.
"""

from dataclasses import dataclass, field
from html import unescape
import re
from typing import Any

import httpx

from backend.connections import ShopifyConnection, invalid_response, request_object
from backend.errors import ConnectionError


STOREFRONT_VERSION = "2026-10"
MAX_VARIANTS = 20
MAX_RESULTS = 50
MAX_MEDIA = 12

PRODUCT_FIELDS = """
  id
  title
  description
  onlineStoreUrl
  featuredImage { url }
  media(first: 12) { nodes { ... on MediaImage { image { url } } } }
  variants(first: 20) {
    nodes {
      id
      availableForSale
      price { amount currencyCode }
      selectedOptions { name value }
      image { url }
    }
  }
"""

SEARCH_QUERY = """
query StoreSearch($query: String!, $first: Int!) {
  products(first: $first, query: $query) {
    nodes {
%s  }
  }
}
""" % PRODUCT_FIELDS

PRODUCT_QUERY = """
query StoreProduct($id: ID!) {
  product(id: $id) {
%s  }
}
""" % PRODUCT_FIELDS

VARIANT_QUERY = """
query StoreVariant($id: ID!) {
  node(id: $id) {
    ... on ProductVariant {
      id
      availableForSale
      price { amount currencyCode }
      selectedOptions { name value }
      image { url }
      product {
%s      }
    }
  }
}
""" % PRODUCT_FIELDS


def money_minor(price: Any) -> dict[str, Any] | None:
    """Shopify returns decimal strings like "429.00". Convert to integer minor
    units without letting a float near the value.

    Two decimal places, matching `money_from_shopify` in the purchase server.
    Both sides must agree to the cent or the cart reconciliation fails, so this
    deliberately mirrors that function rather than being cleverer than it.
    """
    if not isinstance(price, dict) or price.get("amount") is None:
        return None
    text = str(price["amount"]).strip()
    whole, _, fraction = text.partition(".")
    fraction = (fraction + "00")[:2]
    sign = -1 if whole.startswith("-") else 1
    whole = whole.lstrip("+-") or "0"
    try:
        minor = sign * (int(whole) * 100 + int(fraction))
    except ValueError:
        return None
    return {"amount": minor, "currency": price.get("currencyCode") or "USD"}


def in_price_range(price: dict[str, Any] | None, bounds: dict[str, Any], currency: str = "") -> bool:
    """Both bounds are integer minor units, the same unit the price is in.

    `currency` is what the caller asked to be billed in. A store priced in one
    currency cannot answer a bound stated in another: 30000 minor units is
    $300 or £300, and comparing them as numbers silently returns products
    outside the budget. When they disagree the variant is dropped.
    """
    if not bounds:
        return True
    if not price:
        return False
    if currency and price.get("currency") and currency.upper() != price["currency"].upper():
        return False
    amount = price["amount"]
    low, high = bounds.get("min"), bounds.get("max")
    return (low is None or amount >= low) and (high is None or amount <= high)


def plain_text(value: Any) -> str:
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", str(value or ""))).split())[:4000]


def variant_listing_url(product_url: str | None, variant_id: str) -> str | None:
    """Deep-link to the exact variant, so the listing the shopper opens is the
    one that was priced and placed, not the product's default."""
    if not product_url:
        return None
    number = variant_id.rsplit("/", 1)[-1]
    joiner = "&" if "?" in product_url else "?"
    return f"{product_url}{joiner}variant={number}"


@dataclass
class StorefrontConnection:
    """One development store, read only. This client never writes a cart."""

    http: httpx.AsyncClient
    domain: str = ""
    token: str = field(default="", repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.domain and self.token)

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise ConnectionError(
                503, "storefront_not_configured",
                "Configure SHOPIFY_STOREFRONT_DOMAIN and SHOPIFY_STOREFRONT_TOKEN to search the store.",
            )
        result = await request_object(
            self.http, "Shopify Storefront", "POST",
            f"https://{self.domain}/api/{STOREFRONT_VERSION}/graphql.json",
            retry_safe=True, timeout=10.0, follow_redirects=False,
            headers={"X-Shopify-Storefront-Access-Token": self.token},
            json={"query": query, "variables": variables},
        )
        if result.get("errors"):
            # The bodies can echo the query and the store's own wording, so the
            # message stays generic and nothing upstream is forwarded.
            raise ConnectionError(502, "storefront_query_failed", "Shopify Storefront rejected the search.")
        data = result.get("data")
        if not isinstance(data, dict):
            raise invalid_response("Shopify Storefront")
        return data

    def _variant(self, node: Any, product_url: str | None) -> dict[str, Any] | None:
        if not isinstance(node, dict) or not str(node.get("id", "")).startswith(
            "gid://shopify/ProductVariant/"
        ):
            return None
        price = money_minor(node.get("price"))
        if price is None:
            # No price means nothing can be placed or bought, so the variant is
            # dropped rather than shown at zero.
            return None
        image = (node.get("image") or {}).get("url")
        return {
            "id": node["id"],
            "seller": {"domain": self.domain},
            "price": price,
            "availability": {"available": bool(node.get("availableForSale"))},
            "url": variant_listing_url(product_url, node["id"]),
            "options": [
                {"name": option["name"], "label": option["value"]}
                for option in node.get("selectedOptions") or []
                if isinstance(option, dict) and option.get("name") and option.get("value")
            ],
            "media": [{"type": "image", "url": image}] if image else [],
        }

    def _product(self, node: Any) -> dict[str, Any] | None:
        if not isinstance(node, dict) or not str(node.get("id", "")).startswith(
            "gid://shopify/Product/"
        ):
            return None
        product_url = node.get("onlineStoreUrl")
        variants = [
            built for raw in ((node.get("variants") or {}).get("nodes") or [])[:MAX_VARIANTS]
            if (built := self._variant(raw, product_url))
        ]
        if not variants:
            return None
        gallery = [(node.get("featuredImage") or {}).get("url")]
        gallery += [
            (entry.get("image") or {}).get("url")
            for entry in ((node.get("media") or {}).get("nodes") or [])
            if isinstance(entry, dict)
        ]
        seen: dict[str, None] = {}
        for url in gallery:
            if url:
                seen.setdefault(url, None)
        return {
            "id": node["id"],
            "title": str(node.get("title") or "")[:500],
            "description": plain_text(node.get("description")),
            "url": product_url,
            "media": [{"type": "image", "url": url} for url in list(seen)[:MAX_MEDIA]],
            "variants": variants,
        }

    # -- the three calls the rest of Studio makes --------------------------

    async def search_catalog(self, catalog: dict[str, Any]) -> dict[str, Any]:
        query = " ".join(str(catalog.get("query") or "").split())[:200]
        if not query:
            raise ConnectionError(422, "search_query_missing", "Provide something to search for.")
        if catalog.get("like"):
            # Shop MCP can search by image; a single store's Storefront API
            # cannot. Refusing is the honest answer. Silently running the text
            # query instead would return confident, unrelated results.
            raise ConnectionError(
                422, "image_search_unsupported",
                "Searching by image is a Shop discovery feature and is not available on the connected store.",
            )

        limit = max(1, min(int((catalog.get("pagination") or {}).get("limit") or 10), MAX_RESULTS))
        price = (catalog.get("filters") or {}).get("price") or {}
        currency = str((catalog.get("context") or {}).get("currency") or "")
        # Price bounds are applied here, on values already converted to minor
        # units, rather than in the store's query syntax. One conversion, one
        # comparison, and the bound cannot drift from the price displayed.
        # Over-fetch so filtering does not return a short page.
        wanted = min(limit * 3, MAX_RESULTS) if price else limit
        data = await self._graphql(SEARCH_QUERY, {"query": query, "first": wanted})
        nodes = (data.get("products") or {}).get("nodes")
        if not isinstance(nodes, list):
            raise invalid_response("Shopify Storefront")

        products = []
        for node in nodes:
            built = self._product(node)
            if built is None:
                continue
            kept = [variant for variant in built["variants"]
                    if in_price_range(variant["price"], price, currency)]
            if kept:
                products.append({**built, "variants": kept})
            if len(products) >= limit:
                break
        return {
            "source": "storefront_api",
            "store_domain": self.domain,
            "products": products,
        }

    async def get_product(self, catalog: dict[str, Any]) -> dict[str, Any]:
        product_id = catalog.get("id") or (catalog.get("ids") or [None])[0]
        data = await self._graphql(PRODUCT_QUERY, {"id": product_id})
        product = self._product(data.get("product"))
        if product is None:
            raise ConnectionError(
                404, "shop_product_not_found", "No verified Shopify product matches the request.")
        return {"source": "storefront_api", "store_domain": self.domain, "product": product}

    async def lookup_variant(self, variant_id: str, context: dict[str, str]) -> dict[str, Any]:
        data = await self._graphql(VARIANT_QUERY, {"id": variant_id})
        node = data.get("node")
        if not isinstance(node, dict) or node.get("id") != variant_id:
            raise ConnectionError(
                404, "shop_variant_not_found", "The exact Shopify variant could not be verified.")
        product = self._product(node.get("product"))
        if product is None:
            raise ConnectionError(
                404, "shop_variant_not_found", "The exact Shopify variant could not be verified.")
        exact = [variant for variant in product["variants"] if variant["id"] == variant_id]
        if not exact:
            raise ConnectionError(
                404, "shop_variant_not_found", "The exact Shopify variant could not be verified.")
        return {
            "source": "storefront_api", "store_domain": self.domain,
            "products": [{**product, "variants": exact}],
        }


@dataclass
class CatalogConnection:
    """Routes every catalog read to the store we can sell from, when there is one.

    With a Storefront store configured, search and variant lookups go there, so
    what the shopper sees is what the purchase server will accept. Without one,
    every call falls through to Shop MCP and Studio behaves exactly as before.

    `discovery` stays available for a deliberate cross-merchant look. Results
    from it are not purchasable, and the purchase server refuses them.
    """

    storefront: StorefrontConnection
    discovery: ShopifyConnection

    @property
    def source(self) -> str:
        return "storefront_api" if self.storefront.configured else "shop_mcp"

    async def search_catalog(self, catalog: dict[str, Any]) -> dict[str, Any]:
        if self.storefront.configured:
            return await self.storefront.search_catalog(catalog)
        return await self.discovery.search_catalog(catalog)

    async def get_product(self, catalog: dict[str, Any]) -> dict[str, Any]:
        if self.storefront.configured:
            try:
                return await self.storefront.get_product(catalog)
            except ConnectionError as error:
                if error.status != 404:
                    raise
        return await self.discovery.get_product(catalog)

    async def lookup_variant(self, variant_id: str, context: dict[str, str]) -> dict[str, Any]:
        """Prefer the store's own answer, because price and stock there are the
        ones checkout will be held to. Fall back to Shop MCP so a product that
        reached the room from discovery can still be re-read and then correctly
        reported as view-only, rather than failing the whole review."""
        if self.storefront.configured:
            try:
                return await self.storefront.lookup_variant(variant_id, context)
            except ConnectionError as error:
                if error.status != 404:
                    raise
        return await self.discovery.lookup_variant(variant_id, context)
