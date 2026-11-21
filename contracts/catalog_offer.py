"""CatalogOffer: a verified product variant. Owner C, used by A and B.

Shopify is the only product source. A product appears in a scene or cart only
if a verified CatalogOffer exists for its exact variant.

Two surfaces produce offers and they are not interchangeable:

  storefront_api  the connected development store. Shoppable. These are the
                  only offers that may ever reach a cart.
  shop_mcp        discovery across the public Shop catalog. Never shoppable.
                  Labelled on screen and blocked from the cart by construction.

Dimensions. The Shop MCP response carries no structured dimension field, so a
dimension parsed from an option label or listing text is marked
listing_text_inferred and caps any fit result at tentative. Missing dimensions
give a fit result of unknown.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field, model_validator

from contracts.common import (
    ApiSurface,
    DimensionSource,
    EthyneonModel,
    EvidenceStatus,
    Metres,
    Money,
    SourceLabel,
    utc_now,
)


class StoreRef(EthyneonModel):
    """Which store an offer came from. Kept on every offer so a cart line can
    never silently cross stores."""

    store_domain: str = Field(description="For example my-dev-store.myshopify.com")
    store_name: Optional[str] = None
    store_id: Optional[str] = Field(
        default=None, description="Full Shopify ID string, exactly as the API returns it."
    )
    api_surface: ApiSurface


class SelectedOption(EthyneonModel):
    """One resolved variant option, for example Color = Walnut."""

    name: str
    value: str


class Availability(EthyneonModel):
    available: bool
    quantity: Optional[int] = Field(default=None, ge=0)
    checked_at: datetime = Field(default_factory=utc_now)


class ProductDimensions(EthyneonModel):
    """Product footprint and height in metres. Any axis may be missing."""

    width_m: Optional[Metres] = None
    depth_m: Optional[Metres] = None
    height_m: Optional[Metres] = None
    evidence_status: EvidenceStatus = "unknown"
    raw_text: Optional[str] = Field(
        default=None,
        description="The listing text a value was parsed from, kept for the audit trail.",
    )

    @property
    def has_footprint(self) -> bool:
        """True only when both floor axes are known. The fit engine needs both."""
        return self.width_m is not None and self.depth_m is not None


class Freshness(EthyneonModel):
    """When the offer was read and how long it may be reused.

    Shop MCP advertises its own ttl on tools/list (currently 3600000 ms); that
    value is carried here rather than hardcoded, and a reused offer is shown
    with a cached label and its last-updated time.
    """

    fetched_at: datetime = Field(default_factory=utc_now)
    ttl_ms: Optional[int] = Field(default=None, ge=0)
    source_label: SourceLabel = "live"


class CatalogOffer(EthyneonModel):
    offer_id: str
    store: StoreRef
    product_id: str = Field(
        description="Full Shopify product ID string. Separate from variant_id."
    )
    variant_id: str = Field(
        description="Full Shopify variant ID string. Separate from product_id."
    )
    title: str
    selected_options: list[SelectedOption] = Field(default_factory=list)
    price: Money
    compare_at_price: Optional[Money] = None
    availability: Availability
    listing_url: str
    image_url: Optional[str] = None
    dimensions: Optional[ProductDimensions] = None
    dimension_source: DimensionSource = "unknown"
    freshness: Freshness = Field(default_factory=Freshness)
    shoppable: bool = Field(
        default=False,
        description=(
            "True only for a storefront_api offer from the configured dev store. "
            "A shop_mcp offer is discovery only and can never reach a cart."
        ),
    )

    @model_validator(mode="after")
    def _discovery_offers_are_never_shoppable(self) -> "CatalogOffer":
        if self.shoppable and self.store.api_surface == "shop_mcp":
            raise ValueError(
                "a shop_mcp offer can never be shoppable: discovery surface only"
            )
        return self

    @model_validator(mode="after")
    def _dimension_source_matches_dimensions(self) -> "CatalogOffer":
        has_any = self.dimensions is not None and any(
            v is not None
            for v in (
                self.dimensions.width_m,
                self.dimensions.depth_m,
                self.dimensions.height_m,
            )
        )
        if self.dimension_source == "unknown" and has_any:
            raise ValueError(
                "dimension_source is unknown but dimensions are present; "
                "use metafield or listing_text_inferred"
            )
        if self.dimension_source != "unknown" and not has_any:
            raise ValueError(
                f"dimension_source is {self.dimension_source} but no dimensions are set"
            )
        return self

    @property
    def fit_is_tentative(self) -> bool:
        """Dimensions read out of listing text never support a firm fit claim."""
        return self.dimension_source == "listing_text_inferred"
