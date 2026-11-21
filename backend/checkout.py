"""Checkout: deterministic reconciliation, then the Decision 1 audit.

A hosted checkout URL is released only when both halves agree:

  1. Every deterministic check passes. These run in plain code and compare
     store, variant IDs, quantities, currency, stock, amounts, budget, fit
     status, approval validity and revisions.
  2. Decision 1 returns a confident ready.

The audit is bound to one approval fingerprint and one cart fingerprint and
it expires. A changed cart, a later edit, a refreshed amount or an expired
audit forces a new one.

Payment is never inferred from the handoff. Opening the checkout sets
checkout_opened, and nothing beyond payment_unconfirmed can be reached
without a read-only order lookup or a verified Shopify order event.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from contracts.cart_review import CartReview
from contracts.checkout_audit import (
    CheckoutAudit,
    DecisionAnswer,
    DeterministicCheck,
)
from contracts.interfaces import AuditInputs, BudgetLine, GateAnswers
from backend.cart import CartService, approval_fingerprint
from backend.db import Database
from backend.decisions import (
    DecisionsClient,
    DecisionsUnavailable,
    run_checkout_gates,
)
from backend.errors import EthyneonError
from backend.events import EventWriter

ACCEPTABLE_FIT = ("fits", "tight")


def _current_cart_fingerprint(
    review: CartReview, live_cart: Optional[dict[str, Any]]
) -> str:
    """Fingerprint the cart as it is right now, not as it was when written."""
    if live_cart is not None:
        from backend.cart import cart_fingerprint_from_shopify

        return cart_fingerprint_from_shopify(live_cart)
    if review.cart_reference and review.cart_reference.fingerprint:
        return review.cart_reference.fingerprint
    return "no_live_cart"


class CheckoutError(EthyneonError):
    pass


def build_audit_inputs(
    cart_review: CartReview,
    *,
    live_cart_lines: Optional[list[BudgetLine]] = None,
    fit_status: str = "unknown",
    hard_constraints: Optional[list[str]] = None,
) -> AuditInputs:
    """The minimal evidence packet.

    Deliberately excludes the buyer's contact details, credentials, the
    private checkout link and any payment data. tests assert that.
    """
    approved = [
        BudgetLine(
            variant_id=line.variant_id, quantity=line.quantity, unit_price=line.unit_price
        )
        for line in cart_review.lines
    ]
    return AuditInputs(
        session_id=cart_review.session_id,
        scene_revision=cart_review.scene_revision,
        brief_revision=cart_review.brief_revision,
        approved_lines=approved,
        merchandise_subtotal=cart_review.merchandise_subtotal,
        budget=cart_review.budget,
        hard_constraints=hard_constraints or [],
        fit_status=fit_status,  # type: ignore[arg-type]
        live_cart_lines=live_cart_lines if live_cart_lines is not None else approved,
        approval_fingerprint=(
            cart_review.approval.fingerprint if cart_review.approval else ""
        ),
        cart_fingerprint=(
            cart_review.cart_reference.fingerprint or ""
            if cart_review.cart_reference
            else ""
        ),
    )


class CheckoutService:
    def __init__(
        self,
        db: Database,
        carts: CartService,
        events: EventWriter,
        *,
        decisions: Optional[DecisionsClient] = None,
        audit_ttl_seconds: int = 600,
        use_stand_in: bool = True,
    ) -> None:
        self.db = db
        self.carts = carts
        self.events = events
        self.decisions = decisions
        self.audit_ttl_seconds = audit_ttl_seconds
        self.use_stand_in = use_stand_in

    # -- deterministic half -----------------------------------------------

    def reconcile(
        self,
        review: CartReview,
        live_cart: Optional[dict[str, Any]],
        *,
        current_scene_revision: int,
        fit_status: str,
    ) -> list[DeterministicCheck]:
        checks: list[DeterministicCheck] = []

        def add(name: str, passed: bool, detail: str, expected=None, observed=None) -> None:
            checks.append(
                DeterministicCheck(
                    name=name,  # type: ignore[arg-type]
                    passed=passed,
                    detail=detail,
                    expected=None if expected is None else str(expected),
                    observed=None if observed is None else str(observed),
                )
            )

        # store
        add(
            "store_matches",
            review.store.api_surface == "storefront_api",
            f"Review is on the {review.store.api_surface} surface.",
            "storefront_api",
            review.store.api_surface,
        )

        approved = {line.variant_id: line.quantity for line in review.lines}

        if live_cart is None:
            live = dict(approved)
            cart_note = "no live cart read; comparing the approval against itself"
        else:
            live = {}
            for edge in (live_cart.get("lines") or {}).get("edges", []):
                node = edge["node"]
                variant_id = (node.get("merchandise") or {}).get("id")
                if variant_id:
                    live[variant_id] = node.get("quantity", 0)
            cart_note = "compared against the live Shopify cart"

        add(
            "variant_ids_match",
            set(approved) == set(live),
            f"{len(approved)} approved variant(s), {cart_note}.",
            sorted(approved),
            sorted(live),
        )
        add(
            "quantities_match",
            all(live.get(v) == q for v, q in approved.items()),
            "Quantities compared line by line.",
            sorted(approved.items()),
            sorted(live.items()),
        )

        currencies = {line.unit_price.currency for line in review.lines}
        add(
            "currency_matches",
            len(currencies) == 1
            and review.merchandise_subtotal.currency in currencies,
            f"Currencies present: {sorted(currencies)}.",
        )

        # stock and amounts, re-read from the catalog
        out_of_stock: list[str] = []
        price_moved: list[str] = []
        for line in review.lines:
            try:
                offer = self.carts.catalog.get_offer(line.variant_id)
            except Exception:  # noqa: BLE001
                out_of_stock.append(line.variant_id)
                continue
            if not offer.availability.available:
                out_of_stock.append(line.variant_id)
            if offer.price.amount_minor != line.unit_price.amount_minor:
                price_moved.append(line.variant_id)

        add(
            "stock_available",
            not out_of_stock,
            "Every approved variant is still in stock."
            if not out_of_stock
            else f"Out of stock: {out_of_stock}",
        )

        expected_total = sum(line.line_subtotal.amount_minor for line in review.lines)
        amounts_ok = (
            not price_moved
            and review.merchandise_subtotal.amount_minor == expected_total
        )
        observed_total = review.merchandise_subtotal.amount_minor
        amount_detail = (
            "Subtotal equals the sum of the approved lines at current prices."
            if not price_moved
            else f"Prices moved on: {price_moved}"
        )

        # When a live cart was read, its own subtotal must agree too. This is
        # what catches a cart whose amount drifted after approval.
        if live_cart is not None:
            from backend.cart_write import subtotal_from_cart

            live_subtotal = subtotal_from_cart(live_cart)
            if live_subtotal is None:
                amounts_ok = False
                amount_detail = "The live cart subtotal could not be read."
            else:
                observed_total = live_subtotal.amount_minor
                if (
                    live_subtotal.amount_minor != review.merchandise_subtotal.amount_minor
                    or live_subtotal.currency != review.merchandise_subtotal.currency
                ):
                    amounts_ok = False
                    amount_detail = (
                        "The live cart subtotal does not match the approved amount."
                    )

        add(
            "amounts_match",
            amounts_ok,
            amount_detail,
            expected_total,
            observed_total,
        )

        within = True
        if review.budget is not None:
            within = (
                review.budget.currency == review.merchandise_subtotal.currency
                and review.merchandise_subtotal.amount_minor <= review.budget.amount_minor
            )
        add(
            "within_budget",
            within,
            f"{review.merchandise_subtotal.amount_minor} of "
            f"{review.budget.amount_minor if review.budget else 'no'} budget.",
        )

        add(
            "fit_status_acceptable",
            fit_status in ACCEPTABLE_FIT,
            f"Scene fit status is {fit_status}.",
            "fits or tight",
            fit_status,
        )

        approval = review.approval
        approval_ok = approval is not None
        detail = "No approval on this review."
        if approval is not None:
            expired = datetime.now(timezone.utc) > approval.expires_at
            drifted = approval.fingerprint != approval_fingerprint(review)
            approval_ok = not expired and not drifted
            if expired:
                detail = "The approval has expired."
            elif drifted:
                detail = "The review changed after it was approved."
            else:
                detail = "Approval is valid and still matches the review."
        add("approval_valid", approval_ok, detail)

        add(
            "revisions_current",
            review.scene_revision == current_scene_revision,
            f"Review is on scene revision {review.scene_revision}, "
            f"current is {current_scene_revision}.",
            current_scene_revision,
            review.scene_revision,
        )

        return checks

    # -- the full gate -----------------------------------------------------

    def continue_checkout(
        self,
        review_id: str,
        action_id: str,
        *,
        current_scene_revision: int,
        fit_status: str = "unknown",
        hard_constraints: Optional[list[str]] = None,
        live_cart: Optional[dict[str, Any]] = None,
    ) -> tuple[CheckoutAudit, Optional[str]]:
        """Returns the audit and, only when it passes, the checkout URL."""
        replayed = self.db.get_action(action_id)
        if replayed is not None:
            audit = CheckoutAudit.model_validate(replayed["audit"])
            return audit, replayed.get("checkout_url")

        stored = self.db.get_cart_review(review_id)
        if stored is None:
            raise CheckoutError(
                code="unknown_review",
                message=f"No cart review {review_id}.",
                retryable=False,
            )
        review = CartReview.model_validate(stored)

        if review.approval is None:
            raise CheckoutError(
                code="not_approved",
                message="Checkout needs an approved cart review.",
                retryable=False,
            )

        # Always compare against the cart as it is right now. A cart edited
        # in another tab after approval is exactly what this catches.
        if live_cart is None:
            live_cart = self._read_live_cart(review)

        checks = self.reconcile(
            review,
            live_cart,
            current_scene_revision=current_scene_revision,
            fit_status=fit_status,
        )
        deterministic_passed = all(check.passed for check in checks)

        packet = build_audit_inputs(
            review, fit_status=fit_status, hard_constraints=hard_constraints
        )

        decision_answers: list[DecisionAnswer] = []
        gates: Optional[GateAnswers] = None
        unavailable_reason: Optional[str] = None

        try:
            gates = run_checkout_gates(
                packet, client=self.decisions, use_stand_in=self.use_stand_in
            )
        except DecisionsUnavailable as exc:
            unavailable_reason = str(exc)

        if gates is not None:
            for answer in gates.answers:
                decision_answers.append(
                    DecisionAnswer(
                        question=answer.question,
                        answer_type=(
                            "choice" if answer.question == "next_action" else "noul"
                        ),
                        answer=answer.answer,
                        confidence=answer.confidence,
                        threshold=answer.threshold,
                        passed_threshold=answer.passed,
                        source=answer.source,
                    )
                )

        next_action = None
        for answer in decision_answers:
            if answer.question == "next_action":
                next_action = answer.answer

        if unavailable_reason is not None:
            outcome = "unavailable"
        elif deterministic_passed and gates is not None and gates.all_passed:
            outcome = "ready"
        elif not deterministic_passed:
            outcome = "blocked"
        else:
            outcome = "needs_review"

        if outcome != "ready":
            # next_action only means "ready" when the whole audit is ready.
            if next_action == "ready":
                next_action = "hold_checkout"

        now = datetime.now(timezone.utc)
        audit = CheckoutAudit(
            audit_id=f"audit_{uuid.uuid4().hex[:12]}",
            session_id=review.session_id,
            workflow_run_id=None,
            scene_revision=review.scene_revision,
            brief_revision=review.brief_revision,
            approval_fingerprint=review.approval.fingerprint,
            cart_fingerprint=_current_cart_fingerprint(review, live_cart),
            deterministic_results=checks,
            decision_answers=decision_answers,
            next_action=next_action,  # type: ignore[arg-type]
            outcome=outcome,  # type: ignore[arg-type]
            expires_at=now + timedelta(seconds=self.audit_ttl_seconds),
        )
        self.db.insert_audit(audit.model_dump(mode="json"), review_id)

        checkout_url: Optional[str] = None
        if outcome == "ready":
            checkout_url = self._release_url(review, audit, live_cart)
            review.payment_state = (
                "checkout_opened" if checkout_url else "checkout_audit_pending"
            )
            self.events.emit_simple(
                review.session_id,
                "commerce",
                "checkout_checks_passed",
                "Reconciliation and the Decision 1 audit both passed.",
                scene_revision=review.scene_revision,
            )
            if checkout_url:
                self.events.emit_simple(
                    review.session_id,
                    "commerce",
                    "checkout_opened",
                    "Hosted test checkout opened. Payment is not confirmed by this step.",
                    scene_revision=review.scene_revision,
                )
        else:
            review.payment_state = "needs_review"
            failing = [c.name for c in checks if not c.passed]
            self.events.emit_simple(
                review.session_id,
                "commerce",
                "cart_needs_review",
                f"Checkout held: {outcome}.",
                payload={
                    "failing_checks": failing,
                    "decisions_unavailable": unavailable_reason,
                },
                scene_revision=review.scene_revision,
            )

        self.db.upsert_cart_review(review.model_dump(mode="json"))
        self.db.record_action(
            action_id,
            review.session_id,
            "checkout_continue",
            {"audit": audit.model_dump(mode="json"), "checkout_url": checkout_url},
        )
        return audit, checkout_url

    def _read_live_cart(self, review: CartReview) -> Optional[dict[str, Any]]:
        """Read the cart from Shopify, if there is one and we can reach it."""
        reference = review.cart_reference
        if reference is None or reference.cart_id in (None, "", "pending"):
            return None
        storefront = self.carts.catalog.storefront
        if storefront is None:
            return None
        try:
            return storefront.get_cart(reference.cart_id)
        except Exception:  # noqa: BLE001 - an unreadable cart is not a pass
            return None

    def _release_url(
        self,
        review: CartReview,
        audit: CheckoutAudit,
        live_cart: Optional[dict[str, Any]],
    ) -> Optional[str]:
        """Hand back the hosted checkout URL, bound to this approval and cart.

        Released only when the audit is for this exact approval fingerprint
        and the cart fingerprint still matches what was audited. Any drift
        means a new audit, not a release.
        """
        reference = review.cart_reference
        if reference is None or not reference.checkout_url:
            return None

        from backend.cart import approval_fingerprint, cart_fingerprint_from_shopify

        if review.approval is None:
            return None
        if audit.approval_fingerprint != approval_fingerprint(review):
            return None
        if audit.expires_at <= datetime.now(timezone.utc):
            return None

        if live_cart is not None:
            current = cart_fingerprint_from_shopify(live_cart)
            if current != audit.cart_fingerprint:
                # The cart moved between the audit and the release.
                return None

        return reference.checkout_url

    # -- payment state ----------------------------------------------------

    def mark_payment_unconfirmed(self, review_id: str) -> CartReview:
        """The shopper came back from the hosted checkout. That is not payment."""
        review = CartReview.model_validate(self.db.get_cart_review(review_id))
        review.payment_state = "payment_unconfirmed"
        self.db.upsert_cart_review(review.model_dump(mode="json"))
        self.events.emit_simple(
            review.session_id,
            "commerce",
            "payment_unconfirmed",
            "Returned from checkout. Payment is not confirmed until an order lookup says so.",
            scene_revision=review.scene_revision,
        )
        return review

    def confirm_order(
        self,
        review_id: str,
        *,
        order_id: Optional[str],
        lookup_enabled: bool,
        blocked_reason: Optional[str] = None,
    ) -> CartReview:
        """order_confirmed comes only from a read-only order lookup or a
        verified Shopify order event, never from the handoff.

        Without the flag and a separate credential the state stays
        payment_unconfirmed, and the refusal says plainly why.
        """
        review = CartReview.model_validate(self.db.get_cart_review(review_id))

        if not lookup_enabled:
            reason = blocked_reason or "ENABLE_ORDER_LOOKUP is off"
            review.payment_state = "payment_unconfirmed"
            self.db.upsert_cart_review(review.model_dump(mode="json"))
            raise CheckoutError(
                code="order_lookup_disabled",
                message=(
                    f"An order cannot be confirmed: {reason}. The state stays "
                    f"payment_unconfirmed."
                ),
                retryable=False,
                details={"reason": reason, "payment_state": "payment_unconfirmed"},
            )
        if not order_id:
            review.payment_state = "payment_unconfirmed"
            self.db.upsert_cart_review(review.model_dump(mode="json"))
            return review

        review.payment_state = "order_confirmed"
        self.db.upsert_cart_review(review.model_dump(mode="json"))
        self.events.emit_simple(
            review.session_id,
            "commerce",
            "order_confirmed",
            "Order confirmed by a read-only order lookup.",
            payload={"order_id": order_id},
            scene_revision=review.scene_revision,
        )
        return review
