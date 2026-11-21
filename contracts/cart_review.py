"""CartReview: the itemised list the shopper explicitly approves. Owner C.

Money is integer minor units throughout. The review states that shipping and
tax are added at checkout, because the budget caps the merchandise subtotal.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field, model_validator

from contracts.common import (
    EthyneonModel,
    Money,
    PaymentState,
    Revision,
    SourceLabel,
    utc_now,
)
from contracts.catalog_offer import StoreRef

CartReviewStatus = Literal[
    "draft",
    "awaiting_confirmation",
    "approved",
    "superseded",
    "voided",
]


class CartLine(EthyneonModel):
    """One approved line. The variant is exact and comes from a verified,
    shoppable CatalogOffer on the configured store."""

    line_id: str
    product_id: str
    variant_id: str
    title: str
    quantity: int = Field(ge=1)
    unit_price: Money
    line_subtotal: Money
    offer_ref: str = Field(description="offer_id of the verified CatalogOffer.")
    offer_fetched_at: datetime = Field(
        description="Freshness of the offer this line was priced from."
    )

    @model_validator(mode="after")
    def _subtotal_is_exact(self) -> "CartLine":
        if self.unit_price.currency != self.line_subtotal.currency:
            raise ValueError("unit_price and line_subtotal currencies differ")
        expected = self.unit_price.amount_minor * self.quantity
        if self.line_subtotal.amount_minor != expected:
            raise ValueError(
                f"line_subtotal {self.line_subtotal.amount_minor} is not "
                f"unit_price x quantity ({expected})"
            )
        return self


class KnownCharge(EthyneonModel):
    """A charge the store already told us about. Unknown charges are not
    guessed; they are named as resolved at checkout."""

    label: str
    amount: Money


class ApprovalRecord(EthyneonModel):
    """Binds an approval to the shopper, the exact lines, the amounts and the
    revisions it was made against. Any drift voids it."""

    approval_id: str
    shopper_id: str
    approved_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    fingerprint: str = Field(
        description="Deterministic hash over store, lines, quantities, amounts and revisions."
    )
    base_scene_revision: Revision
    base_brief_revision: Revision
    action_id: str = Field(description="Client-made UUID for this approval.")


class CartRef(EthyneonModel):
    """The Shopify cart written for one approval. Written once per approval."""

    cart_id: str
    store_domain: str
    checkout_url: Optional[str] = Field(
        default=None,
        description="Hosted checkout URL. Released only after reconciliation and a passing audit.",
    )
    fingerprint: Optional[str] = Field(
        default=None, description="Hash over the cart lines and totals as Shopify returned them."
    )
    written_at: Optional[datetime] = None
    write_outcome: Literal["unknown", "confirmed", "failed"] = "unknown"


class CartReview(EthyneonModel):
    review_id: str
    session_id: str
    shopper_id: str
    store: StoreRef
    lines: list[CartLine] = Field(min_length=1)
    merchandise_subtotal: Money
    known_charges: list[KnownCharge] = Field(default_factory=list)
    shipping_and_tax_note: str = Field(
        default="Shipping and tax are added at checkout and are not included in this subtotal.",
    )
    budget: Optional[Money] = None
    within_budget: Optional[bool] = None
    brief_revision: Revision
    scene_revision: Revision
    approval: Optional[ApprovalRecord] = None
    cart_reference: Optional[CartRef] = None
    status: CartReviewStatus = "draft"
    payment_state: PaymentState = "cart_ready"
    source: SourceLabel = "live"
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _subtotal_matches_lines(self) -> "CartReview":
        currencies = {line.unit_price.currency for line in self.lines}
        if len(currencies) > 1:
            raise ValueError(f"cart lines mix currencies: {sorted(currencies)}")
        currency = currencies.pop()
        if self.merchandise_subtotal.currency != currency:
            raise ValueError("merchandise_subtotal currency does not match the lines")
        expected = sum(line.line_subtotal.amount_minor for line in self.lines)
        if self.merchandise_subtotal.amount_minor != expected:
            raise ValueError(
                f"merchandise_subtotal {self.merchandise_subtotal.amount_minor} "
                f"is not the sum of the lines ({expected})"
            )
        return self

    @model_validator(mode="after")
    def _one_store_only(self) -> "CartReview":
        if self.cart_reference and self.cart_reference.store_domain != self.store.store_domain:
            raise ValueError("cart_reference points at a different store than the review")
        if self.store.api_surface == "shop_mcp":
            raise ValueError(
                "a cart review can never be built on the shop_mcp discovery surface"
            )
        return self
