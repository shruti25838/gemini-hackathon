"""The cart-write gates, checked through the services as the smoke script builds them.

These exist because of a real failure. `backend/catalog.py` hardcoded
`allow_cart_writes=False` when constructing the Storefront client, so both
policy gates could open while the client itself stayed shut, and the live
smoke test died at the write with a misleading "cart writes are disabled".

Every other cart-write test built the client by hand and passed the flag
directly, so none of them touched the path the real application uses. These
build the services exactly as `scripts/live_cart_smoke.py` does.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

import pytest

from contracts.cart_review import CartReview
from contracts.common import Money
from contracts.interfaces import BudgetLine
from backend.cart_write import CartWriteRefused
from backend.config import Settings
from backend.db import Database
from backend.services import Services, build_services
from tests.backend.test_live_cart_write import MONITOR, WALNUT_DESK, FakeStorefront

pytestmark = [pytest.mark.backend, pytest.mark.commerce]

DEV_STORE = "ethyneon.myshopify.com"
OTHER_STORE = "someones-real-shop.myshopify.com"


def make_settings(**overrides: Any) -> Settings:
    """What the smoke script runs with: live commerce, mocked everything else."""
    base: dict[str, Any] = {
        "_env_file": None,
        "MOCK_AI": True,
        "MOCK_COMMERCE": False,
        "MOCK_ROOM_INPUT": True,
        "MOCK_AGORA": True,
        "SHOP_MCP": False,
        "GOOGLE_API_KEY": None,
        "GEMINI_API_KEY": None,
        "SHOPIFY_STORE_DOMAIN": DEV_STORE,
        "SHOPIFY_STOREFRONT_TOKEN": "placeholder-never-sent",
        "ALLOWED_CART_STORE_DOMAINS": DEV_STORE,
        "LIVE_CART_WRITES": True,
    }
    base.update(overrides)
    return Settings(**base)


def build(settings: Settings) -> Services:
    return build_services(settings, Database(":memory:"))


def approved_review_for(services: Services, offers: list[Any]) -> CartReview:
    """An approved review built against whatever the fake catalog returns."""
    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    services.db.create_session(session_id, "shopper_gate")
    review = services.carts.build_cart_review(
        session_id,
        "shopper_gate",
        [
            BudgetLine(
                variant_id=offers[0],
                quantity=1,
                unit_price=Money(amount_minor=0, currency="USD"),
            )
        ],
        scene_revision=0,
        brief_revision=0,
        budget=Money(amount_minor=1_000_000, currency="USD"),
    )
    return services.carts.confirm_cart(
        review.review_id, 0, str(uuid.uuid4()), current_scene_revision=0
    )


class CatalogFake:
    """A Storefront stand-in that can both serve the catalog and take writes."""

    def __init__(self, allow_cart_writes: bool) -> None:
        self.allow_cart_writes = allow_cart_writes
        self.inner = FakeStorefront()
        self.create_attempts = 0

    # catalog side
    def search_products(self, *, first: int = 20, query: Optional[str] = None):
        return []

    def get_variant(self, variant_id: str):
        from datetime import datetime, timezone

        from contracts.catalog_offer import (
            Availability,
            CatalogOffer,
            Freshness,
            ProductDimensions,
            StoreRef,
        )

        return CatalogOffer(
            offer_id=f"offer_{variant_id.rsplit('/', 1)[-1]}",
            store=StoreRef(store_domain=DEV_STORE, api_surface="storefront_api"),
            product_id="gid://shopify/Product/1",
            variant_id=variant_id,
            title="Walnut Gaming Desk",
            price=Money(amount_minor=42900, currency="USD"),
            availability=Availability(available=True, checked_at=datetime.now(timezone.utc)),
            listing_url=f"https://{DEV_STORE}/products/walnut",
            dimensions=ProductDimensions(
                width_m=1.4, depth_m=0.6, height_m=0.75, evidence_status="confirmed"
            ),
            dimension_source="metafield",
            freshness=Freshness(source_label="live"),
            shoppable=True,
        )

    # write side, mirroring the real client's own guard
    def create_cart(self, lines):
        self.create_attempts += 1
        if not self.allow_cart_writes:
            from backend.shopify import ShopifyError

            raise ShopifyError(
                "live cart writes are disabled. Set allow_cart_writes to enable them.",
                retryable=False,
                code="cart_writes_disabled",
            )
        return self.inner.create_cart(lines)

    def add_cart_lines(self, cart_id, lines):
        return self.inner.add_cart_lines(cart_id, lines)

    def get_cart(self, cart_id):
        return self.inner.get_cart(cart_id)


# ==========================================================================
# One place decides
# ==========================================================================


def test_the_client_flag_follows_the_two_gates() -> None:
    """The regression. Settings said yes, the client said no."""
    settings = make_settings()
    services = build(settings)
    assert settings.cart_writes_allowed is True
    assert services.catalog.storefront.allow_cart_writes is True


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({}, True),
        ({"LIVE_CART_WRITES": False}, False),
        ({"SHOPIFY_STORE_DOMAIN": OTHER_STORE}, False),
        ({"LIVE_CART_WRITES": False, "SHOPIFY_STORE_DOMAIN": OTHER_STORE}, False),
    ],
)
def test_the_two_sources_can_never_disagree(overrides: dict, expected: bool) -> None:
    """Whatever the combination, the client and the policy must agree."""
    settings = make_settings(**overrides)
    services = build(settings)
    assert settings.cart_writes_allowed is expected
    assert services.catalog.storefront.allow_cart_writes is expected


def test_a_blank_allowlist_falls_back_to_the_configured_default() -> None:
    """Worth stating, because it is not what you might assume.

    A blank value in .env means "not configured", so the default allowlist
    applies rather than becoming empty. Blanking this is NOT a way to turn
    writes off. The off switch is LIVE_CART_WRITES=0, which is the default.
    """
    settings = make_settings(ALLOWED_CART_STORE_DOMAINS="")
    assert settings.cart_store_allowlist == [DEV_STORE]
    assert settings.cart_writes_allowed is True

    off = make_settings(ALLOWED_CART_STORE_DOMAINS="", LIVE_CART_WRITES=False)
    assert off.cart_writes_allowed is False


def test_a_mocked_catalog_has_no_client_to_write_with() -> None:
    settings = make_settings(MOCK_COMMERCE=True)
    assert build(settings).catalog.storefront is None


# ==========================================================================
# (a) Both gates open: the write reaches the transport
# ==========================================================================


def test_with_both_gates_open_the_write_reaches_the_transport() -> None:
    settings = make_settings()
    services = build(settings)
    fake = CatalogFake(allow_cart_writes=settings.cart_writes_allowed)
    services.catalog._storefront = fake

    review = approved_review_for(services, [WALNUT_DESK])
    written, reconciliation = services.carts.write_cart_once(review)

    assert fake.create_attempts == 1, "the write never reached the transport"
    assert fake.inner.creates, "cartCreate was not called"
    assert reconciliation.matches is True
    assert written.cart_reference.write_outcome == "confirmed"


def test_the_whole_smoke_sequence_runs_with_both_gates_open() -> None:
    """Catalog read, review, approve, write, reconcile, audit, release."""
    settings = make_settings()
    services = build(settings)
    services.catalog._storefront = CatalogFake(
        allow_cart_writes=settings.cart_writes_allowed
    )

    review = approved_review_for(services, [WALNUT_DESK])
    written, reconciliation = services.carts.write_cart_once(review)
    assert reconciliation.matches

    audit, url = services.checkout.continue_checkout(
        written.review_id, str(uuid.uuid4()), current_scene_revision=0, fit_status="fits"
    )
    assert audit.outcome == "ready"
    assert url is not None and url.startswith("https://")


# ==========================================================================
# (b) LIVE_CART_WRITES off
# ==========================================================================


def test_with_the_flag_off_the_write_is_refused_before_the_transport() -> None:
    settings = make_settings(LIVE_CART_WRITES=False)
    services = build(settings)
    fake = CatalogFake(allow_cart_writes=settings.cart_writes_allowed)
    services.catalog._storefront = fake

    review = approved_review_for(services, [WALNUT_DESK])
    with pytest.raises(CartWriteRefused) as excinfo:
        services.carts.write_cart_once(review)

    assert excinfo.value.code == "live_cart_writes_refused"
    assert "LIVE_CART_WRITES is off" in excinfo.value.message
    assert fake.create_attempts == 0, "nothing should have reached the transport"


# ==========================================================================
# (c) Non-allowlisted domain
# ==========================================================================


def test_a_non_allowlisted_domain_is_refused_before_the_transport() -> None:
    settings = make_settings(SHOPIFY_STORE_DOMAIN=OTHER_STORE)
    services = build(settings)
    fake = CatalogFake(allow_cart_writes=settings.cart_writes_allowed)
    services.catalog._storefront = fake

    review = approved_review_for(services, [WALNUT_DESK])
    with pytest.raises(CartWriteRefused) as excinfo:
        services.carts.write_cart_once(review)

    assert excinfo.value.code == "live_cart_writes_refused"
    assert "not on the cart-write allowlist" in excinfo.value.message
    assert OTHER_STORE in excinfo.value.message
    assert fake.create_attempts == 0


def test_the_refusal_names_the_store_but_no_credential() -> None:
    settings = make_settings(
        SHOPIFY_STORE_DOMAIN=OTHER_STORE, SHOPIFY_STOREFRONT_TOKEN="shpat_do_not_leak"
    )
    services = build(settings)
    services.catalog._storefront = CatalogFake(allow_cart_writes=False)

    review = approved_review_for(services, [WALNUT_DESK])
    with pytest.raises(CartWriteRefused) as excinfo:
        services.carts.write_cart_once(review)

    blob = excinfo.value.message + str(excinfo.value.details)
    assert OTHER_STORE in blob
    assert "shpat_do_not_leak" not in blob


# ==========================================================================
# (d) Defence in depth: a client built without the flag still refuses
# ==========================================================================


def test_a_client_built_without_the_flag_still_refuses() -> None:
    from backend.shopify import ShopifyError, ShopifyStorefront

    client = ShopifyStorefront(DEV_STORE, "placeholder", "2026-10")
    assert client.allow_cart_writes is False
    with pytest.raises(ShopifyError) as excinfo:
        client.create_cart([{"merchandiseId": WALNUT_DESK, "quantity": 1}])
    assert excinfo.value.code == "cart_writes_disabled"


def test_a_client_built_without_the_flag_refuses_lines_add_too() -> None:
    from backend.shopify import ShopifyError, ShopifyStorefront

    client = ShopifyStorefront(DEV_STORE, "placeholder", "2026-10")
    with pytest.raises(ShopifyError) as excinfo:
        client.add_cart_lines("gid://shopify/Cart/c1", [{"merchandiseId": WALNUT_DESK, "quantity": 1}])
    assert excinfo.value.code == "cart_writes_disabled"


def test_the_gates_are_rechecked_at_write_time_not_only_at_construction() -> None:
    """A client built permissively is still stopped if policy changed since."""
    settings = make_settings()
    services = build(settings)
    # The client was constructed while the gates were open.
    fake = CatalogFake(allow_cart_writes=True)
    services.catalog._storefront = fake
    review = approved_review_for(services, [WALNUT_DESK])

    # Policy closes afterwards, as it would if .env were edited and reloaded.
    services.catalog.settings = make_settings(LIVE_CART_WRITES=False)

    with pytest.raises(CartWriteRefused):
        services.carts.write_cart_once(review)
    assert fake.create_attempts == 0, "the write-time recheck did not fire"
