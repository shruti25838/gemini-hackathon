"""Shopify Storefront API. The only purchasable product source.

Catalog reads happen here. Cart writes are implemented but gated: nothing in
this module performs a live cart mutation unless `allow_cart_writes` is
explicitly true, which is off by default and off in every test.

Shopify's own sample products (snowboards, ski wax, gift cards) ship with a
new development store and must never appear in the shoppable catalog.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from contracts.catalog_offer import (
    Availability,
    CatalogOffer,
    Freshness,
    SelectedOption,
    StoreRef,
)
from contracts.common import Money
from backend.dimensions import dimensions_from_metafields, extract_dimensions
from backend.sanitise import clean_title

# Titles and handles matching any of these are Shopify demo data, not stock.
SAMPLE_PRODUCT_MARKERS: tuple[str, ...] = (
    "snowboard",
    "ski wax",
    "skiwax",
    "gift card",
    "selling plans ski",
    "the collection snowboard",
    "the multi-managed snowboard",
    "the inventory not tracked snowboard",
    "the videographer snowboard",
    "the draft snowboard",
    "the hidden snowboard",
    "the archived snowboard",
    "the complete snowboard",
    "the 3p fulfilled snowboard",
    "the multi-location snowboard",
    "the out of stock snowboard",
)

SAMPLE_PRODUCT_TYPES: tuple[str, ...] = ("snowboard", "gift cards", "accessories - ski")


class ShopifyError(Exception):
    def __init__(self, message: str, *, retryable: bool = True, code: Optional[str] = None):
        super().__init__(message)
        self.retryable = retryable
        self.code = code


def is_sample_product(title: str, product_type: str = "", handle: str = "") -> bool:
    """Shopify's seeded demo products. Excluded from the shoppable catalog."""
    haystack = f"{title} {handle}".lower()
    if any(marker in haystack for marker in SAMPLE_PRODUCT_MARKERS):
        return True
    return product_type.strip().lower() in SAMPLE_PRODUCT_TYPES


PRODUCTS_QUERY = """
query EthyneonCatalog($first: Int!, $query: String) {
  products(first: $first, query: $query) {
    edges {
      node {
        id
        title
        handle
        productType
        onlineStoreUrl
        description
        featuredImage { url }
        width: metafield(namespace: "custom", key: "width") { value }
        depth: metafield(namespace: "custom", key: "depth") { value }
        height: metafield(namespace: "custom", key: "height") { value }
        variants(first: 20) {
          edges {
            node {
              id
              title
              availableForSale
              __INVENTORY__
              price { amount currencyCode }
              compareAtPrice { amount currencyCode }
              selectedOptions { name value }
              image { url }
            }
          }
        }
      }
    }
  }
}
"""

VARIANT_QUERY = """
query EthyneonVariant($id: ID!) {
  node(id: $id) {
    ... on ProductVariant {
      id
      title
      availableForSale
      __INVENTORY__
      price { amount currencyCode }
      compareAtPrice { amount currencyCode }
      selectedOptions { name value }
      image { url }
      product {
        id
        title
        handle
        productType
        onlineStoreUrl
        description
        featuredImage { url }
        width: metafield(namespace: "custom", key: "width") { value }
        depth: metafield(namespace: "custom", key: "depth") { value }
        height: metafield(namespace: "custom", key: "height") { value }
      }
    }
  }
}
"""

CART_CREATE = """
mutation EthyneonCartCreate($lines: [CartLineInput!]!) {
  cartCreate(input: {lines: $lines}) {
    cart {
      id
      checkoutUrl
      cost { subtotalAmount { amount currencyCode } }
      lines(first: 50) {
        edges { node { id quantity merchandise { ... on ProductVariant { id } } } }
      }
    }
    userErrors { field message }
  }
}
"""

CART_LINES_ADD = """
mutation EthyneonCartLinesAdd($cartId: ID!, $lines: [CartLineInput!]!) {
  cartLinesAdd(cartId: $cartId, lines: $lines) {
    cart {
      id
      checkoutUrl
      cost { subtotalAmount { amount currencyCode } }
      lines(first: 50) {
        edges { node { id quantity merchandise { ... on ProductVariant { id } } } }
      }
    }
    userErrors { field message }
  }
}
"""

CART_QUERY = """
query EthyneonCart($id: ID!) {
  cart(id: $id) {
    id
    checkoutUrl
    cost { subtotalAmount { amount currencyCode } }
    lines(first: 50) {
      edges { node { id quantity merchandise { ... on ProductVariant { id } } } }
    }
  }
}
"""


def money_from_shopify(amount: Any) -> Money:
    """Shopify returns decimal strings like "429.00". Convert to minor units
    without ever letting a float near the value."""
    if amount is None:
        raise ShopifyError("missing price", retryable=False)
    text = str(amount.get("amount") if isinstance(amount, dict) else amount).strip()
    currency = (
        amount.get("currencyCode", "USD") if isinstance(amount, dict) else "USD"
    )
    if "." in text:
        whole, fraction = text.split(".", 1)
        fraction = (fraction + "00")[:2]
    else:
        whole, fraction = text, "00"
    sign = -1 if whole.startswith("-") else 1
    whole = whole.lstrip("+-") or "0"
    return Money(amount_minor=sign * (int(whole) * 100 + int(fraction)), currency=currency)


class ShopifyStorefront:
    """Storefront GraphQL client for one development store."""

    def __init__(
        self,
        store_domain: str,
        token: str,
        api_version: str = "2026-10",
        *,
        timeout_seconds: float = 30.0,
        allow_cart_writes: bool = False,
        read_inventory_quantity: bool = False,
    ) -> None:
        self.store_domain = store_domain
        self.api_version = api_version
        self.timeout_seconds = timeout_seconds
        self.allow_cart_writes = allow_cart_writes
        self._token = token
        self._auth_header = self.PRIVATE_HEADER
        # quantityAvailable needs the unauthenticated_read_product_inventory
        # scope. Most Storefront tokens do not have it, and asking for a field
        # we cannot read turns every query into a GraphQL error. availableForSale
        # is what the cart actually gates on; the quantity is a bonus check that
        # is simply skipped when the scope is absent.
        self.read_inventory_quantity = read_inventory_quantity

    def _query(self, template: str) -> str:
        return template.replace(
            "__INVENTORY__", "quantityAvailable" if self.read_inventory_quantity else ""
        )

    @property
    def endpoint(self) -> str:
        return f"https://{self.store_domain}/api/{self.api_version}/graphql.json"

    @property
    def store_ref(self) -> StoreRef:
        return StoreRef(
            store_domain=self.store_domain,
            store_name=None,
            store_id=None,
            api_surface="storefront_api",
        )

    # Shopify accepts exactly one authentication method per request. Sending
    # both of these returns 401, so the client picks one and falls back to the
    # other rather than sending them together.
    PRIVATE_HEADER = "Shopify-Storefront-Private-Token"
    PUBLIC_HEADER = "X-Shopify-Storefront-Access-Token"

    # What Shopify answers when the token is real but sent under the wrong
    # header. It documents 401, and returns 403 against our development store
    # on the 2026-10 endpoint. Treating only 401 as "wrong header" meant a
    # public token never fell back: every catalog read failed with
    # "Storefront returned HTTP 403" while /api/health still said commerce
    # was live, because health reports configuration and not reachability.
    WRONG_HEADER_STATUSES = frozenset({401, 403})

    def _headers(self, auth_header: str) -> dict[str, str]:
        return {"Content-Type": "application/json", auth_header: self._token}

    def _post(self, auth_header: str, query: str, variables: dict[str, Any]):
        import httpx

        try:
            return httpx.post(
                self.endpoint,
                headers=self._headers(auth_header),
                json={"query": query, "variables": variables},
                timeout=self.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            raise ShopifyError(f"Storefront request failed: {exc!r}", retryable=True) from exc

    def execute(self, query: str, variables: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        variables = variables or {}

        response = self._post(self._auth_header, query, variables)
        if (
            response.status_code in self.WRONG_HEADER_STATUSES
            and self._auth_header == self.PRIVATE_HEADER
        ):
            # A public access token was configured instead. Switch once and
            # remember, so later calls do not pay for the retry.
            retry = self._post(self.PUBLIC_HEADER, query, variables)
            if retry.status_code == 200:
                self._auth_header = self.PUBLIC_HEADER
                response = retry

        if response.status_code != 200:
            raise ShopifyError(
                f"Storefront returned HTTP {response.status_code}",
                retryable=response.status_code >= 500,
                code=str(response.status_code),
            )

        body = response.json()
        if body.get("errors"):
            messages = "; ".join(e.get("message", "") for e in body["errors"])
            raise ShopifyError(f"Storefront GraphQL errors: {messages}", retryable=False)
        return body.get("data") or {}

    # -- catalog ----------------------------------------------------------

    def search_products(
        self, *, first: int = 20, query: Optional[str] = None
    ) -> list[CatalogOffer]:
        data = self.execute(self._query(PRODUCTS_QUERY), {"first": first, "query": query})
        offers: list[CatalogOffer] = []
        for edge in (data.get("products") or {}).get("edges", []):
            offers.extend(self._offers_from_product(edge["node"]))
        return offers

    def get_variant(self, variant_id: str) -> CatalogOffer:
        """A fresh read of one variant. Never served from cache."""
        data = self.execute(self._query(VARIANT_QUERY), {"id": variant_id})
        node = data.get("node")
        if not node:
            raise ShopifyError(
                f"variant {variant_id} not found on {self.store_domain}", retryable=False
            )
        product = node["product"]
        offer = self._offer_from_variant(product, node)
        if offer is None:
            raise ShopifyError(
                f"variant {variant_id} belongs to an excluded sample product",
                retryable=False,
            )
        return offer

    def _offers_from_product(self, product: dict[str, Any]) -> list[CatalogOffer]:
        offers: list[CatalogOffer] = []
        for edge in (product.get("variants") or {}).get("edges", []):
            offer = self._offer_from_variant(product, edge["node"])
            if offer is not None:
                offers.append(offer)
        return offers

    def _offer_from_variant(
        self, product: dict[str, Any], variant: dict[str, Any]
    ) -> Optional[CatalogOffer]:
        title = product.get("title", "")
        if is_sample_product(
            title, product.get("productType", ""), product.get("handle", "")
        ):
            return None

        dimensions = dimensions_from_metafields(
            (product.get("width") or {}).get("value"),
            (product.get("depth") or {}).get("value"),
            (product.get("height") or {}).get("value"),
        )
        dimension_source = "metafield" if dimensions else "unknown"

        if dimensions is None:
            # Fall back to the listing text, clearly labelled as inferred.
            option_labels = " ".join(
                option.get("value", "") for option in variant.get("selectedOptions", [])
            )
            inferred = extract_dimensions(
                variant.get("title"), option_labels, product.get("description")
            )
            if inferred is not None:
                dimensions = inferred
                dimension_source = "listing_text_inferred"

        listing_url = product.get("onlineStoreUrl") or (
            f"https://{self.store_domain}/products/{product.get('handle', '')}"
        )
        image = (variant.get("image") or {}).get("url") or (
            product.get("featuredImage") or {}
        ).get("url")

        compare_at = variant.get("compareAtPrice")

        return CatalogOffer(
            offer_id=f"offer_sf_{variant['id'].rsplit('/', 1)[-1]}",
            store=self.store_ref,
            product_id=product["id"],
            variant_id=variant["id"],
            # Merchant text is untrusted and is stripped before it is stored.
            title=clean_title(f"{title} {variant.get('title', '')}".strip()),
            selected_options=[
                SelectedOption(name=o["name"], value=clean_title(o["value"]))
                for o in variant.get("selectedOptions", [])
            ],
            price=money_from_shopify(variant.get("price")),
            compare_at_price=money_from_shopify(compare_at) if compare_at else None,
            availability=Availability(
                available=bool(variant.get("availableForSale")),
                quantity=variant.get("quantityAvailable"),
                checked_at=datetime.now(timezone.utc),
            ),
            listing_url=listing_url,
            image_url=image,
            dimensions=dimensions,
            dimension_source=dimension_source,  # type: ignore[arg-type]
            freshness=Freshness(
                fetched_at=datetime.now(timezone.utc), ttl_ms=None, source_label="live"
            ),
            shoppable=True,
        )

    # -- cart, gated ------------------------------------------------------

    def create_cart(self, lines: list[dict[str, Any]]) -> dict[str, Any]:
        """Write the Shopify cart once per approval.

        Refuses unless cart writes are explicitly enabled. Phase 1 ships with
        them off, so this path is exercised only by tests with a fake
        transport.
        """
        if not self.allow_cart_writes:
            raise ShopifyError(
                "live cart writes are disabled. Set allow_cart_writes to enable them.",
                retryable=False,
                code="cart_writes_disabled",
            )
        data = self.execute(CART_CREATE, {"lines": lines})
        result = data.get("cartCreate") or {}
        if result.get("userErrors"):
            messages = "; ".join(e.get("message", "") for e in result["userErrors"])
            raise ShopifyError(f"cartCreate failed: {messages}", retryable=False)
        cart = result.get("cart")
        if not cart:
            # Outcome unclear. The caller must read the cart back before any
            # retry rather than writing again.
            raise ShopifyError(
                "cartCreate returned no cart and no error; read the cart back before retrying",
                retryable=False,
                code="unclear_outcome",
            )
        return cart

    def add_cart_lines(self, cart_id: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
        """Add lines to an existing cart.

        Used only to complete a cart whose read-back showed missing lines.
        Never used to create a second cart: that is what would double-charge.
        """
        if not self.allow_cart_writes:
            raise ShopifyError(
                "live cart writes are disabled. Set allow_cart_writes to enable them.",
                retryable=False,
                code="cart_writes_disabled",
            )
        data = self.execute(CART_LINES_ADD, {"cartId": cart_id, "lines": lines})
        result = data.get("cartLinesAdd") or {}
        if result.get("userErrors"):
            messages = "; ".join(e.get("message", "") for e in result["userErrors"])
            raise ShopifyError(f"cartLinesAdd failed: {messages}", retryable=False)
        cart = result.get("cart")
        if not cart:
            raise ShopifyError(
                "cartLinesAdd returned no cart and no error; read the cart back "
                "before retrying",
                retryable=False,
                code="unclear_outcome",
            )
        return cart

    def get_cart(self, cart_id: str) -> Optional[dict[str, Any]]:
        """Read-only, and always allowed. This is how an unclear write is
        reconciled without mutating anything."""
        data = self.execute(CART_QUERY, {"id": cart_id})
        return data.get("cart")
