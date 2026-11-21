"""search_catalog and get_offer, across three surfaces.

  storefront_api  the configured development store. Shoppable.
  shop_mcp        public discovery across other merchants. Never shoppable.
  mock            the fixture catalog, used when MOCK_COMMERCE is on.

Two rules hold on every path:

  Only a storefront_api offer from the configured store is shoppable. The
  CatalogOffer model refuses anything else, so this is enforced twice.

  A cached result is labelled cached and carries the time it was read. Shop
  MCP advertises its own ttl on tools/list and that value is used rather than
  a number invented here.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from contracts.catalog_offer import (
    Availability,
    CatalogOffer,
    Freshness,
    SelectedOption,
    StoreRef,
)
from contracts.common import Money
from contracts.interfaces import SearchRequirements
from backend.config import Settings
from backend.db import Database
from backend.dimensions import extract_dimensions
from backend.sanitise import clean_title
from backend.shop_mcp import FALLBACK_CACHE_TTL_MS, ShopMcpClient, ShopMcpError
from backend.shopify import ShopifyError, ShopifyStorefront, is_sample_product

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_CATALOG = REPO_ROOT / "fixtures" / "catalog_offers.sample.json"


class CatalogService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        *,
        storefront: Optional[ShopifyStorefront] = None,
        shop_mcp: Optional[ShopMcpClient] = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self._storefront = storefront
        self._shop_mcp = shop_mcp
        self.last_discovery_error: Optional[str] = None

    # -- surfaces ---------------------------------------------------------

    @property
    def storefront(self) -> Optional[ShopifyStorefront]:
        if self._storefront is not None:
            return self._storefront
        if self.settings.mock_commerce:
            return None
        if not (self.settings.shopify_store_domain and self.settings.shopify_storefront_token):
            return None
        self._storefront = ShopifyStorefront(
            self.settings.shopify_store_domain,
            self.settings.shopify_storefront_token,
            self.settings.shopify_api_version,
            # Derived from the same two gates that govern policy, so the
            # client and the policy can never disagree. Hardcoding this to
            # False once meant both gates could open while the client stayed
            # shut, and the live write died with a misleading message.
            # It is re-checked again at write time by check_cart_writes_allowed.
            allow_cart_writes=self.settings.cart_writes_allowed,
        )
        return self._storefront

    @property
    def shop_mcp(self) -> Optional[ShopMcpClient]:
        if not self.settings.shop_mcp:
            return None
        if self._shop_mcp is None:
            self._shop_mcp = ShopMcpClient(
                endpoint=self.settings.shop_mcp_endpoint,
                protocol_version=self.settings.shop_mcp_protocol_version,
                timeout_seconds=self.settings.shop_mcp_timeout_seconds,
            )
        return self._shop_mcp

    # -- the mock catalog -------------------------------------------------

    def _fixture_offers(self) -> list[CatalogOffer]:
        raw = json.loads(FIXTURE_CATALOG.read_text(encoding="utf-8"))
        offers: list[CatalogOffer] = []
        for item in raw:
            offer = CatalogOffer.model_validate(item)
            # A mock must never look live.
            offer.freshness = Freshness(
                fetched_at=offer.freshness.fetched_at,
                ttl_ms=offer.freshness.ttl_ms,
                source_label="mock",
            )
            offers.append(offer)
        return offers

    # -- cache ------------------------------------------------------------

    def _cache_key(self, surface: str, key: str) -> str:
        return f"{surface}:{key}"

    def _cached(self, surface: str, key: str) -> Optional[list[CatalogOffer]]:
        entry = self.db.cache_get(self._cache_key(surface, key))
        if entry is None:
            return None
        payload, fetched_at_iso, ttl_ms = entry
        ttl = self.settings.shop_mcp_cache_ttl_ms_override or ttl_ms
        if ttl is None:
            return None
        fetched_at = datetime.fromisoformat(fetched_at_iso)
        if datetime.now(timezone.utc) - fetched_at > timedelta(milliseconds=ttl):
            return None

        offers: list[CatalogOffer] = []
        for item in payload:
            offer = CatalogOffer.model_validate(item)
            # Relabel as cached, with the time it was actually read.
            offer.freshness = Freshness(
                fetched_at=fetched_at, ttl_ms=ttl, source_label="cached"
            )
            offers.append(offer)
        return offers

    def _store_cache(
        self, surface: str, key: str, offers: list[CatalogOffer], ttl_ms: Optional[int]
    ) -> None:
        self.db.cache_put(
            self._cache_key(surface, key),
            [o.model_dump(mode="json") for o in offers],
            ttl_ms,
            surface,
        )

    # -- search -----------------------------------------------------------

    def search_catalog(
        self, requirements: SearchRequirements, limit: int = 10
    ) -> list[CatalogOffer]:
        """Shoppable offers first, then labelled discovery results."""
        offers: list[CatalogOffer] = []

        if requirements.surface in ("any", "storefront_api"):
            offers.extend(self._search_shoppable(requirements, limit))

        if requirements.surface in ("any", "shop_mcp"):
            offers.extend(self._search_discovery(requirements, limit))

        return self._filter(offers, requirements)[:limit] if requirements.surface != "any" else (
            self._filter(offers, requirements)
        )

    def _search_shoppable(
        self, requirements: SearchRequirements, limit: int
    ) -> list[CatalogOffer]:
        if self.settings.mock_commerce or self.storefront is None:
            return [
                o
                for o in self._fixture_offers()
                if o.store.api_surface == "storefront_api"
            ]
        try:
            return self.storefront.search_products(
                first=max(limit, 20), query=requirements.query
            )
        except ShopifyError as exc:
            self.last_discovery_error = f"Storefront unavailable: {exc}"
            # A cached listing is read-only during an outage, and labelled.
            cached = self._cached("storefront_api", requirements.query or "*")
            return cached or []

    def _search_discovery(
        self, requirements: SearchRequirements, limit: int
    ) -> list[CatalogOffer]:
        if self.settings.mock_commerce or self.shop_mcp is None:
            return [o for o in self._fixture_offers() if o.store.api_surface == "shop_mcp"]

        key = requirements.query or "*"
        cached = self._cached("shop_mcp", key)
        if cached is not None:
            return cached

        try:
            result = self.shop_mcp.search_catalog(
                requirements.query or "furniture", limit=min(max(limit, 1), 50)
            )
        except ShopMcpError as exc:
            self.last_discovery_error = f"Shop MCP unavailable: {exc}"
            return []

        offers = normalise_shop_mcp(result.structured_content)
        ttl = result.ttl_ms or FALLBACK_CACHE_TTL_MS
        self._store_cache("shop_mcp", key, offers, ttl)
        return offers

    @staticmethod
    def _filter(
        offers: list[CatalogOffer], requirements: SearchRequirements
    ) -> list[CatalogOffer]:
        kept: list[CatalogOffer] = []
        for offer in offers:
            if requirements.require_available and not offer.availability.available:
                continue
            if requirements.require_dimensions and (
                offer.dimensions is None or not offer.dimensions.has_footprint
            ):
                continue
            if requirements.max_price and (
                offer.price.currency != requirements.max_price.currency
                or offer.price.amount_minor > requirements.max_price.amount_minor
            ):
                continue
            if requirements.min_price and (
                offer.price.currency != requirements.min_price.currency
                or offer.price.amount_minor < requirements.min_price.amount_minor
            ):
                continue
            if requirements.max_width_m is not None:
                width = offer.dimensions.width_m if offer.dimensions else None
                if width is None or width > requirements.max_width_m:
                    continue
            if requirements.max_depth_m is not None:
                depth = offer.dimensions.depth_m if offer.dimensions else None
                if depth is None or depth > requirements.max_depth_m:
                    continue
            if requirements.query:
                needle = requirements.query.lower()
                if needle not in offer.title.lower():
                    # Only filter locally for the mock and cache paths; the
                    # live APIs already applied the query.
                    if offer.freshness.source_label in ("mock", "cached"):
                        continue
            kept.append(offer)
        return kept

    # -- single offer -----------------------------------------------------

    def get_offer(self, variant_id: str) -> CatalogOffer:
        """A fresh read of one variant, never from cache.

        Only the configured store can answer this, because only a storefront
        offer can ever be bought.
        """
        if self.settings.mock_commerce or self.storefront is None:
            for offer in self._fixture_offers():
                if offer.variant_id == variant_id:
                    return offer
            raise ShopifyError(f"variant {variant_id} is not in the mock catalog", retryable=False)
        return self.storefront.get_variant(variant_id)


# --------------------------------------------------------------------------
# Shop MCP normalisation
# --------------------------------------------------------------------------


def normalise_shop_mcp(structured_content: dict[str, Any]) -> list[CatalogOffer]:
    """Turn a Shop MCP search result into CatalogOffers.

    Everything produced here is `shoppable=False`. The CatalogOffer model
    refuses a shoppable discovery offer outright, so this cannot drift.

    Product text is stripped before it is stored, and the raw description is
    never carried on the offer. Dimensions, where they can be found at all,
    come from a plain-code extractor reading the option label and the sales
    copy, and are labelled listing_text_inferred.
    """
    offers: list[CatalogOffer] = []

    for product in structured_content.get("products", []) or []:
        description = (product.get("description") or {}).get("plain")
        product_title = product.get("title", "")

        for variant in product.get("variants", []) or []:
            seller = variant.get("seller") or {}
            price = variant.get("price") or {}
            if not variant.get("id") or "amount" not in price:
                continue

            option_text = " ".join(
                f"{o.get('name', '')} {o.get('label', '')}"
                for o in variant.get("options", []) or []
            )
            # Option labels first: they are far more reliable than sales copy.
            dimensions = extract_dimensions(option_text, description)

            offers.append(
                CatalogOffer(
                    offer_id=f"offer_mcp_{str(variant['id']).rsplit('/', 1)[-1]}",
                    store=StoreRef(
                        store_domain=seller.get("domain", "unknown"),
                        store_name=clean_title(seller.get("name", "")) or None,
                        store_id=seller.get("id"),
                        api_surface="shop_mcp",
                    ),
                    product_id=variant.get("seller_product_id") or product.get("id", ""),
                    variant_id=variant["id"],
                    title=clean_title(variant.get("title") or product_title),
                    selected_options=[
                        SelectedOption(
                            name=clean_title(o.get("name", "")),
                            value=clean_title(o.get("label", "")),
                        )
                        for o in (variant.get("options") or [])
                        if o.get("name")
                    ],
                    price=Money(
                        amount_minor=int(price["amount"]),
                        currency=price.get("currency", "USD"),
                    ),
                    availability=Availability(
                        available=bool((variant.get("availability") or {}).get("available")),
                        checked_at=datetime.now(timezone.utc),
                    ),
                    listing_url=variant.get("url") or "",
                    image_url=_first_image(product),
                    dimensions=dimensions,
                    dimension_source=(
                        "listing_text_inferred" if dimensions else "unknown"
                    ),
                    freshness=Freshness(
                        fetched_at=datetime.now(timezone.utc),
                        ttl_ms=FALLBACK_CACHE_TTL_MS,
                        source_label="live",
                    ),
                    # Never shoppable. These belong to other merchants.
                    shoppable=False,
                )
            )

    return offers


def _first_image(product: dict[str, Any]) -> Optional[str]:
    for media in product.get("media", []) or []:
        if media.get("type") == "image" and media.get("url"):
            return media["url"]
    return None
