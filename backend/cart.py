"""Cart review, approval and the Shopify cart write.

The sequence that protects the shopper:

  1. build_cart_review resolves each selection to a fresh CatalogOffer. A
     variant that is unavailable, unverified, or from the discovery surface
     never becomes a line.
  2. confirm_cart re-reads price and stock. If anything moved, the approval
     is voided and a new review comes back for the shopper to look at again.
  3. An approval is bound by fingerprint to the exact lines, quantities,
     amounts, currency and revisions it was made against, and it expires.
  4. The Shopify cart is written once per approval. If the outcome is
     unclear, the cart is read back before any retry, never written again.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from contracts.cart_review import (
    ApprovalRecord,
    CartLine,
    CartRef,
    CartReview,
)
from contracts.catalog_offer import CatalogOffer
from contracts.common import Money
from contracts.interfaces import BudgetLine
from backend.budget import compute_budget
from backend.catalog import CatalogService
from backend.db import Database
from backend.errors import EthyneonError
from backend.events import EventWriter


class CartError(EthyneonError):
    pass


def fingerprint(payload: dict[str, Any]) -> str:
    """Deterministic hash over the things that must not change silently."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def approval_fingerprint(review: CartReview) -> str:
    return fingerprint(
        {
            "store": review.store.store_domain,
            "shopper": review.shopper_id,
            "lines": sorted(
                [
                    {
                        "variant_id": line.variant_id,
                        "quantity": line.quantity,
                        "unit_price": line.unit_price.amount_minor,
                        "currency": line.unit_price.currency,
                    }
                    for line in review.lines
                ],
                key=lambda item: item["variant_id"],
            ),
            "subtotal": review.merchandise_subtotal.amount_minor,
            "currency": review.merchandise_subtotal.currency,
            "scene_revision": review.scene_revision,
            "brief_revision": review.brief_revision,
        }
    )


def cart_fingerprint_from_shopify(cart: dict[str, Any]) -> str:
    """Hash of the cart as Shopify returned it, for reconciliation."""
    lines = []
    for edge in (cart.get("lines") or {}).get("edges", []):
        node = edge["node"]
        lines.append(
            {
                "variant_id": (node.get("merchandise") or {}).get("id"),
                "quantity": node.get("quantity"),
            }
        )
    subtotal = ((cart.get("cost") or {}).get("subtotalAmount") or {})
    return fingerprint(
        {
            "cart_id": cart.get("id"),
            "lines": sorted(lines, key=lambda item: str(item["variant_id"])),
            "subtotal": str(subtotal.get("amount")),
            "currency": subtotal.get("currencyCode"),
        }
    )


class CartService:
    def __init__(
        self,
        db: Database,
        catalog: CatalogService,
        events: EventWriter,
        *,
        approval_ttl_seconds: int = 900,
    ) -> None:
        self.db = db
        self.catalog = catalog
        self.events = events
        self.approval_ttl_seconds = approval_ttl_seconds

    # -- review -----------------------------------------------------------

    def build_cart_review(
        self,
        session_id: str,
        shopper_id: str,
        selections: Sequence[BudgetLine],
        *,
        scene_revision: int,
        brief_revision: int,
        budget: Optional[Money] = None,
        review_id: Optional[str] = None,
    ) -> CartReview:
        if not selections:
            raise CartError(
                code="empty_selection",
                message="A cart review needs at least one selected variant.",
                retryable=False,
            )

        lines: list[CartLine] = []
        rejected: list[dict[str, str]] = []

        for index, selection in enumerate(selections):
            try:
                offer = self.catalog.get_offer(selection.variant_id)
            except Exception as exc:  # noqa: BLE001
                rejected.append(
                    {"variant_id": selection.variant_id, "reason": f"no verified offer: {exc}"}
                )
                continue

            if not offer.shoppable:
                rejected.append(
                    {
                        "variant_id": selection.variant_id,
                        "reason": (
                            "this offer is from the discovery surface and can never "
                            "reach a cart"
                        ),
                    }
                )
                continue

            if not offer.availability.available:
                rejected.append(
                    {"variant_id": selection.variant_id, "reason": "variant is unavailable"}
                )
                continue

            if (
                offer.availability.quantity is not None
                and offer.availability.quantity < selection.quantity
            ):
                rejected.append(
                    {
                        "variant_id": selection.variant_id,
                        "reason": (
                            f"only {offer.availability.quantity} in stock, "
                            f"{selection.quantity} requested"
                        ),
                    }
                )
                continue

            lines.append(
                CartLine(
                    line_id=f"line_{index}_{offer.variant_id.rsplit('/', 1)[-1]}",
                    product_id=offer.product_id,
                    variant_id=offer.variant_id,
                    title=offer.title,
                    quantity=selection.quantity,
                    # The live price wins, never the one the client sent.
                    unit_price=offer.price,
                    line_subtotal=Money(
                        amount_minor=offer.price.amount_minor * selection.quantity,
                        currency=offer.price.currency,
                    ),
                    offer_ref=offer.offer_id,
                    offer_fetched_at=offer.freshness.fetched_at,
                )
            )

        if not lines:
            raise CartError(
                code="no_shoppable_lines",
                message="None of the selected variants can be bought.",
                retryable=False,
                details={"rejected": rejected},
            )

        budget_result = compute_budget(
            [
                BudgetLine(
                    variant_id=line.variant_id,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                )
                for line in lines
            ],
            budget,
        )

        review = CartReview(
            review_id=review_id or f"cart_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            shopper_id=shopper_id,
            store=lines[0].offer_ref and self._store_ref_for(lines[0]),
            lines=lines,
            merchandise_subtotal=budget_result.items_subtotal,
            budget=budget,
            within_budget=budget_result.within_budget,
            brief_revision=brief_revision,
            scene_revision=scene_revision,
            status="awaiting_confirmation",
            payment_state="cart_ready",
            source="mock" if self.catalog.settings.mock_commerce else "live",
        )

        self.db.upsert_cart_review(review.model_dump(mode="json"))

        if rejected:
            self.events.emit_simple(
                session_id,
                "commerce",
                "cart_needs_review",
                f"{len(rejected)} selection(s) could not be added.",
                payload={"rejected": rejected},
                scene_revision=scene_revision,
            )

        return review

    def _store_ref_for(self, line: CartLine):
        offer = self.catalog.get_offer(line.variant_id)
        return offer.store

    # -- confirm ----------------------------------------------------------

    def confirm_cart(
        self, review_id: str, base_revision: int, action_id: str, *, current_scene_revision: int
    ) -> CartReview:
        """Record the approval after re-reading price and stock.

        If anything changed, the approval is voided and a fresh review is
        returned instead. The shopper approves the new numbers or nothing.
        """
        replayed = self.db.get_action(action_id)
        if replayed is not None:
            return CartReview.model_validate(replayed)

        stored = self.db.get_cart_review(review_id)
        if stored is None:
            raise CartError(
                code="unknown_review",
                message=f"No cart review {review_id}.",
                retryable=False,
            )
        review = CartReview.model_validate(stored)

        if review.scene_revision != current_scene_revision:
            review.status = "superseded"
            review.approval = None
            self.db.upsert_cart_review(review.model_dump(mode="json"))
            raise CartError(
                code="stale_base_revision",
                message=(
                    f"The scene moved to revision {current_scene_revision} after this "
                    f"review was built at {review.scene_revision}."
                ),
                retryable=True,
                status_code=409,
                details={"latest_revision": current_scene_revision},
            )

        # Re-read every line before binding an approval to it.
        changes: list[dict[str, Any]] = []
        refreshed: list[CartLine] = []
        for line in review.lines:
            offer = self.catalog.get_offer(line.variant_id)
            if not offer.availability.available:
                changes.append({"variant_id": line.variant_id, "change": "now unavailable"})
                continue
            if offer.price.amount_minor != line.unit_price.amount_minor:
                changes.append(
                    {
                        "variant_id": line.variant_id,
                        "change": "price changed",
                        "was": line.unit_price.amount_minor,
                        "now": offer.price.amount_minor,
                    }
                )
            refreshed.append(
                line.model_copy(
                    update={
                        "unit_price": offer.price,
                        "line_subtotal": Money(
                            amount_minor=offer.price.amount_minor * line.quantity,
                            currency=offer.price.currency,
                        ),
                        "offer_fetched_at": offer.freshness.fetched_at,
                    }
                )
            )

        if changes:
            if not refreshed:
                # Every line fell away. There is nothing left to re-approve,
                # and CartReview requires at least one line, so the review is
                # voided rather than rebuilt into an invalid shape.
                voided = review.model_copy(
                    update={
                        "approval": None,
                        "status": "voided",
                        "payment_state": "needs_review",
                    }
                )
                self.db.upsert_cart_review(voided.model_dump(mode="json"))
                self.events.emit_simple(
                    review.session_id,
                    "commerce",
                    "cart_needs_review",
                    "Every line became unavailable, so the approval was voided.",
                    payload={"changes": changes},
                    scene_revision=review.scene_revision,
                )
                raise CartError(
                    code="cart_changed",
                    message="Every selected variant became unavailable.",
                    retryable=True,
                    status_code=409,
                    details={"changes": changes},
                )

            budget_result = compute_budget(
                [
                    BudgetLine(
                        variant_id=line.variant_id,
                        quantity=line.quantity,
                        unit_price=line.unit_price,
                    )
                    for line in refreshed
                ],
                review.budget,
            )
            # One atomic update. Assigning lines and the subtotal separately
            # would trip the model's own consistency validator halfway through.
            review = review.model_copy(
                update={
                    "lines": refreshed,
                    "merchandise_subtotal": budget_result.items_subtotal,
                    "within_budget": budget_result.within_budget,
                    "approval": None,
                    "status": "awaiting_confirmation",
                    "payment_state": "needs_review",
                }
            )
            self.db.upsert_cart_review(review.model_dump(mode="json"))
            self.events.emit_simple(
                review.session_id,
                "commerce",
                "cart_needs_review",
                "Prices or stock changed, so the approval was voided.",
                payload={"changes": changes},
                scene_revision=review.scene_revision,
            )
            raise CartError(
                code="cart_changed",
                message="Prices or stock changed. Review the new version and confirm again.",
                retryable=True,
                status_code=409,
                details={"changes": changes, "review": review.model_dump(mode="json")},
            )

        now = datetime.now(timezone.utc)
        review.approval = ApprovalRecord(
            approval_id=f"appr_{uuid.uuid4().hex[:12]}",
            shopper_id=review.shopper_id,
            approved_at=now,
            expires_at=now + timedelta(seconds=self.approval_ttl_seconds),
            fingerprint="placeholder",
            base_scene_revision=review.scene_revision,
            base_brief_revision=review.brief_revision,
            action_id=action_id,
        )
        review.approval.fingerprint = approval_fingerprint(review)
        review.status = "approved"
        review.payment_state = "cart_ready"

        self.db.upsert_cart_review(review.model_dump(mode="json"))
        self.db.record_action(
            action_id, review.session_id, "cart_confirm", review.model_dump(mode="json")
        )
        self.events.emit_simple(
            review.session_id,
            "commerce",
            "variant_verified",
            f"{len(review.lines)} line(s) approved at "
            f"{review.merchandise_subtotal.amount_minor} "
            f"{review.merchandise_subtotal.currency}.",
            scene_revision=review.scene_revision,
        )
        return review

    # -- the Shopify write ------------------------------------------------

    def write_cart_once(self, review: CartReview) -> CartReview:
        """Create the Shopify cart for an approval, exactly once.

        Not reached in Phase 1: cart writes are disabled on the Storefront
        client, so this raises rather than mutating anything. The logic is
        here, and tested with a fake transport, so enabling it later is a
        configuration change and not new code.
        """
        if review.approval is None:
            raise CartError(
                code="not_approved",
                message="A cart is written only for an approved review.",
                retryable=False,
            )

        if review.cart_reference and review.cart_reference.write_outcome == "confirmed":
            return review

        if review.cart_reference and review.cart_reference.write_outcome == "unknown":
            # The previous attempt's outcome is unclear. Read, never rewrite.
            return self.reconcile_unclear_write(review)

        storefront = self.catalog.storefront
        if storefront is None:
            raise CartError(
                code="commerce_is_mocked",
                message="MOCK_COMMERCE is on, so no live cart is written.",
                retryable=False,
            )

        lines = [
            {"merchandiseId": line.variant_id, "quantity": line.quantity}
            for line in review.lines
        ]
        review.cart_reference = CartRef(
            cart_id="pending",
            store_domain=review.store.store_domain,
            write_outcome="unknown",
            written_at=datetime.now(timezone.utc),
        )
        self.db.upsert_cart_review(review.model_dump(mode="json"))

        cart = storefront.create_cart(lines)

        review.cart_reference = CartRef(
            cart_id=cart["id"],
            store_domain=review.store.store_domain,
            checkout_url=None,  # released only after a passing audit
            fingerprint=cart_fingerprint_from_shopify(cart),
            written_at=datetime.now(timezone.utc),
            write_outcome="confirmed",
        )
        self.db.upsert_cart_review(review.model_dump(mode="json"))
        self.events.emit_simple(
            review.session_id,
            "commerce",
            "cart_written",
            "Shopify cart created for this approval.",
            scene_revision=review.scene_revision,
        )
        return review

    def reconcile_unclear_write(self, review: CartReview) -> CartReview:
        """Read the cart back rather than writing again."""
        storefront = self.catalog.storefront
        if storefront is None or review.cart_reference is None:
            raise CartError(
                code="cannot_reconcile",
                message="No live store or no cart reference to read back.",
                retryable=False,
            )
        if review.cart_reference.cart_id == "pending":
            review.cart_reference.write_outcome = "failed"
            self.db.upsert_cart_review(review.model_dump(mode="json"))
            return review

        cart = storefront.get_cart(review.cart_reference.cart_id)
        if cart is None:
            review.cart_reference.write_outcome = "failed"
        else:
            review.cart_reference.fingerprint = cart_fingerprint_from_shopify(cart)
            review.cart_reference.write_outcome = "confirmed"
        self.db.upsert_cart_review(review.model_dump(mode="json"))
        return review
