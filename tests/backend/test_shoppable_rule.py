"""The shoppable rule: what may be placed, and what may be bought.

The rule in full:

  a scene object that is shoppable must reference a verified CatalogOffer,
  meaning an exact Shopify variant ID, from the configured store, available
  for sale;

  a scene commit that places a shoppable item with no verified variant is
  rejected in the standard error shape;

  a generic stand-in carries a not-for-sale signal and can never appear in a
  CartReview.

These tests cover the parts that hold today. One part does not: the scene
validator checks that a variant is *known* but not that it is *shoppable*,
so a Shop MCP discovery variant is currently accepted into a scene. That gap
is reported to Person C with a proposed fix, and the tests for it ship with
the fix rather than being asserted here as if they passed.
"""

from __future__ import annotations

import uuid

import pytest

from contracts.catalog_offer import (
    Availability,
    CatalogOffer,
    Freshness,
    ProductDimensions,
    StoreRef,
)
from contracts.common import Money
from contracts.interfaces import BudgetLine, SearchRequirements
from contracts.scene_revision import ObjectPlacement, SceneRevision, VariantSelection
from backend.cart import CartError
from backend.services import Services
from tests.backend.conftest import make_room_payload

pytestmark = [pytest.mark.backend, pytest.mark.commerce]

WALNUT_DESK = "gid://shopify/ProductVariant/49000000000001"
OUT_OF_STOCK = "gid://shopify/ProductVariant/49000000000011"
SHOP_MCP_DESK = "gid://shopify/ProductVariant/54307265806649"
DEV_STORE = "ethyneon-dev.myshopify.com"


def add_object_patch(**payload_overrides):
    payload = {
        "object_id": "obj_desk",
        "label": "Walnut desk",
        "x_m": 2.0,
        "y_m": 0.5,
        "width_m": 1.2,
        "depth_m": 0.6,
        "product_id": "gid://shopify/Product/9000000000001",
        "variant_id": WALNUT_DESK,
        "store_domain": DEV_STORE,
        "offer_ref": "offer_dev_desk_walnut",
        "asset_id": "asset_desk_rect",
    }
    payload.update(payload_overrides)
    return {"actions": [{"type": "add_object", "object_id": "obj_desk", "payload": payload}]}


def commit_scene(services: Services, session_id: str, patch, base: int = 0):
    known = {
        offer.variant_id
        for offer in services.catalog.search_catalog(SearchRequirements(), limit=200)
    }
    return services.revisions.commit_revision(
        "scene",
        patch,
        base,
        str(uuid.uuid4()),
        session_id=session_id,
        known_variant_ids=known,
        known_asset_ids={"asset_desk_rect"},
    )


# ==========================================================================
# A shoppable scene object needs a variant
# ==========================================================================


def test_a_shoppable_placement_with_no_variant_id_is_rejected(
    services: Services, session_id: str, committed_room
) -> None:
    patch = add_object_patch()
    del patch["actions"][0]["payload"]["variant_id"]

    result = commit_scene(services, session_id, patch)

    assert result.committed is False
    codes = {error.code for error in result.validation.errors}
    assert "variant_required" in codes


def test_a_variant_with_no_verified_offer_is_rejected(
    services: Services, session_id: str, committed_room
) -> None:
    result = commit_scene(
        services,
        session_id,
        add_object_patch(variant_id="gid://shopify/ProductVariant/invented"),
    )

    assert result.committed is False
    errors = {e.code: e for e in result.validation.errors}
    assert "unverified_variant" in errors
    assert "no verified CatalogOffer" in errors["unverified_variant"].message


def test_a_verified_variant_is_accepted(
    services: Services, session_id: str, committed_room
) -> None:
    result = commit_scene(services, session_id, add_object_patch())
    assert result.committed is True
    assert result.scene.placements[0].variant.variant_id == WALNUT_DESK


def test_the_rejection_uses_the_standard_error_shape(
    services: Services, monkeypatch
) -> None:
    """Through the route, as a client would see it."""
    import backend.services as services_module
    from fastapi.testclient import TestClient

    from contracts.common import ErrorResponse
    from backend.app import app

    monkeypatch.setattr(services_module, "_services", services)
    client = TestClient(app)

    session_id = client.post("/api/session", json={}).json()["session_id"]
    client.post(
        "/api/room/confirm",
        json={
            "session_id": session_id,
            "room_spec": make_room_payload(),
            "base_revision": 0,
            "action_id": str(uuid.uuid4()),
        },
    )

    response = client.post(
        "/api/scene/patch",
        json={
            "session_id": session_id,
            "base_revision": 0,
            "action_id": str(uuid.uuid4()),
            "patch": add_object_patch(variant_id="gid://shopify/ProductVariant/invented"),
        },
    )

    assert response.status_code == 422
    body = response.json()
    ErrorResponse.model_validate(body)
    assert set(body) == {"code", "message", "retryable", "details"}
    assert body["code"] == "patch_rejected"
    assert body["retryable"] is False
    assert "unverified_variant" in {e["code"] for e in body["details"]["errors"]}


def test_an_unknown_asset_is_also_rejected(
    services: Services, session_id: str, committed_room
) -> None:
    result = commit_scene(services, session_id, add_object_patch(asset_id="asset_invented"))
    assert result.committed is False
    assert "unknown_asset" in {e.code for e in result.validation.errors}


# ==========================================================================
# Verified means: this store, this exact variant, available
# ==========================================================================


def test_only_a_storefront_offer_is_ever_shoppable() -> None:
    """Enforced by the model itself, not by a caller remembering to check."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as excinfo:
        CatalogOffer(
            offer_id="offer_x",
            store=StoreRef(store_domain="other.myshopify.com", api_surface="shop_mcp"),
            product_id="gid://shopify/Product/1",
            variant_id="gid://shopify/ProductVariant/1",
            title="Desk",
            price=Money(amount_minor=1000, currency="USD"),
            availability=Availability(available=True),
            listing_url="https://other.example/p",
            shoppable=True,
        )
    assert "never be shoppable" in str(excinfo.value)


def test_every_shoppable_offer_in_the_catalog_is_from_the_configured_store(
    services: Services,
) -> None:
    offers = services.catalog.search_catalog(SearchRequirements(), limit=200)
    for offer in offers:
        if offer.shoppable:
            assert offer.store.api_surface == "storefront_api"


def test_product_and_variant_ids_stay_separate(services: Services) -> None:
    """An exact variant, not just a product."""
    offers = services.catalog.search_catalog(SearchRequirements(), limit=200)
    assert offers
    for offer in offers:
        assert offer.product_id != offer.variant_id
        assert "ProductVariant" in offer.variant_id


def test_an_unavailable_variant_cannot_be_bought(
    services: Services, session_id: str
) -> None:
    with pytest.raises(CartError) as excinfo:
        services.carts.build_cart_review(
            session_id,
            "shopper_test",
            [
                BudgetLine(
                    variant_id=OUT_OF_STOCK,
                    quantity=1,
                    unit_price=Money(amount_minor=0, currency="USD"),
                )
            ],
            scene_revision=0,
            brief_revision=0,
        )
    assert excinfo.value.code == "no_shoppable_lines"
    assert "unavailable" in str(excinfo.value.details["rejected"][0]["reason"])


def test_a_discovery_variant_cannot_be_bought(
    services: Services, session_id: str
) -> None:
    """Whatever happens in the scene, the cart refuses the discovery surface."""
    with pytest.raises(CartError) as excinfo:
        services.carts.build_cart_review(
            session_id,
            "shopper_test",
            [
                BudgetLine(
                    variant_id=SHOP_MCP_DESK,
                    quantity=1,
                    unit_price=Money(amount_minor=0, currency="USD"),
                )
            ],
            scene_revision=0,
            brief_revision=0,
        )
    assert excinfo.value.code == "no_shoppable_lines"
    reason = excinfo.value.details["rejected"][0]["reason"]
    assert "discovery" in reason or "never reach a cart" in reason


def test_a_cart_review_cannot_be_built_on_the_discovery_surface() -> None:
    """A second, independent guard at the model level."""
    from pydantic import ValidationError

    from contracts.cart_review import CartLine, CartReview
    from contracts.common import utc_now

    with pytest.raises(ValidationError) as excinfo:
        CartReview(
            review_id="cart_x",
            session_id="s",
            shopper_id="shopper",
            store=StoreRef(store_domain="other.myshopify.com", api_surface="shop_mcp"),
            lines=[
                CartLine(
                    line_id="l1",
                    product_id="gid://shopify/Product/1",
                    variant_id="gid://shopify/ProductVariant/1",
                    title="Desk",
                    quantity=1,
                    unit_price=Money(amount_minor=1000, currency="USD"),
                    line_subtotal=Money(amount_minor=1000, currency="USD"),
                    offer_ref="offer_x",
                    offer_fetched_at=utc_now(),
                )
            ],
            merchandise_subtotal=Money(amount_minor=1000, currency="USD"),
            brief_revision=0,
            scene_revision=0,
        )
    assert "shop_mcp" in str(excinfo.value)


# ==========================================================================
# Generic stand-ins
# ==========================================================================


def test_a_stand_in_has_no_variant_and_is_marked_representative() -> None:
    """The shopper's own furniture, or a placeholder. Not for sale."""
    stand_in = ObjectPlacement(
        object_id="obj_existing_monitor",
        label="Shopper's own monitor",
        pose={"x_m": 1.0, "y_m": 1.0},
        footprint={"x_m": 1.0, "y_m": 1.0, "width_m": 0.6, "depth_m": 0.25},
        locked=True,
    )
    assert stand_in.variant is None
    assert stand_in.asset_is_representative is True


def test_a_stand_in_is_valid_in_a_scene(
    services: Services, session_id: str, committed_room
) -> None:
    """Keeping your own desk must not require buying one."""
    scene = SceneRevision(
        scene_revision=1,
        session_id=session_id,
        room_revision=1,
        placements=[
            ObjectPlacement(
                object_id="obj_own_chair",
                label="Shopper's own chair",
                pose={"x_m": 1.0, "y_m": 1.0},
                footprint={"x_m": 1.0, "y_m": 1.0, "width_m": 0.6, "depth_m": 0.6},
            )
        ],
    )
    assert scene.placements[0].variant is None
    assert scene.selected_variants == []


def test_a_stand_in_can_never_appear_in_a_cart_review(
    services: Services, session_id: str
) -> None:
    """There is no variant to select, so no line can be built from it."""
    with pytest.raises(CartError):
        services.carts.build_cart_review(
            session_id,
            "shopper_test",
            [
                BudgetLine(
                    variant_id="obj_own_chair",
                    quantity=1,
                    unit_price=Money(amount_minor=0, currency="USD"),
                )
            ],
            scene_revision=0,
            brief_revision=0,
        )


def test_only_placements_with_a_variant_reach_selected_variants(
    services: Services, session_id: str, committed_room
) -> None:
    """selected_variants is what a cart would be built from, and a stand-in
    is absent from it by construction."""
    result = commit_scene(services, session_id, add_object_patch())
    assert result.committed

    scene = result.scene
    assert len(scene.selected_variants) == 1
    assert scene.selected_variants[0].variant_id == WALNUT_DESK

    for variant in scene.selected_variants:
        assert variant.variant_id
        assert variant.offer_ref


def test_every_cart_line_traces_back_to_a_verified_offer(
    services: Services, session_id: str, desk_selection
) -> None:
    review = services.carts.build_cart_review(
        session_id, "shopper_test", desk_selection, scene_revision=0, brief_revision=0
    )
    for line in review.lines:
        offer = services.catalog.get_offer(line.variant_id)
        assert offer.shoppable is True
        assert offer.availability.available is True
        assert offer.variant_id == line.variant_id
        assert line.offer_ref == offer.offer_id


# ==========================================================================
# Verified means shoppable: this store, this variant, available
# ==========================================================================


@pytest.fixture
def route_client(services: Services, monkeypatch):
    """A client on an isolated backend, with a room already confirmed."""
    import backend.services as services_module
    from fastapi.testclient import TestClient

    from backend.app import app

    monkeypatch.setattr(services_module, "_services", services)
    client = TestClient(app)
    session_id = client.post("/api/session", json={}).json()["session_id"]
    client.post(
        "/api/room/confirm",
        json={
            "session_id": session_id,
            "room_spec": make_room_payload(),
            "base_revision": 0,
            "action_id": str(uuid.uuid4()),
        },
    )
    return client, session_id


def post_patch(client, session_id: str, patch, base: int = 0):
    return client.post(
        "/api/scene/patch",
        json={
            "session_id": session_id,
            "base_revision": base,
            "action_id": str(uuid.uuid4()),
            "patch": patch,
        },
    )


def test_a_discovery_variant_cannot_enter_the_scene(route_client) -> None:
    """Another merchant's product must never become a scene placement.

    It cannot be bought either way, but a placement carrying a
    VariantSelection reads as purchasable, and that is the confusion the
    rule exists to prevent.
    """
    client, session_id = route_client

    response = post_patch(
        client,
        session_id,
        add_object_patch(
            object_id="obj_discovery",
            label="Discovery desk",
            product_id="gid://shopify/Product/10300986753337",
            variant_id=SHOP_MCP_DESK,
            store_domain="mygarvee.myshopify.com",
            offer_ref="offer_mcp_garvee_folding_desk",
        ),
    )

    assert response.status_code == 422
    codes = {e["code"] for e in response.json()["details"]["errors"]}
    assert "unverified_variant" in codes

    # And nothing was committed.
    state = client.get("/api/state", params={"session_id": session_id}).json()
    assert state["scene"] is None or state["scene"]["placements"] == []


def test_an_unavailable_variant_cannot_enter_the_scene(route_client) -> None:
    """Out of stock is not placeable, for the same reason."""
    client, session_id = route_client

    response = post_patch(
        client,
        session_id,
        add_object_patch(
            object_id="obj_oos",
            label="Sold out desk",
            product_id="gid://shopify/Product/9000000000011",
            variant_id=OUT_OF_STOCK,
            offer_ref="offer_dev_desk_walnut_oos",
        ),
    )

    assert response.status_code == 422
    codes = {e["code"] for e in response.json()["details"]["errors"]}
    assert "unverified_variant" in codes


def test_a_rejected_discovery_variant_uses_the_standard_error_shape(
    route_client,
) -> None:
    from contracts.common import ErrorResponse

    client, session_id = route_client
    response = post_patch(
        client,
        session_id,
        add_object_patch(
            object_id="obj_discovery",
            variant_id=SHOP_MCP_DESK,
            store_domain="mygarvee.myshopify.com",
            offer_ref="offer_mcp_garvee_folding_desk",
        ),
    )

    assert response.status_code == 422
    body = response.json()
    ErrorResponse.model_validate(body)
    assert set(body) == {"code", "message", "retryable", "details"}
    assert body["code"] == "patch_rejected"
    assert body["retryable"] is False
    assert isinstance(body["details"]["errors"], list)
    error = next(
        e for e in body["details"]["errors"] if e["code"] == "unverified_variant"
    )
    assert error["field_path"].endswith("payload.variant_id")
    assert "no verified CatalogOffer" in error["message"]


def test_a_shoppable_available_variant_still_goes_in(route_client) -> None:
    """The narrowing must not block the ordinary case."""
    client, session_id = route_client
    response = post_patch(client, session_id, add_object_patch())
    assert response.status_code == 200, response.text
    assert response.json()["scene"]["placements"][0]["variant"]["variant_id"] == WALNUT_DESK
