"""The HTTP surface, end to end on mocks: photo to checkout link.

These exercise the real routes rather than the services directly, so the
error envelope, the 409 behaviour and the reconnect path are all covered as a
client would see them.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

import backend.services as services_module
from backend.app import app
from backend.services import Services
from tests.backend.conftest import make_room_payload

pytestmark = pytest.mark.backend

WALNUT_DESK = "gid://shopify/ProductVariant/49000000000001"
MONITOR = "gid://shopify/ProductVariant/49000000000004"
OUT_OF_STOCK = "gid://shopify/ProductVariant/49000000000011"
SHOP_MCP_DESK = "gid://shopify/ProductVariant/54307265806649"


@pytest.fixture
def client(services: Services, monkeypatch) -> TestClient:
    """A client wired to an isolated in-memory backend."""
    monkeypatch.setattr(services_module, "_services", services)
    return TestClient(app)


def new_session(client: TestClient) -> str:
    response = client.post("/api/session", json={})
    assert response.status_code == 200
    return response.json()["session_id"]


def confirm_room(client: TestClient, session_id: str, base: int = 0) -> dict[str, Any]:
    response = client.post(
        "/api/room/confirm",
        json={
            "session_id": session_id,
            "room_spec": make_room_payload(),
            "base_revision": base,
            "action_id": str(uuid.uuid4()),
        },
    )
    return response


# ==========================================================================
# Shape of the API
# ==========================================================================


def test_every_contracted_route_is_registered() -> None:
    paths = set(app.openapi()["paths"].keys())
    for path in (
        "/api/session",
        "/api/room/analyze",
        "/api/room/confirm",
        "/api/state",
        "/api/command",
        "/api/scene/patch",
        "/api/input/respond",
        "/api/cart/review",
        "/api/cart/confirm",
        "/api/checkout/continue",
        "/api/agora/token",
    ):
        assert path in paths, f"{path} is not registered"


def test_health_reports_the_real_state(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["cart_writes"] == "disabled"
    assert "MOCK_COMMERCE" in body["mocks_on"]


def test_health_never_leaks_a_secret(client: TestClient) -> None:
    text = client.get("/api/health").text.lower()
    for forbidden in ("token", "api_key", "secret", "certificate", "password"):
        assert forbidden not in text


def test_errors_always_use_the_standard_envelope(client: TestClient) -> None:
    from contracts.common import ErrorResponse

    response = client.get("/api/state", params={"session_id": "nope"})
    assert response.status_code == 404
    ErrorResponse.model_validate(response.json())
    assert set(response.json()) == {"code", "message", "retryable", "details"}


def test_agora_token_is_labelled_mock(client: TestClient) -> None:
    body = client.get("/api/agora/token", params={"session_id": "x"}).json()
    assert body["source_label"] == "mock"
    assert body["token"] is None


def test_an_unknown_route_returns_no_stack_trace(client: TestClient) -> None:
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert "Traceback" not in response.text


# ==========================================================================
# Session, room, state
# ==========================================================================


def test_a_session_starts_at_revision_zero(client: TestClient) -> None:
    body = client.post("/api/session", json={}).json()
    assert body["room_revision"] == 0
    assert body["scene_revision"] == 0


def test_analysis_requires_consent(client: TestClient) -> None:
    session_id = new_session(client)
    response = client.post(
        "/api/room/analyze", json={"session_id": session_id, "consent_given": False}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "consent_required"


def test_analysis_returns_a_labelled_draft_and_its_validation(
    client: TestClient,
) -> None:
    session_id = new_session(client)
    body = client.post(
        "/api/room/analyze", json={"session_id": session_id, "consent_given": True}
    ).json()
    assert body["source_label"] == "mock"
    assert body["analysis"]["room_spec_draft"] is not None
    assert body["validation"]["ok"] is True


def test_analysis_does_not_commit_anything(client: TestClient) -> None:
    """A draft is a proposal. Only /api/room/confirm writes."""
    session_id = new_session(client)
    client.post("/api/room/analyze", json={"session_id": session_id, "consent_given": True})
    state = client.get("/api/state", params={"session_id": session_id}).json()
    assert state["room_revision"] == 0
    assert state["room_spec"] is None


def test_confirming_a_room_commits_revision_one(client: TestClient) -> None:
    session_id = new_session(client)
    response = confirm_room(client, session_id)
    assert response.status_code == 200
    assert response.json()["revision"] == 1


def test_a_stale_room_confirm_returns_409_with_the_latest(client: TestClient) -> None:
    session_id = new_session(client)
    confirm_room(client, session_id)
    response = confirm_room(client, session_id, base=0)
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "stale_base_revision"
    assert body["retryable"] is True
    assert body["details"]["latest_revision"] == 1


def test_an_invalid_room_returns_422_and_keeps_the_old_one(client: TestClient) -> None:
    session_id = new_session(client)
    confirm_room(client, session_id)

    bad = make_room_payload(width=3.2)
    bad["openings"][0]["offset_m"] = 3.0
    response = client.post(
        "/api/room/confirm",
        json={
            "session_id": session_id,
            "room_spec": bad,
            "base_revision": 1,
            "action_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "room_invalid"

    state = client.get("/api/state", params={"session_id": session_id}).json()
    assert state["room_revision"] == 1
    assert state["room_spec"]["dimensions_m"]["width_m"]["value_m"] == 4.0


def test_state_is_everything_a_reconnecting_client_needs(client: TestClient) -> None:
    session_id = new_session(client)
    confirm_room(client, session_id)
    state = client.get("/api/state", params={"session_id": session_id}).json()
    for key in ("room_spec", "scene", "cart_review", "events", "room_revision"):
        assert key in state
    assert any(e["type"] == "room_committed" for e in state["events"])


def test_state_accepts_since_revision(client: TestClient) -> None:
    session_id = new_session(client)
    confirm_room(client, session_id)
    response = client.get(
        "/api/state", params={"session_id": session_id, "since_revision": 0}
    )
    assert response.status_code == 200


# ==========================================================================
# Catalog
# ==========================================================================


def test_catalog_search_separates_shoppable_from_discovery(client: TestClient) -> None:
    body = client.get("/api/catalog/search", params={"limit": 50}).json()
    assert body["shoppable_count"] > 0
    assert body["discovery_count"] > 0
    assert "never be added to the cart" in body["discovery_note"]


def test_every_discovery_offer_is_flagged_unshoppable(client: TestClient) -> None:
    body = client.get("/api/catalog/search", params={"limit": 50}).json()
    for offer in body["offers"]:
        if offer["store"]["api_surface"] == "shop_mcp":
            assert offer["shoppable"] is False


def test_catalog_search_can_return_only_shoppable_offers(client: TestClient) -> None:
    body = client.get(
        "/api/catalog/search", params={"surface": "storefront_api", "limit": 50}
    ).json()
    assert body["discovery_count"] == 0
    assert body["shoppable_count"] > 0


# ==========================================================================
# Scene patches
# ==========================================================================


def add_desk(client: TestClient, session_id: str, base: int = 0, x: float = 2.0):
    return client.post(
        "/api/scene/patch",
        json={
            "session_id": session_id,
            "base_revision": base,
            "action_id": str(uuid.uuid4()),
            "patch": {
                "actions": [
                    {
                        "type": "add_object",
                        "object_id": "obj_desk",
                        "payload": {
                            "object_id": "obj_desk",
                            "label": "Walnut desk",
                            "x_m": x,
                            "y_m": 0.5,
                            "width_m": 1.2,
                            "depth_m": 0.6,
                            "product_id": "gid://shopify/Product/9000000000001",
                            "variant_id": WALNUT_DESK,
                            "store_domain": "ethyneon-dev.myshopify.com",
                            "offer_ref": "offer_dev_desk_walnut",
                            "asset_id": "asset_desk_rect",
                        },
                    }
                ]
            },
        },
    )


def test_a_scene_patch_commits(client: TestClient) -> None:
    session_id = new_session(client)
    confirm_room(client, session_id)
    response = add_desk(client, session_id)
    assert response.status_code == 200
    assert response.json()["revision"] == 1


def test_a_scene_patch_before_a_room_is_refused(client: TestClient) -> None:
    session_id = new_session(client)
    response = add_desk(client, session_id)
    assert response.status_code == 400
    assert response.json()["code"] == "no_room_yet"


def test_a_stale_scene_patch_returns_409(client: TestClient) -> None:
    session_id = new_session(client)
    confirm_room(client, session_id)
    add_desk(client, session_id)
    response = add_desk(client, session_id, base=0)
    assert response.status_code == 409
    assert response.json()["details"]["latest_revision"] == 1


def test_a_patch_with_an_unverified_variant_is_refused(client: TestClient) -> None:
    session_id = new_session(client)
    confirm_room(client, session_id)
    response = client.post(
        "/api/scene/patch",
        json={
            "session_id": session_id,
            "base_revision": 0,
            "action_id": str(uuid.uuid4()),
            "patch": {
                "actions": [
                    {
                        "type": "add_object",
                        "object_id": "obj_x",
                        "payload": {
                            "object_id": "obj_x",
                            "x_m": 1.0,
                            "y_m": 1.0,
                            "product_id": "p",
                            "variant_id": "gid://shopify/ProductVariant/not-real",
                            "store_domain": "ethyneon-dev.myshopify.com",
                            "offer_ref": "nope",
                        },
                    }
                ]
            },
        },
    )
    assert response.status_code == 422
    codes = {e["code"] for e in response.json()["details"]["errors"]}
    assert "unverified_variant" in codes


# ==========================================================================
# The full journey
# ==========================================================================


def test_photo_to_checkout_link_on_mocks(client: TestClient) -> None:
    """The e2e replay: consent, draft, confirm, place, review, approve, audit."""
    session_id = new_session(client)

    analysis = client.post(
        "/api/room/analyze", json={"session_id": session_id, "consent_given": True}
    )
    assert analysis.status_code == 200

    assert confirm_room(client, session_id).status_code == 200
    assert add_desk(client, session_id).status_code == 200

    review = client.post(
        "/api/cart/review",
        json={
            "session_id": session_id,
            "selections": [
                {"variant_id": WALNUT_DESK, "quantity": 1},
                {"variant_id": MONITOR, "quantity": 1},
            ],
            "budget_minor": 120000,
        },
    )
    assert review.status_code == 200, review.text
    review_body = review.json()
    assert review_body["merchandise_subtotal"]["amount_minor"] == 74800
    assert review_body["within_budget"] is True

    confirmed = client.post(
        "/api/cart/confirm",
        json={
            "session_id": session_id,
            "review_id": review_body["review_id"],
            "base_revision": 1,
            "action_id": str(uuid.uuid4()),
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "approved"

    checkout = client.post(
        "/api/checkout/continue",
        json={
            "session_id": session_id,
            "review_id": review_body["review_id"],
            "action_id": str(uuid.uuid4()),
            "fit_status": "fits",
        },
    )
    assert checkout.status_code == 200, checkout.text
    body = checkout.json()
    assert body["audit"]["outcome"] == "ready"
    assert "not payment" in body["note"]

    # No live cart exists on mocks, so there is no URL to release. The audit
    # passing is the thing under test; the URL arrives with a real store.
    assert body["checkout_url"] is None

    state = client.get("/api/state", params={"session_id": session_id}).json()
    types = {e["type"] for e in state["events"]}
    assert "room_committed" in types
    assert "scene_committed" in types
    assert "checkout_checks_passed" in types


def test_an_unavailable_variant_is_refused_at_the_review_route(
    client: TestClient,
) -> None:
    session_id = new_session(client)
    response = client.post(
        "/api/cart/review",
        json={"session_id": session_id, "selections": [{"variant_id": OUT_OF_STOCK}]},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "no_shoppable_lines"


def test_a_discovery_variant_is_refused_at_the_review_route(
    client: TestClient,
) -> None:
    session_id = new_session(client)
    response = client.post(
        "/api/cart/review",
        json={"session_id": session_id, "selections": [{"variant_id": SHOP_MCP_DESK}]},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "no_shoppable_lines"


def test_a_failing_fit_blocks_the_checkout_route(client: TestClient) -> None:
    session_id = new_session(client)
    confirm_room(client, session_id)
    review = client.post(
        "/api/cart/review",
        json={"session_id": session_id, "selections": [{"variant_id": WALNUT_DESK}]},
    ).json()
    client.post(
        "/api/cart/confirm",
        json={
            "session_id": session_id,
            "review_id": review["review_id"],
            "base_revision": 0,
            "action_id": str(uuid.uuid4()),
        },
    )
    checkout = client.post(
        "/api/checkout/continue",
        json={
            "session_id": session_id,
            "review_id": review["review_id"],
            "action_id": str(uuid.uuid4()),
            "fit_status": "does_not_fit",
        },
    ).json()
    assert checkout["audit"]["outcome"] == "blocked"
    assert checkout["checkout_url"] is None


# ==========================================================================
# The events socket
# ==========================================================================


def test_the_events_socket_connects_and_receives_a_live_event(
    client: TestClient,
) -> None:
    session_id = new_session(client)
    with client.websocket_connect(f"/ws/events?session_id={session_id}") as ws:
        assert ws.receive_json()["type"] == "connected"
        confirm_room(client, session_id)
        message = ws.receive_json()
        assert message["type"] == "room_committed"
        assert message["revision_refs"]["room_revision"] == 1


def test_events_are_persisted_even_with_no_socket_listening(
    client: TestClient,
) -> None:
    """A dropped client must not lose events."""
    session_id = new_session(client)
    confirm_room(client, session_id)
    state = client.get("/api/state", params={"session_id": session_id}).json()
    assert any(e["type"] == "room_committed" for e in state["events"])
