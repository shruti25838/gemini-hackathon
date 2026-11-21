"""The live cart write path, exercised offline with a fake Storefront.

Nothing here touches a real store. The fake records every mutation it
receives, so "wrote once" is an assertion about call counts rather than a
hope.

The cases that matter, in the order they would hurt:
  a second cartCreate after an unclear outcome would give the shopper two
  carts; a second cartLinesAdd would double their quantities; a URL released
  against a stale audit would let them pay for something they did not approve.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from contracts.cart_review import CartRef, CartReview
from contracts.common import Money
from backend.cart_write import (
    CartWriteRefused,
    LiveCartWriter,
    check_cart_writes_allowed,
    lines_from_cart,
    missing_lines,
    reconcile_cart_against_approval,
)
from backend.config import Settings
from backend.services import Services, build_services

pytestmark = [pytest.mark.backend, pytest.mark.commerce]

DEV_STORE = "ethyneon.myshopify.com"
WALNUT_DESK = "gid://shopify/ProductVariant/49000000000001"
MONITOR = "gid://shopify/ProductVariant/49000000000004"


# ==========================================================================
# A fake Storefront that counts its mutations
# ==========================================================================


class FakeStorefront:
    """Records every call. Never touches a network."""

    allow_cart_writes = True

    def __init__(
        self,
        *,
        create_returns: Optional[dict[str, Any]] = None,
        create_raises: Optional[Exception] = None,
        read_returns: Optional[dict[str, Any]] = None,
    ) -> None:
        self.creates: list[list[dict[str, Any]]] = []
        self.adds: list[tuple[str, list[dict[str, Any]]]] = []
        self.reads: list[str] = []
        self._create_returns = create_returns
        self._create_raises = create_raises
        self._read_returns = read_returns
        self.carts: dict[str, dict[str, Any]] = {}

    def create_cart(self, lines: list[dict[str, Any]]) -> dict[str, Any]:
        self.creates.append(lines)
        if self._create_raises is not None:
            raise self._create_raises
        cart = self._create_returns or build_cart(lines)
        self.carts[cart["id"]] = cart
        return cart

    def add_cart_lines(self, cart_id: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
        self.adds.append((cart_id, lines))
        cart = self.carts.get(cart_id) or build_cart([], cart_id=cart_id)
        existing = lines_from_cart(cart)
        for line in lines:
            existing[line["merchandiseId"]] = (
                existing.get(line["merchandiseId"], 0) + line["quantity"]
            )
        updated = build_cart(
            [{"merchandiseId": v, "quantity": q} for v, q in existing.items()],
            cart_id=cart_id,
        )
        self.carts[cart_id] = updated
        return updated

    def get_cart(self, cart_id: str) -> Optional[dict[str, Any]]:
        self.reads.append(cart_id)
        if self._read_returns is not None:
            return self._read_returns
        return self.carts.get(cart_id)


PRICES = {WALNUT_DESK: 42900, MONITOR: 31900}


def build_cart(
    lines: list[dict[str, Any]],
    *,
    cart_id: str = "gid://shopify/Cart/c1-abcdef123456",
    currency: str = "USD",
    subtotal_override: Optional[str] = None,
) -> dict[str, Any]:
    total = sum(PRICES.get(l["merchandiseId"], 0) * l["quantity"] for l in lines)
    amount = subtotal_override if subtotal_override is not None else f"{total // 100}.{total % 100:02d}"
    return {
        "id": cart_id,
        "checkoutUrl": f"https://{DEV_STORE}/cart/c/{cart_id.rsplit('/', 1)[-1]}",
        "cost": {"subtotalAmount": {"amount": amount, "currencyCode": currency}},
        "lines": {
            "edges": [
                {
                    "node": {
                        "id": f"gid://shopify/CartLine/{i}",
                        "quantity": line["quantity"],
                        "merchandise": {"id": line["merchandiseId"]},
                    }
                }
                for i, line in enumerate(lines)
            ]
        },
    }


@pytest.fixture
def live_settings() -> Settings:
    """Mocked catalog, but both cart-write gates open."""
    return Settings(
        _env_file=None,
        MOCK_AI=True,
        MOCK_COMMERCE=True,
        MOCK_ROOM_INPUT=True,
        MOCK_AGORA=True,
        SHOP_MCP=False,
        GOOGLE_API_KEY=None,
        GEMINI_API_KEY=None,
        LIVE_CART_WRITES=True,
        SHOPIFY_STORE_DOMAIN=DEV_STORE,
        ALLOWED_CART_STORE_DOMAINS=DEV_STORE,
    )


@pytest.fixture
def live_services(live_settings: Settings, db) -> Services:
    return build_services(live_settings, db)


@pytest.fixture
def live_review(live_services: Services) -> CartReview:
    from contracts.interfaces import BudgetLine

    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    live_services.db.create_session(session_id, "shopper_live")
    selections = [
        BudgetLine(
            variant_id=WALNUT_DESK, quantity=1, unit_price=Money(amount_minor=0, currency="USD")
        ),
        BudgetLine(
            variant_id=MONITOR, quantity=1, unit_price=Money(amount_minor=0, currency="USD")
        ),
    ]
    review = live_services.carts.build_cart_review(
        session_id,
        "shopper_live",
        selections,
        scene_revision=0,
        brief_revision=0,
        budget=Money(amount_minor=120000, currency="USD"),
    )
    return live_services.carts.confirm_cart(
        review.review_id, 0, str(uuid.uuid4()), current_scene_revision=0
    )


# ==========================================================================
# The guard
# ==========================================================================


def test_writes_are_off_by_default() -> None:
    settings = Settings(_env_file=None, SHOPIFY_STORE_DOMAIN=DEV_STORE)
    assert settings.cart_writes_allowed is False
    with pytest.raises(CartWriteRefused) as excinfo:
        check_cart_writes_allowed(settings)
    assert "LIVE_CART_WRITES is off" in str(excinfo.value)


def test_the_flag_alone_is_not_enough_on_a_non_allowlisted_store() -> None:
    """The case that matters: a real shop's domain in the config by mistake."""
    settings = Settings(
        _env_file=None,
        LIVE_CART_WRITES=True,
        SHOPIFY_STORE_DOMAIN="someones-real-shop.myshopify.com",
        ALLOWED_CART_STORE_DOMAINS=DEV_STORE,
    )
    assert settings.cart_writes_allowed is False
    with pytest.raises(CartWriteRefused) as excinfo:
        check_cart_writes_allowed(settings)
    assert "not on the cart-write allowlist" in str(excinfo.value)


def test_the_allowlist_alone_is_not_enough_without_the_flag() -> None:
    settings = Settings(
        _env_file=None, SHOPIFY_STORE_DOMAIN=DEV_STORE, ALLOWED_CART_STORE_DOMAINS=DEV_STORE
    )
    assert settings.cart_writes_allowed is False


def test_both_gates_open_permits_a_write(live_settings: Settings) -> None:
    assert live_settings.cart_writes_allowed is True
    check_cart_writes_allowed(live_settings)


def test_the_allowlist_ignores_case_and_whitespace() -> None:
    settings = Settings(
        _env_file=None,
        LIVE_CART_WRITES=True,
        SHOPIFY_STORE_DOMAIN="  Ethyneon.MyShopify.com ",
        ALLOWED_CART_STORE_DOMAINS="ethyneon.myshopify.com",
    )
    assert settings.cart_writes_allowed is True


def test_a_refusal_uses_the_standard_error_shape() -> None:
    from contracts.common import ErrorResponse

    settings = Settings(_env_file=None, SHOPIFY_STORE_DOMAIN=DEV_STORE)
    with pytest.raises(CartWriteRefused) as excinfo:
        check_cart_writes_allowed(settings)
    payload = excinfo.value.to_response()
    ErrorResponse.model_validate(payload.model_dump(mode="json"))
    assert payload.code == "live_cart_writes_refused"
    assert payload.retryable is False
    assert excinfo.value.status_code == 403


def test_a_refusal_never_leaks_a_token() -> None:
    settings = Settings(
        _env_file=None,
        SHOPIFY_STORE_DOMAIN=DEV_STORE,
        SHOPIFY_STOREFRONT_TOKEN="super-secret-value",
    )
    with pytest.raises(CartWriteRefused) as excinfo:
        check_cart_writes_allowed(settings)
    blob = str(excinfo.value) + str(excinfo.value.details)
    assert "super-secret-value" not in blob


def test_the_service_refuses_when_the_guard_is_closed(services, approved_review) -> None:
    """MOCK_COMMERCE on means no storefront at all, which is its own refusal."""
    from backend.cart import CartError

    with pytest.raises(CartError) as excinfo:
        services.carts.write_cart_once(approved_review)
    assert excinfo.value.code == "commerce_is_mocked"


# ==========================================================================
# Writing once
# ==========================================================================


def test_a_clean_write_creates_exactly_one_cart(
    live_services: Services, live_review: CartReview
) -> None:
    fake = FakeStorefront()
    live_services.catalog._storefront = fake

    review, reconciliation = live_services.carts.write_cart_once(live_review)

    assert len(fake.creates) == 1, "exactly one cartCreate"
    assert fake.adds == [], "no cartLinesAdd was needed"
    assert reconciliation.matches is True
    assert review.cart_reference.write_outcome == "confirmed"
    assert review.payment_state == "cart_ready"


def test_the_write_uses_the_exact_approved_variant_ids(
    live_services: Services, live_review: CartReview
) -> None:
    fake = FakeStorefront()
    live_services.catalog._storefront = fake
    live_services.carts.write_cart_once(live_review)

    sent = {line["merchandiseId"]: line["quantity"] for line in fake.creates[0]}
    approved = {line.variant_id: line.quantity for line in live_review.lines}
    assert sent == approved


def test_the_cart_is_read_back_after_writing(
    live_services: Services, live_review: CartReview
) -> None:
    """Trusting the mutation response alone is how a mismatch slips through."""
    fake = FakeStorefront()
    live_services.catalog._storefront = fake
    live_services.carts.write_cart_once(live_review)
    assert fake.reads, "the cart must be read back before it is trusted"


def test_a_second_call_does_not_write_again(
    live_services: Services, live_review: CartReview
) -> None:
    fake = FakeStorefront()
    live_services.catalog._storefront = fake

    review, _ = live_services.carts.write_cart_once(live_review)
    review, _ = live_services.carts.write_cart_once(review)

    assert len(fake.creates) == 1, "a duplicate request must not create a second cart"
    assert fake.adds == []


def test_a_duplicate_request_still_verifies_the_cart(
    live_services: Services, live_review: CartReview
) -> None:
    fake = FakeStorefront()
    live_services.catalog._storefront = fake
    review, _ = live_services.carts.write_cart_once(live_review)
    reads_after_first = len(fake.reads)
    review, reconciliation = live_services.carts.write_cart_once(review)
    assert len(fake.reads) > reads_after_first
    assert reconciliation.matches is True


# ==========================================================================
# Unclear outcomes
# ==========================================================================


def test_an_unclear_outcome_is_read_back_and_never_rewritten(
    live_services: Services, live_review: CartReview
) -> None:
    """The case that would otherwise double-charge."""
    cart_id = "gid://shopify/Cart/c1-already-written"
    fake = FakeStorefront()
    fake.carts[cart_id] = build_cart(
        [
            {"merchandiseId": WALNUT_DESK, "quantity": 1},
            {"merchandiseId": MONITOR, "quantity": 1},
        ],
        cart_id=cart_id,
    )
    live_services.catalog._storefront = fake

    live_review.cart_reference = CartRef(
        cart_id=cart_id,
        store_domain=live_review.store.store_domain,
        write_outcome="unknown",
    )
    live_services.db.upsert_cart_review(live_review.model_dump(mode="json"))

    review, reconciliation = live_services.carts.write_cart_once(live_review)

    assert fake.creates == [], "an unclear outcome must never trigger another cartCreate"
    assert fake.adds == [], "the cart was already complete"
    assert fake.reads, "it must be read back"
    assert reconciliation.matches is True
    assert review.cart_reference.write_outcome == "confirmed"


def test_an_incomplete_cart_is_completed_with_lines_add_not_a_new_cart(
    live_services: Services, live_review: CartReview
) -> None:
    cart_id = "gid://shopify/Cart/c1-partial"
    fake = FakeStorefront()
    # The desk landed; the monitor did not.
    fake.carts[cart_id] = build_cart(
        [{"merchandiseId": WALNUT_DESK, "quantity": 1}], cart_id=cart_id
    )
    live_services.catalog._storefront = fake

    live_review.cart_reference = CartRef(
        cart_id=cart_id,
        store_domain=live_review.store.store_domain,
        write_outcome="unknown",
    )

    review, reconciliation = live_services.carts.write_cart_once(live_review)

    assert fake.creates == [], "never a second cart"
    assert len(fake.adds) == 1, "exactly one cartLinesAdd for the shortfall"
    added = {line["merchandiseId"]: line["quantity"] for line in fake.adds[0][1]}
    assert added == {MONITOR: 1}, "only the missing quantity is added"
    assert reconciliation.matches is True


def test_an_already_complete_cart_gets_no_lines_add(
    live_services: Services, live_review: CartReview
) -> None:
    """Topping up a cart that already matches is how quantities double."""
    cart = build_cart(
        [
            {"merchandiseId": WALNUT_DESK, "quantity": 1},
            {"merchandiseId": MONITOR, "quantity": 1},
        ]
    )
    assert missing_lines(live_review, cart) == []


def test_a_vanished_cart_after_an_unclear_write_is_recreated_once(
    live_services: Services, live_review: CartReview
) -> None:
    """If the cart genuinely does not exist, the earlier write did not land,
    so creating one now cannot duplicate anything."""
    fake = FakeStorefront()
    live_services.catalog._storefront = fake
    live_review.cart_reference = CartRef(
        cart_id="gid://shopify/Cart/c1-never-existed",
        store_domain=live_review.store.store_domain,
        write_outcome="unknown",
    )

    review, reconciliation = live_services.carts.write_cart_once(live_review)

    assert len(fake.creates) == 1
    assert reconciliation.matches is True


def test_the_intent_is_recorded_before_the_call(
    live_services: Services, live_review: CartReview
) -> None:
    """If the process dies mid-write, the next attempt must find `unknown`."""
    from backend.shopify import ShopifyError

    fake = FakeStorefront(create_raises=ShopifyError("timeout", retryable=True))
    live_services.catalog._storefront = fake

    with pytest.raises(ShopifyError):
        live_services.carts.write_cart_once(live_review)

    stored = live_services.db.get_cart_review(live_review.review_id)
    assert stored["cart_reference"] is not None
    assert stored["cart_reference"]["write_outcome"] == "unknown"


# ==========================================================================
# Verification against the approval
# ==========================================================================


def test_a_cart_with_an_extra_line_does_not_reconcile(live_review: CartReview) -> None:
    cart = build_cart(
        [
            {"merchandiseId": WALNUT_DESK, "quantity": 1},
            {"merchandiseId": MONITOR, "quantity": 1},
            {"merchandiseId": "gid://shopify/ProductVariant/sneaky", "quantity": 1},
        ]
    )
    result = reconcile_cart_against_approval(cart, live_review)
    assert result.matches is False
    assert result.checks["variant_ids_match"] is False
    assert any("never approved" in p for p in result.problems)


def test_a_cart_with_the_wrong_quantity_does_not_reconcile(
    live_review: CartReview,
) -> None:
    cart = build_cart(
        [
            {"merchandiseId": WALNUT_DESK, "quantity": 3},
            {"merchandiseId": MONITOR, "quantity": 1},
        ]
    )
    result = reconcile_cart_against_approval(cart, live_review)
    assert result.matches is False
    assert result.checks["quantities_match"] is False


def test_a_cart_in_another_currency_does_not_reconcile(
    live_review: CartReview,
) -> None:
    cart = build_cart(
        [
            {"merchandiseId": WALNUT_DESK, "quantity": 1},
            {"merchandiseId": MONITOR, "quantity": 1},
        ],
        currency="EUR",
    )
    result = reconcile_cart_against_approval(cart, live_review)
    assert result.matches is False
    assert result.checks["currency_matches"] is False


def test_a_cart_whose_subtotal_drifted_does_not_reconcile(
    live_review: CartReview,
) -> None:
    cart = build_cart(
        [
            {"merchandiseId": WALNUT_DESK, "quantity": 1},
            {"merchandiseId": MONITOR, "quantity": 1},
        ],
        subtotal_override="999.00",
    )
    result = reconcile_cart_against_approval(cart, live_review)
    assert result.matches is False
    assert result.checks["subtotal_matches"] is False


def test_a_mismatched_cart_is_marked_failed_and_needs_review(
    live_services: Services, live_review: CartReview
) -> None:
    wrong = build_cart([{"merchandiseId": WALNUT_DESK, "quantity": 5}])
    fake = FakeStorefront(create_returns=wrong, read_returns=wrong)
    live_services.catalog._storefront = fake

    review, reconciliation = live_services.carts.write_cart_once(live_review)

    assert reconciliation.matches is False
    assert review.cart_reference.write_outcome == "failed"
    assert review.payment_state == "needs_review"


def test_the_reconciliation_summary_carries_no_url_or_credential(
    live_review: CartReview,
) -> None:
    cart = build_cart([{"merchandiseId": WALNUT_DESK, "quantity": 9}])
    result = reconcile_cart_against_approval(cart, live_review)
    blob = result.summary() + str(result.problems) + str(result.checks)
    assert "http" not in blob.lower()
    assert "checkout" not in blob.lower()


# ==========================================================================
# Releasing the checkout URL
# ==========================================================================


def write_and_audit(
    live_services: Services, live_review: CartReview, **kwargs: Any
):
    fake = FakeStorefront()
    live_services.catalog._storefront = fake
    review, _ = live_services.carts.write_cart_once(live_review)
    audit, url = live_services.checkout.continue_checkout(
        review.review_id,
        str(uuid.uuid4()),
        current_scene_revision=0,
        fit_status=kwargs.get("fit_status", "fits"),
    )
    return fake, review, audit, url


def test_a_passing_audit_releases_the_url(
    live_services: Services, live_review: CartReview
) -> None:
    _fake, _review, audit, url = write_and_audit(live_services, live_review)
    assert audit.outcome == "ready"
    assert url is not None and url.startswith("https://")


def test_a_failing_audit_releases_nothing(
    live_services: Services, live_review: CartReview
) -> None:
    _fake, _review, audit, url = write_and_audit(
        live_services, live_review, fit_status="does_not_fit"
    )
    assert audit.outcome == "blocked"
    assert url is None


def test_a_cart_changed_after_the_audit_releases_nothing(
    live_services: Services, live_review: CartReview
) -> None:
    """Someone edits the cart in another tab between audit and handoff."""
    fake = FakeStorefront()
    live_services.catalog._storefront = fake
    review, _ = live_services.carts.write_cart_once(live_review)

    # Mutate the live cart behind the app's back.
    cart_id = review.cart_reference.cart_id
    fake.carts[cart_id] = build_cart(
        [{"merchandiseId": WALNUT_DESK, "quantity": 7}], cart_id=cart_id
    )

    audit, url = live_services.checkout.continue_checkout(
        review.review_id, str(uuid.uuid4()), current_scene_revision=0, fit_status="fits"
    )
    assert url is None
    assert audit.outcome != "ready"


def test_an_expired_audit_releases_nothing(
    live_services: Services, live_review: CartReview
) -> None:
    from backend.checkout import CheckoutService

    fake = FakeStorefront()
    live_services.catalog._storefront = fake
    review, _ = live_services.carts.write_cart_once(live_review)

    # An audit that expires the instant it is made.
    live_services.checkout.audit_ttl_seconds = -1
    audit, url = live_services.checkout.continue_checkout(
        review.review_id, str(uuid.uuid4()), current_scene_revision=0, fit_status="fits"
    )
    assert url is None


def test_the_url_is_bound_to_this_approval(
    live_services: Services, live_review: CartReview
) -> None:
    fake = FakeStorefront()
    live_services.catalog._storefront = fake
    review, _ = live_services.carts.write_cart_once(live_review)

    # Edit the review after approval, which breaks the approval fingerprint.
    stored = live_services.db.get_cart_review(review.review_id)
    stored["lines"][0]["quantity"] = 2
    stored["lines"][0]["line_subtotal"]["amount_minor"] = 85800
    stored["merchandise_subtotal"]["amount_minor"] = 117700
    live_services.db.upsert_cart_review(stored)

    audit, url = live_services.checkout.continue_checkout(
        review.review_id, str(uuid.uuid4()), current_scene_revision=0, fit_status="fits"
    )
    assert url is None


def test_no_event_or_log_ever_contains_the_checkout_url(
    live_services: Services, live_review: CartReview
) -> None:
    """The URL is private to the shopper. It must not ride along in events."""
    _fake, review, _audit, url = write_and_audit(live_services, live_review)
    assert url

    events = live_services.db.events_since(review.session_id)
    blob = str(events)
    assert url not in blob
    assert "/cart/c/" not in blob
    assert "checkoutUrl" not in blob


# ==========================================================================
# Payment states
# ==========================================================================


def test_opening_the_checkout_is_not_payment(
    live_services: Services, live_review: CartReview
) -> None:
    _fake, review, _audit, url = write_and_audit(live_services, live_review)
    assert url
    stored = live_services.db.get_cart_review(review.review_id)
    assert stored["payment_state"] == "checkout_opened"

    after = live_services.checkout.mark_payment_unconfirmed(review.review_id)
    assert after.payment_state == "payment_unconfirmed"


def test_order_confirmation_needs_the_flag_and_a_separate_credential() -> None:
    settings = Settings(_env_file=None, ENABLE_ORDER_LOOKUP=True)
    assert settings.order_lookup_ready is False
    assert "SHOPIFY_ORDER_LOOKUP_TOKEN" in settings.order_lookup_blocked_reason()


def test_the_order_credential_must_differ_from_the_storefront_token() -> None:
    settings = Settings(
        _env_file=None,
        ENABLE_ORDER_LOOKUP=True,
        SHOPIFY_STOREFRONT_TOKEN="same-value",
        SHOPIFY_ORDER_LOOKUP_TOKEN="same-value",
    )
    assert settings.order_lookup_ready is False
    assert "separate" in settings.order_lookup_blocked_reason()


def test_a_distinct_credential_with_the_flag_is_ready() -> None:
    settings = Settings(
        _env_file=None,
        ENABLE_ORDER_LOOKUP=True,
        SHOPIFY_STOREFRONT_TOKEN="storefront-value",
        SHOPIFY_ORDER_LOOKUP_TOKEN="order-lookup-value",
    )
    assert settings.order_lookup_ready is True
    assert settings.order_lookup_blocked_reason() is None


def test_without_the_credential_the_state_stays_payment_unconfirmed(
    live_services: Services, live_review: CartReview
) -> None:
    from backend.checkout import CheckoutError

    _fake, review, _audit, _url = write_and_audit(live_services, live_review)
    live_services.checkout.mark_payment_unconfirmed(review.review_id)

    with pytest.raises(CheckoutError) as excinfo:
        live_services.checkout.confirm_order(
            review.review_id,
            order_id="gid://shopify/Order/1",
            lookup_enabled=live_services.settings.order_lookup_ready,
            blocked_reason=live_services.settings.order_lookup_blocked_reason(),
        )
    assert excinfo.value.code == "order_lookup_disabled"
    assert excinfo.value.details["payment_state"] == "payment_unconfirmed"

    stored = live_services.db.get_cart_review(review.review_id)
    assert stored["payment_state"] == "payment_unconfirmed"


def test_paid_is_never_reachable_from_the_handoff_alone(
    live_services: Services, live_review: CartReview
) -> None:
    _fake, review, _audit, url = write_and_audit(live_services, live_review)
    assert url
    stored = live_services.db.get_cart_review(review.review_id)
    assert stored["payment_state"] != "order_confirmed"


# ==========================================================================
# Nothing sensitive reaches storage or logs
# ==========================================================================


SENTINEL_TOKEN = "shpat_sentinel_must_never_appear_anywhere"


def test_no_stored_row_ever_contains_the_storefront_token(
    live_settings: Settings, db, live_review: CartReview
) -> None:
    """Run the whole path with a recognisable token and then grep the database."""
    settings = live_settings.model_copy(
        update={"shopify_storefront_token": SENTINEL_TOKEN}
    )
    services = build_services(settings, db)
    services.catalog._storefront = FakeStorefront()

    review, _ = services.carts.write_cart_once(live_review)
    services.checkout.continue_checkout(
        review.review_id, str(uuid.uuid4()), current_scene_revision=0, fit_status="fits"
    )

    tables = (
        "cart_reviews",
        "checkout_audits",
        "activity_events",
        "actions",
        "offer_cache",
    )
    for table in tables:
        rows = services.db.query_all(f"SELECT * FROM {table}")
        blob = str([dict(r) for r in rows])
        assert SENTINEL_TOKEN not in blob, f"the token reached the {table} table"


def test_an_error_envelope_never_carries_a_token(live_settings: Settings) -> None:
    settings = live_settings.model_copy(
        update={
            "shopify_storefront_token": SENTINEL_TOKEN,
            "live_cart_writes": False,
        }
    )
    with pytest.raises(CartWriteRefused) as excinfo:
        check_cart_writes_allowed(settings)
    payload = excinfo.value.to_response().model_dump_json()
    assert SENTINEL_TOKEN not in payload


def test_the_smoke_script_only_prints_a_cart_id_suffix() -> None:
    """A full cart ID in a scrollback is more than anyone needs."""
    from scripts.live_cart_smoke import cart_suffix

    full = "gid://shopify/Cart/c1-abcdefghijklmnop"
    suffix = cart_suffix(full)
    assert len(suffix) == 6
    assert suffix in full
    assert "gid://" not in suffix


def test_the_health_endpoint_never_exposes_a_token(
    live_settings: Settings, db, monkeypatch
) -> None:
    import backend.services as services_module
    from fastapi.testclient import TestClient
    from backend.app import app

    settings = live_settings.model_copy(
        update={"shopify_storefront_token": SENTINEL_TOKEN}
    )
    monkeypatch.setattr(services_module, "_services", build_services(settings, db))
    body = TestClient(app).get("/api/health").text
    assert SENTINEL_TOKEN not in body


def test_api_state_withholds_the_checkout_url(
    live_settings: Settings, db, monkeypatch
) -> None:
    """A bearer link to a hosted checkout does not belong in a broad read.

    It is released only by POST /api/checkout/continue, after a passing
    audit bound to this approval and cart.
    """
    import backend.services as services_module
    from fastapi.testclient import TestClient
    from contracts.interfaces import BudgetLine
    from backend.app import app

    services = build_services(live_settings, db)
    services.catalog._storefront = FakeStorefront()
    monkeypatch.setattr(services_module, "_services", services)

    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    services.db.create_session(session_id, "shopper_state")
    review = services.carts.build_cart_review(
        session_id,
        "shopper_state",
        [
            BudgetLine(
                variant_id=WALNUT_DESK,
                quantity=1,
                unit_price=Money(amount_minor=0, currency="USD"),
            )
        ],
        scene_revision=0,
        brief_revision=0,
    )
    approved = services.carts.confirm_cart(
        review.review_id, 0, str(uuid.uuid4()), current_scene_revision=0
    )
    written, _ = services.carts.write_cart_once(approved)
    real_url = written.cart_reference.checkout_url
    assert real_url, "the fake store should have produced a URL"

    client = TestClient(app)
    body = client.get("/api/state", params={"session_id": session_id})
    assert body.status_code == 200
    payload = body.json()

    # The cart is reported, but the link is not.
    assert payload["cart_review"] is not None
    assert payload["cart_review"]["cart_reference"] is not None
    assert payload["cart_review"]["cart_reference"]["checkout_url"] is None
    assert real_url not in body.text
    assert "/cart/c/" not in body.text

    # And the stored record still has it, so checkout can still release it.
    stored = services.db.get_cart_review(written.review_id)
    assert stored["cart_reference"]["checkout_url"] == real_url


def test_the_checkout_route_still_releases_the_url(
    live_settings: Settings, db, monkeypatch
) -> None:
    """Redacting /api/state must not break the legitimate handoff."""
    import backend.services as services_module
    from fastapi.testclient import TestClient
    from contracts.interfaces import BudgetLine
    from backend.app import app

    services = build_services(live_settings, db)
    services.catalog._storefront = FakeStorefront()
    monkeypatch.setattr(services_module, "_services", services)

    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    services.db.create_session(session_id, "shopper_state")
    review = services.carts.build_cart_review(
        session_id,
        "shopper_state",
        [
            BudgetLine(
                variant_id=WALNUT_DESK,
                quantity=1,
                unit_price=Money(amount_minor=0, currency="USD"),
            )
        ],
        scene_revision=0,
        brief_revision=0,
    )
    approved = services.carts.confirm_cart(
        review.review_id, 0, str(uuid.uuid4()), current_scene_revision=0
    )
    services.carts.write_cart_once(approved)

    response = TestClient(app).post(
        "/api/checkout/continue",
        json={
            "session_id": session_id,
            "review_id": review.review_id,
            "action_id": str(uuid.uuid4()),
            "fit_status": "fits",
        },
    )
    assert response.status_code == 200
    assert response.json()["checkout_url"], "the handoff must still work"
