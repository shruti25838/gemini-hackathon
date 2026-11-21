"""Writing the Shopify cart for an approval, exactly once.

This is the only module in the project that mutates anything on a real store,
so the rules are written out rather than implied.

The guard. A live write needs two independent conditions, checked together at
the moment of the write and not merely at startup:

    LIVE_CART_WRITES=1
    SHOPIFY_STORE_DOMAIN is on ALLOWED_CART_STORE_DOMAINS

Either one off means refusal with the standard error envelope. The default is
off, so a fresh clone cannot write to anything.

Write once. A cart is created once per approval. If a previous attempt left an
unclear outcome, the cart is **read back**, never written again: a second
cartCreate is how a shopper ends up with two carts, and a second
cartLinesAdd is how they end up with double quantities.

Verify before trusting. After the write the cart is read back from Shopify and
compared against the approval, line by line: exact variant IDs, quantities,
currency, and the subtotal in integer minor units. A cart that does not match
is not marked ready.

Nothing here logs a checkout URL. The URL is stored server side and released
only by the checkout gate, after a passing audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from contracts.cart_review import CartRef, CartReview
from contracts.common import Money
from backend.errors import EthyneonError
from backend.shopify import ShopifyError, ShopifyStorefront, money_from_shopify


class CartWriteRefused(EthyneonError):
    """The guard said no. Carries the reason, never a credential."""

    def __init__(self, reason: str, *, store_domain: Optional[str] = None) -> None:
        super().__init__(
            code="live_cart_writes_refused",
            message=f"Live cart writes are not permitted: {reason}.",
            retryable=False,
            status_code=403,
            details={"reason": reason, "store_domain": store_domain},
        )


def check_cart_writes_allowed(settings) -> None:
    """Raise unless both gates are open. Call immediately before any write."""
    reason = settings.cart_write_refusal_reason()
    if reason is not None:
        raise CartWriteRefused(reason, store_domain=settings.shopify_store_domain)


@dataclass
class Reconciliation:
    """What the read-back found. Safe to print: no URL, no credential."""

    matches: bool
    checks: dict[str, bool] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    observed_lines: dict[str, int] = field(default_factory=dict)
    expected_lines: dict[str, int] = field(default_factory=dict)
    observed_subtotal: Optional[Money] = None
    expected_subtotal: Optional[Money] = None

    def summary(self) -> str:
        if self.matches:
            return "cart matches the approval"
        return "cart does not match the approval: " + "; ".join(self.problems)


def lines_from_cart(cart: dict[str, Any]) -> dict[str, int]:
    """variant_id -> total quantity, summing any duplicate lines."""
    totals: dict[str, int] = {}
    for edge in (cart.get("lines") or {}).get("edges", []):
        node = edge.get("node") or {}
        variant_id = (node.get("merchandise") or {}).get("id")
        if not variant_id:
            continue
        totals[variant_id] = totals.get(variant_id, 0) + int(node.get("quantity") or 0)
    return totals


def subtotal_from_cart(cart: dict[str, Any]) -> Optional[Money]:
    amount = ((cart.get("cost") or {}).get("subtotalAmount")) or None
    if not amount:
        return None
    try:
        return money_from_shopify(amount)
    except ShopifyError:
        return None


def reconcile_cart_against_approval(
    cart: dict[str, Any], review: CartReview
) -> Reconciliation:
    """Compare the live cart with what the shopper actually approved."""
    expected = {line.variant_id: line.quantity for line in review.lines}
    observed = lines_from_cart(cart)
    expected_subtotal = review.merchandise_subtotal
    observed_subtotal = subtotal_from_cart(cart)

    problems: list[str] = []
    checks: dict[str, bool] = {}

    checks["variant_ids_match"] = set(expected) == set(observed)
    if not checks["variant_ids_match"]:
        extra = sorted(set(observed) - set(expected))
        missing = sorted(set(expected) - set(observed))
        if missing:
            problems.append(f"{len(missing)} approved variant(s) missing from the cart")
        if extra:
            problems.append(f"{len(extra)} variant(s) in the cart were never approved")

    checks["quantities_match"] = all(
        observed.get(variant) == quantity for variant, quantity in expected.items()
    )
    if not checks["quantities_match"]:
        wrong = [
            v for v, q in expected.items() if observed.get(v) is not None and observed[v] != q
        ]
        if wrong:
            problems.append(f"{len(wrong)} line(s) have the wrong quantity")

    checks["currency_matches"] = bool(
        observed_subtotal and observed_subtotal.currency == expected_subtotal.currency
    )
    if not checks["currency_matches"]:
        problems.append("the cart currency does not match the approval")

    checks["subtotal_matches"] = bool(
        observed_subtotal
        and observed_subtotal.amount_minor == expected_subtotal.amount_minor
    )
    if not checks["subtotal_matches"]:
        observed_text = (
            str(observed_subtotal.amount_minor) if observed_subtotal else "unreadable"
        )
        problems.append(
            f"subtotal is {observed_text} but the approval was "
            f"{expected_subtotal.amount_minor} {expected_subtotal.currency}"
        )

    return Reconciliation(
        matches=all(checks.values()),
        checks=checks,
        problems=problems,
        observed_lines=observed,
        expected_lines=expected,
        observed_subtotal=observed_subtotal,
        expected_subtotal=expected_subtotal,
    )


def missing_lines(review: CartReview, cart: dict[str, Any]) -> list[dict[str, Any]]:
    """Approved lines that the cart is short of, as cartLinesAdd input.

    Only ever used to complete a cart that was created but came back
    incomplete. Never used to top up a cart that already matches.
    """
    observed = lines_from_cart(cart)
    additions: list[dict[str, Any]] = []
    for line in review.lines:
        have = observed.get(line.variant_id, 0)
        if have < line.quantity:
            additions.append(
                {"merchandiseId": line.variant_id, "quantity": line.quantity - have}
            )
    return additions


class LiveCartWriter:
    """Creates and verifies one Shopify cart per approval."""

    def __init__(self, storefront: ShopifyStorefront, settings, on_intent=None) -> None:
        self.storefront = storefront
        self.settings = settings
        # Called with the review the instant a pending cart reference exists
        # and before the mutation goes out. Persisting here is what makes a
        # crash mid-write recoverable: the next attempt finds `unknown` and
        # reads back instead of creating a second cart.
        self._on_intent = on_intent

    def write_and_verify(self, review: CartReview) -> tuple[CartReview, Reconciliation]:
        """The whole sequence. Returns the updated review and what was found."""
        check_cart_writes_allowed(self.settings)

        if review.approval is None:
            raise EthyneonError(
                code="not_approved",
                message="A cart is written only for an approved review.",
                retryable=False,
            )

        existing = review.cart_reference

        # Already done. Do not write again.
        if existing and existing.write_outcome == "confirmed" and existing.cart_id != "pending":
            cart = self.storefront.get_cart(existing.cart_id)
            if cart is None:
                raise EthyneonError(
                    code="cart_vanished",
                    message="The recorded cart no longer exists on the store.",
                    retryable=False,
                )
            return self._finish(review, cart)

        # A previous attempt left an unclear outcome. Read, never rewrite.
        if existing and existing.write_outcome == "unknown" and existing.cart_id != "pending":
            cart = self.storefront.get_cart(existing.cart_id)
            if cart is not None:
                return self._complete_if_short(review, cart)
            # The cart genuinely does not exist, so the earlier write did not
            # land and creating one now cannot duplicate anything.

        cart = self._create(review)
        return self._complete_if_short(review, cart)

    def _create(self, review: CartReview) -> dict[str, Any]:
        """cartCreate, with the intent recorded before the call.

        The reference is marked `unknown` first on purpose: if the process
        dies mid-call, the next attempt finds `unknown` and reads back rather
        than writing a second cart.
        """
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
        if self._on_intent is not None:
            self._on_intent(review)
        return self.storefront.create_cart(lines)

    def _complete_if_short(
        self, review: CartReview, cart: dict[str, Any]
    ) -> tuple[CartReview, Reconciliation]:
        """If the read-back is short of approved lines, add exactly the
        shortfall with cartLinesAdd. Never create another cart."""
        additions = missing_lines(review, cart)
        if additions:
            cart = self.storefront.add_cart_lines(cart["id"], additions)
        return self._finish(review, cart)

    def _finish(
        self, review: CartReview, cart: dict[str, Any]
    ) -> tuple[CartReview, Reconciliation]:
        """Read back, verify, and record the outcome."""
        verified = self.storefront.get_cart(cart["id"]) or cart
        reconciliation = reconcile_cart_against_approval(verified, review)

        from backend.cart import cart_fingerprint_from_shopify

        review.cart_reference = CartRef(
            cart_id=verified["id"],
            store_domain=review.store.store_domain,
            # Stored server side. Released only by the checkout gate, after a
            # passing audit. Never logged.
            checkout_url=verified.get("checkoutUrl"),
            fingerprint=cart_fingerprint_from_shopify(verified),
            written_at=datetime.now(timezone.utc),
            write_outcome="confirmed" if reconciliation.matches else "failed",
        )
        review.payment_state = (
            "cart_ready" if reconciliation.matches else "needs_review"
        )
        return review, reconciliation
