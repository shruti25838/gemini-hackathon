"""The backend scaffold: every contracted route exists and errors keep one shape.

Phase 0 routes answer 501. That is deliberate: a scaffolded route that returned
plausible data would be a mock pretending to be live.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import app

pytestmark = pytest.mark.backend

# Every route the team integration contract names, with its owner.
CONTRACTED_ROUTES = [
    ("POST", "/api/session", "C"),
    ("POST", "/api/room/analyze", "C"),
    ("POST", "/api/room/confirm", "C"),
    ("GET", "/api/state", "C"),
    ("POST", "/api/command", "C"),
    ("POST", "/api/scene/patch", "C"),
    ("POST", "/api/input/respond", "C"),
    ("POST", "/api/cart/review", "C"),
    ("POST", "/api/cart/confirm", "C"),
    ("POST", "/api/checkout/continue", "C"),
    ("GET", "/api/agora/token", "B"),
]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_every_contracted_route_is_registered(client: TestClient) -> None:
    paths = set(app.openapi()["paths"].keys())
    for _method, path, owner in CONTRACTED_ROUTES:
        assert path in paths, f"{path} (owner {owner}) is not registered"


def test_both_lane_route_files_are_registered(client: TestClient) -> None:
    """A owns live_ws.py and B owns agora.py; C registers both."""
    assert "/api/agora/token" in app.openapi()["paths"]
    websocket_paths = {
        route.path for route in app.routes if type(route).__name__ == "APIWebSocketRoute"
    }
    # /ws/live arrives through an included router, so check it can be reached.
    with client.websocket_connect("/ws/live?session_id=t") as ws:
        assert ws.receive_json()["type"] == "scaffold_notice"
    assert "/ws/events" in websocket_paths


def test_health_reports_the_real_mock_state(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["mocks_on"], list)
    assert body["commerce"] in {"live", "mock"}


def test_health_never_leaks_a_secret(client: TestClient) -> None:
    body = client.get("/api/health").text.lower()
    for forbidden in ("token", "api_key", "secret", "certificate", "password"):
        assert forbidden not in body


@pytest.mark.parametrize(
    "method,path",
    [(m, p) for m, p, _owner in CONTRACTED_ROUTES if p != "/api/agora/token"],
)
def test_unimplemented_routes_say_so_in_the_standard_error_shape(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path)
    assert response.status_code == 501
    body = response.json()
    assert set(body) == {"code", "message", "retryable", "details"}
    assert body["code"] == "not_implemented_yet"
    assert body["retryable"] is False
    assert "Phase" in body["details"]["phase"]


def test_the_error_envelope_matches_the_contract_model(client: TestClient) -> None:
    from contracts.common import ErrorResponse

    body = client.post("/api/session").json()
    ErrorResponse.model_validate(body)


def test_agora_token_is_labelled_mock_while_mock_agora_is_on(client: TestClient) -> None:
    """A mock must never look live."""
    body = client.get("/api/agora/token?session_id=t&channel=c").json()
    assert body["source_label"] == "mock"
    assert body["token"] is None


def test_events_socket_accepts_and_labels_itself(client: TestClient) -> None:
    with client.websocket_connect("/ws/events?session_id=t") as ws:
        message = ws.receive_json()
        assert message["type"] == "scaffold_notice"
        assert message["source_label"] == "mock"


def test_unknown_route_does_not_return_a_stack_trace(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert "Traceback" not in response.text


def test_stale_revision_error_carries_the_latest_revision() -> None:
    """The 409 shape the integration doc requires, checked directly."""
    from backend.errors import StaleRevisionError

    error = StaleRevisionError(latest_revision=7, kind="scene")
    payload = error.to_response().model_dump(mode="json")
    assert error.status_code == 409
    assert payload["code"] == "stale_base_revision"
    assert payload["retryable"] is True
    assert payload["details"]["latest_revision"] == 7
