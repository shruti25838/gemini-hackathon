"""The money path: what may enter a cart, and what blocks a checkout.

Every one of these is a case the brief names explicitly. They all run against
the mock catalog with no network, and no live cart is ever written.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from contracts.common import Money
from contracts.interfaces import BudgetLine
from backend.cart import CartError, approval_fingerprint
from backend.checkout import build_audit_inputs
from backend.services import Services

pytestmark = [pytest.mark.backend, pytest.mark.commerce]

WALNUT_DESK = "gid://shopify/ProductVariant/49000000000001"
OAK_DESK = "gid://shopify/ProductVariant/49000000000002"
MONITOR = "gid://shopify/ProductVariant/49000000000004"
OUT_OF_STOCK = "gid://shopify/ProductVariant/49000000000011"
NO_DIMENSIONS = "gid://shopify/ProductVariant/49000000000010"
SHOP_MCP_DESK = "gid://shopify/ProductVariant/54307265806649"


def selection(variant_id: str, quantity: int = 1, amount: int = 42900) -> BudgetLine:
    return BudgetLine(
        variant_id=variant_id,
        quantity=quantity,
        unit_price=Money(amount_minor=amount, currency="USD"),
    )


# ==========================================================================
# What may enter a cart
# ==========================================================================


def test_an_unavailable_variant_cannot_reach_the_cart(
    services: Services, session_id: str
) -> None:
    with pytest.raises(CartError) as excinfo:
        services.carts.build_cart_review(
            session_id,
            "shopper_test",
            [selection(OUT_OF_STOCK, amount=52900)],
            scene_revision=0,
            brief_revision=0,
        )
    assert excinfo.value.code == "no_shoppable_lines"
    assert "unavailable" in str(excinfo.value.details["rejected"][0]["reason"])


def test_a_shop_mcp_discovery_result_can_never_reach_the_cart(
    services: Services, session_id: str
) -> None:
    """The rule the whole discovery design rests on."""
    with pytest.raises(CartError) as excinfo:
        services.carts.build_cart_review(
            session_id,
            "shopper_test",
            [selection(SHOP_MCP_DESK, amount=7299)],
            scene_revision=0,
            brief_revision=0,
        )
    assert excinfo.value.code == "no_shoppable_lines"


def test_an_unknown_variant_cannot_reach_the_cart(
    services: Services, session_id: str
) -> None:
    with pytest.raises(CartError):
        services.carts.build_cart_review(
            session_id,
            "shopper_test",
            [selection("gid://shopify/ProductVariant/does-not-exist")],
            scene_revision=0,
            brief_revision=0,
        )


def test_a_variant_with_no_dimensions_may_still_be_bought(
    services: Services, session_id: str
) -> None:
    """Missing dimensions block a fit claim, not a purchase."""
    review = services.carts.build_cart_review(
        session_id,
        "shopper_test",
        [selection(NO_DIMENSIONS, amount=9900)],
        scene_revision=0,
        brief_revision=0,
    )
    assert len(review.lines) == 1


def test_the_live_price_wins_over_whatever_the_client_sent(
    services: Services, session_id: str
) -> None:
    """A client claiming a lower price must not be able to set it."""
    review = services.carts.build_cart_review(
        session_id,
        "shopper_test",
        [selection(WALNUT_DESK, amount=1)],
        scene_revision=0,
        brief_revision=0,
    )
    assert review.lines[0].unit_price.amount_minor == 42900
    assert review.merchandise_subtotal.amount_minor == 42900


def test_the_subtotal_equals_the_sum_of_the_lines(
    services: Services, session_id: str, desk_selection
) -> None:
    review = services.carts.build_cart_review(
        session_id, "shopper_test", desk_selection, scene_revision=0, brief_revision=0
    )
    assert review.merchandise_subtotal.amount_minor == 74800


def test_a_review_states_that_shipping_and_tax_come_later(
    services: Services, session_id: str, desk_selection
) -> None:
    review = services.carts.build_cart_review(
        session_id, "shopper_test", desk_selection, scene_revision=0, brief_revision=0
    )
    assert "Shipping and tax" in review.shipping_and_tax_note


def test_a_mocked_review_is_labelled_mock(
    services: Services, session_id: str, desk_selection
) -> None:
    review = services.carts.build_cart_review(
        session_id, "shopper_test", desk_selection, scene_revision=0, brief_revision=0
    )
    assert review.source == "mock"


# ==========================================================================
# Approval
# ==========================================================================


def test_confirming_binds_an_approval_to_the_exact_lines(
    approved_review,
) -> None:
    assert approved_review.status == "approved"
    assert approved_review.approval is not None
    assert approved_review.approval.fingerprint == approval_fingerprint(approved_review)
    assert approved_review.approval.base_scene_revision == 0


def test_confirming_twice_with_the_same_action_id_replays(
    services: Services, session_id: str, desk_selection
) -> None:
    review = services.carts.build_cart_review(
        session_id, "shopper_test", desk_selection, scene_revision=0, brief_revision=0
    )
    action_id = str(uuid.uuid4())
    first = services.carts.confirm_cart(review.review_id, 0, action_id, current_scene_revision=0)
    second = services.carts.confirm_cart(review.review_id, 0, action_id, current_scene_revision=0)
    assert first.approval.approval_id == second.approval.approval_id


def test_a_newer_scene_revision_voids_the_confirmation(
    services: Services, session_id: str, desk_selection
) -> None:
    review = services.carts.build_cart_review(
        session_id, "shopper_test", desk_selection, scene_revision=0, brief_revision=0
    )
    with pytest.raises(CartError) as excinfo:
        services.carts.confirm_cart(
            review.review_id, 0, str(uuid.uuid4()), current_scene_revision=5
        )
    assert excinfo.value.code == "stale_base_revision"
    assert excinfo.value.status_code == 409
    assert excinfo.value.details["latest_revision"] == 5


def test_a_changed_price_voids_the_approval_and_returns_a_new_review(
    services: Services, session_id: str, desk_selection, monkeypatch
) -> None:
    review = services.carts.build_cart_review(
        session_id, "shopper_test", desk_selection, scene_revision=0, brief_revision=0
    )

    real_get_offer = services.catalog.get_offer

    def pricier(variant_id: str):
        offer = real_get_offer(variant_id)
        if variant_id == WALNUT_DESK:
            offer.price = Money(amount_minor=45900, currency="USD")
        return offer

    monkeypatch.setattr(services.catalog, "get_offer", pricier)

    with pytest.raises(CartError) as excinfo:
        services.carts.confirm_cart(
            review.review_id, 0, str(uuid.uuid4()), current_scene_revision=0
        )

    assert excinfo.value.code == "cart_changed"
    changed = excinfo.value.details["changes"][0]
    assert changed["was"] == 42900 and changed["now"] == 45900

    stored = services.db.get_cart_review(review.review_id)
    assert stored["approval"] is None
    assert stored["payment_state"] == "needs_review"


def test_a_variant_going_out_of_stock_voids_the_approval(
    services: Services, session_id: str, desk_selection, monkeypatch
) -> None:
    review = services.carts.build_cart_review(
        session_id, "shopper_test", desk_selection, scene_revision=0, brief_revision=0
    )
    real_get_offer = services.catalog.get_offer

    def sold_out(variant_id: str):
        offer = real_get_offer(variant_id)
        if variant_id == WALNUT_DESK:
            offer.availability.available = False
        return offer

    monkeypatch.setattr(services.catalog, "get_offer", sold_out)
    with pytest.raises(CartError) as excinfo:
        services.carts.confirm_cart(
            review.review_id, 0, str(uuid.uuid4()), current_scene_revision=0
        )
    assert excinfo.value.code == "cart_changed"


# ==========================================================================
# The checkout gate
# ==========================================================================


def test_a_clean_cart_passes_both_halves(
    services: Services, session_id: str, approved_review
) -> None:
    audit, url = services.checkout.continue_checkout(
        approved_review.review_id,
        str(uuid.uuid4()),
        current_scene_revision=0,
        fit_status="fits",
    )
    assert audit.outcome == "ready"
    assert audit.deterministic_passed is True
    assert audit.next_action == "ready"
    assert all(a.passed_threshold for a in audit.decision_answers)


def test_the_stand_in_is_labelled_on_every_answer(
    services: Services, approved_review
) -> None:
    """A mock must never look live."""
    audit, _ = services.checkout.continue_checkout(
        approved_review.review_id,
        str(uuid.uuid4()),
        current_scene_revision=0,
        fit_status="fits",
    )
    assert {a.source for a in audit.decision_answers} == {"local_stand_in"}


def test_the_wrong_variant_in_the_live_cart_blocks_checkout(
    services: Services, approved_review
) -> None:
    """Same price, different variant. Only the variant check can catch it."""
    live_cart = {
        "id": "gid://shopify/Cart/c1",
        "lines": {
            "edges": [
                {"node": {"quantity": 1, "merchandise": {"id": OAK_DESK}}},
                {"node": {"quantity": 1, "merchandise": {"id": MONITOR}}},
            ]
        },
    }
    audit, url = services.checkout.continue_checkout(
        approved_review.review_id,
        str(uuid.uuid4()),
        current_scene_revision=0,
        fit_status="fits",
        live_cart=live_cart,
    )
    assert audit.outcome == "blocked"
    assert url is None
    failed = {c.name for c in audit.deterministic_results if not c.passed}
    assert "variant_ids_match" in failed


def test_the_wrong_quantity_blocks_checkout(
    services: Services, approved_review
) -> None:
    live_cart = {
        "id": "gid://shopify/Cart/c1",
        "lines": {
            "edges": [
                {"node": {"quantity": 3, "merchandise": {"id": WALNUT_DESK}}},
                {"node": {"quantity": 1, "merchandise": {"id": MONITOR}}},
            ]
        },
    }
    audit, url = services.checkout.continue_checkout(
        approved_review.review_id,
        str(uuid.uuid4()),
        current_scene_revision=0,
        fit_status="fits",
        live_cart=live_cart,
    )
    assert audit.outcome == "blocked"
    assert url is None
    assert "quantities_match" in {c.name for c in audit.deterministic_results if not c.passed}


def test_a_newer_scene_revision_blocks_checkout(
    services: Services, approved_review
) -> None:
    audit, url = services.checkout.continue_checkout(
        approved_review.review_id,
        str(uuid.uuid4()),
        current_scene_revision=9,
        fit_status="fits",
    )
    assert audit.outcome == "blocked"
    assert url is None
    assert "revisions_current" in {c.name for c in audit.deterministic_results if not c.passed}


def test_an_expired_approval_blocks_checkout(
    services: Services, approved_review
) -> None:
    stored = services.db.get_cart_review(approved_review.review_id)
    stored["approval"]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    services.db.upsert_cart_review(stored)

    audit, url = services.checkout.continue_checkout(
        approved_review.review_id,
        str(uuid.uuid4()),
        current_scene_revision=0,
        fit_status="fits",
    )
    assert audit.outcome == "blocked"
    assert url is None
    failing = {c.name for c in audit.deterministic_results if not c.passed}
    assert "approval_valid" in failing


def test_editing_the_review_after_approval_breaks_the_fingerprint(
    services: Services, approved_review
) -> None:
    stored = services.db.get_cart_review(approved_review.review_id)
    stored["lines"][0]["quantity"] = 2
    stored["lines"][0]["line_subtotal"]["amount_minor"] = 85800
    stored["merchandise_subtotal"]["amount_minor"] = 117700
    services.db.upsert_cart_review(stored)

    audit, url = services.checkout.continue_checkout(
        approved_review.review_id,
        str(uuid.uuid4()),
        current_scene_revision=0,
        fit_status="fits",
    )
    assert audit.outcome == "blocked"
    assert url is None
    assert "approval_valid" in {c.name for c in audit.deterministic_results if not c.passed}


def test_a_does_not_fit_scene_blocks_checkout(
    services: Services, approved_review
) -> None:
    audit, url = services.checkout.continue_checkout(
        approved_review.review_id,
        str(uuid.uuid4()),
        current_scene_revision=0,
        fit_status="does_not_fit",
    )
    assert audit.outcome == "blocked"
    assert url is None
    assert "fit_status_acceptable" in {
        c.name for c in audit.deterministic_results if not c.passed
    }


def test_an_unknown_fit_blocks_checkout(services: Services, approved_review) -> None:
    audit, url = services.checkout.continue_checkout(
        approved_review.review_id,
        str(uuid.uuid4()),
        current_scene_revision=0,
        fit_status="unknown",
    )
    assert audit.outcome == "blocked"
    assert url is None


def test_over_budget_blocks_checkout(services: Services, session_id: str) -> None:
    review = services.carts.build_cart_review(
        session_id,
        "shopper_test",
        [selection(WALNUT_DESK), selection(MONITOR, amount=31900)],
        scene_revision=0,
        brief_revision=0,
        budget=Money(amount_minor=50000, currency="USD"),
    )
    confirmed = services.carts.confirm_cart(
        review.review_id, 0, str(uuid.uuid4()), current_scene_revision=0
    )
    audit, url = services.checkout.continue_checkout(
        confirmed.review_id, str(uuid.uuid4()), current_scene_revision=0, fit_status="fits"
    )
    assert audit.outcome == "blocked"
    assert url is None
    assert "within_budget" in {c.name for c in audit.deterministic_results if not c.passed}


def test_an_unavailable_decisions_api_pauses_rather_than_passing(
    services: Services, approved_review
) -> None:
    """Low confidence, disagreement or an outage holds checkout and keeps the
    verified cart. It never falls through to ready."""
    services.checkout.use_stand_in = False
    audit, url = services.checkout.continue_checkout(
        approved_review.review_id,
        str(uuid.uuid4()),
        current_scene_revision=0,
        fit_status="fits",
    )
    assert audit.outcome == "unavailable"
    assert url is None
    assert audit.next_action != "ready"

    # The cart survives the pause.
    stored = services.db.get_cart_review(approved_review.review_id)
    assert stored["lines"]
    assert stored["payment_state"] == "needs_review"


def test_a_low_confidence_answer_does_not_pass_its_threshold(
    services: Services, approved_review, monkeypatch
) -> None:
    import backend.checkout as checkout_module
    from contracts.interfaces import GateAnswer, GateAnswers

    def unsure(packet, client=None, use_stand_in=True):
        return GateAnswers(
            gate_set="checkout_audit",
            answers=[
                GateAnswer(
                    gate="checkout_audit",
                    question="purchase_scope_matches",
                    answer="yes",
                    confidence=0.41,
                    threshold=0.80,
                    passed=False,
                    source="local_stand_in",
                ),
            ],
            all_passed=False,
        )

    monkeypatch.setattr(checkout_module, "run_checkout_gates", unsure)
    audit, url = services.checkout.continue_checkout(
        approved_review.review_id,
        str(uuid.uuid4()),
        current_scene_revision=0,
        fit_status="fits",
    )
    assert audit.outcome == "needs_review"
    assert url is None


def test_the_audit_is_bound_to_the_approval_and_cart_fingerprints(
    services: Services, approved_review
) -> None:
    audit, _ = services.checkout.continue_checkout(
        approved_review.review_id,
        str(uuid.uuid4()),
        current_scene_revision=0,
        fit_status="fits",
    )
    assert audit.approval_fingerprint == approved_review.approval.fingerprint
    assert audit.expires_at > datetime.now(timezone.utc)


def test_continue_checkout_is_idempotent(
    services: Services, approved_review
) -> None:
    action_id = str(uuid.uuid4())
    first, _ = services.checkout.continue_checkout(
        approved_review.review_id, action_id, current_scene_revision=0, fit_status="fits"
    )
    second, _ = services.checkout.continue_checkout(
        approved_review.review_id, action_id, current_scene_revision=0, fit_status="fits"
    )
    assert first.audit_id == second.audit_id


def test_checkout_without_an_approval_is_refused(
    services: Services, session_id: str, desk_selection
) -> None:
    review = services.carts.build_cart_review(
        session_id, "shopper_test", desk_selection, scene_revision=0, brief_revision=0
    )
    from backend.checkout import CheckoutError

    with pytest.raises(CheckoutError) as excinfo:
        services.checkout.continue_checkout(
            review.review_id, str(uuid.uuid4()), current_scene_revision=0, fit_status="fits"
        )
    assert excinfo.value.code == "not_approved"


# ==========================================================================
# The evidence packet
# ==========================================================================


def test_the_audit_packet_carries_no_contact_or_payment_data(
    approved_review,
) -> None:
    packet = build_audit_inputs(approved_review, fit_status="fits")
    blob = packet.model_dump_json().lower()
    for forbidden in (
        "email",
        "phone",
        "address",
        "card",
        "token",
        "checkout_url",
        "credential",
        "password",
    ):
        assert forbidden not in blob, f"the evidence packet leaked {forbidden}"


def test_the_audit_packet_carries_the_facts_the_gate_needs(
    approved_review,
) -> None:
    packet = build_audit_inputs(approved_review, fit_status="fits")
    assert packet.approved_lines
    assert packet.merchandise_subtotal.amount_minor == 74800
    assert packet.approval_fingerprint
    assert packet.scene_revision == 0


# ==========================================================================
# Payment states
# ==========================================================================


def test_opening_the_checkout_is_not_payment(
    services: Services, approved_review
) -> None:
    services.checkout.continue_checkout(
        approved_review.review_id,
        str(uuid.uuid4()),
        current_scene_revision=0,
        fit_status="fits",
    )
    review = services.checkout.mark_payment_unconfirmed(approved_review.review_id)
    assert review.payment_state == "payment_unconfirmed"


def test_an_order_cannot_be_confirmed_while_the_lookup_is_disabled(
    services: Services, approved_review
) -> None:
    from backend.checkout import CheckoutError

    with pytest.raises(CheckoutError) as excinfo:
        services.checkout.confirm_order(
            approved_review.review_id, order_id="gid://shopify/Order/1", lookup_enabled=False
        )
    assert excinfo.value.code == "order_lookup_disabled"


def test_order_confirmed_requires_an_actual_order_id(
    services: Services, approved_review
) -> None:
    review = services.checkout.confirm_order(
        approved_review.review_id, order_id=None, lookup_enabled=True
    )
    assert review.payment_state == "payment_unconfirmed"


def test_order_confirmed_only_from_a_lookup(
    services: Services, approved_review
) -> None:
    review = services.checkout.confirm_order(
        approved_review.review_id,
        order_id="gid://shopify/Order/12345",
        lookup_enabled=True,
    )
    assert review.payment_state == "order_confirmed"


# ==========================================================================
# Cart writes stay off
# ==========================================================================


def test_no_live_cart_write_happens_while_commerce_is_mocked(
    services: Services, approved_review
) -> None:
    with pytest.raises(CartError) as excinfo:
        services.carts.write_cart_once(approved_review)
    assert excinfo.value.code == "commerce_is_mocked"


def test_the_storefront_client_refuses_cart_writes_by_default() -> None:
    from backend.shopify import ShopifyError, ShopifyStorefront

    client = ShopifyStorefront("dev.myshopify.com", "token-placeholder", "2026-10")
    assert client.allow_cart_writes is False
    with pytest.raises(ShopifyError) as excinfo:
        client.create_cart([{"merchandiseId": WALNUT_DESK, "quantity": 1}])
    assert excinfo.value.code == "cart_writes_disabled"


def test_an_unclear_write_is_read_back_rather_than_retried(
    services: Services, approved_review
) -> None:
    """A second write could double-charge. Reading cannot."""
    from contracts.cart_review import CartRef

    reads: list[str] = []

    class FakeStorefront:
        allow_cart_writes = True

        def get_cart(self, cart_id: str):
            reads.append(cart_id)
            return {
                "id": cart_id,
                "lines": {"edges": []},
                "cost": {"subtotalAmount": {"amount": "748.00", "currencyCode": "USD"}},
            }

        def create_cart(self, lines):  # pragma: no cover - must never be called
            raise AssertionError("an unclear outcome must never trigger another write")

    approved_review.cart_reference = CartRef(
        cart_id="gid://shopify/Cart/c1",
        store_domain=approved_review.store.store_domain,
        write_outcome="unknown",
    )
    services.db.upsert_cart_review(approved_review.model_dump(mode="json"))
    services.catalog._storefront = FakeStorefront()

    result = services.carts.write_cart_once(approved_review)

    assert reads == ["gid://shopify/Cart/c1"]
    assert result.cart_reference.write_outcome == "confirmed"
