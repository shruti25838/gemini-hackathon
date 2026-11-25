"""The standalone lane A demo boots, serves her front end and stays mocked.

The point of keeping her demo in the tree is that it runs. A demo folder that
has quietly stopped importing is worse than no demo folder, because the README
still claims it works. So this imports her app, serves her page and walks the
preset room path, all in process.

It also pins the two properties that keep the demo safe: it defaults to mock
mode, and it does not reach Shopify.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_ROOT = REPO_ROOT / "demos" / "karishma-lane-a"


@pytest.fixture(scope="module")
def demo_app():
    """Import her app with the demo folder on sys.path, then put sys.path back."""
    if not DEMO_ROOT.is_dir():  # pragma: no cover
        pytest.skip("the lane A demo folder is not present")

    sys.path.insert(0, str(DEMO_ROOT))
    try:
        module = importlib.import_module("lane_a_demo.app")
        yield module.app
    finally:
        sys.path.remove(str(DEMO_ROOT))


@pytest.fixture
def client(demo_app):
    with TestClient(demo_app) as test_client:
        yield test_client


# ==========================================================================
# It runs
# ==========================================================================


def test_her_front_end_is_served(client: TestClient) -> None:
    page = client.get("/")

    assert page.status_code == 200
    assert "<title>Ethyneon</title>" in page.text
    assert 'id="viewport"' in page.text, "the three.js viewport should be on the page"
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/main.js").status_code == 200


def test_the_preset_room_builds(client: TestClient) -> None:
    """The path a screenshot would take: no photo, no key, a room on screen."""
    session_id = client.post("/api/session").json()["id"]

    preset = client.post(f"/api/session/{session_id}/preset")
    assert preset.status_code == 200, preset.text

    room = preset.json()["room"]
    assert room["width_m"]["value_m"] == pytest.approx(3.2)
    assert room["depth_m"]["value_m"] == pytest.approx(3.0)
    assert room["openings"], "a preset room needs a door to check against"


def test_the_state_route_answers_for_a_live_session(client: TestClient) -> None:
    session_id = client.post("/api/session").json()["id"]
    client.post(f"/api/session/{session_id}/preset")

    state = client.get(f"/api/session/{session_id}/state").json()

    assert {"room", "scene", "fit", "cart", "audit"} <= set(state)
    assert state["room"]["width_m"]["value_m"] == pytest.approx(3.2)


def test_her_seventeen_session_routes_are_all_there(demo_app) -> None:
    """If these shrink, the README's comparison with our routes is stale."""
    paths = {route.path for route in demo_app.routes if hasattr(route, "path")}

    for path in (
        "/api/session/{sid}/photo",
        "/api/session/{sid}/door",
        "/api/session/{sid}/events",
        "/api/session/{sid}/move",
        "/api/session/{sid}/undo",
        "/api/session/{sid}/preset",
        "/api/session/{sid}/measure",
        "/api/session/{sid}/approve",
        "/api/session/{sid}/checkout",
    ):
        assert path in paths, f"{path} went missing from her backend"


# ==========================================================================
# It stays mocked, and off the money path
# ==========================================================================


def test_it_defaults_to_mock_mode(client: TestClient) -> None:
    health = client.get("/api/health").json()

    assert health["mock_ai"] is True
    assert health["shopify_live"] is False
    assert health["fixture_catalog"] is True


def test_the_bootstrap_blanks_shopify_before_her_dotenv_can_fill_it() -> None:
    """The guard that keeps this demo off the real store.

    Her app.py calls load_dotenv() at import, which walks up and finds the
    repository root's .env, and her shop.py checks live() before
    fixture_mode(). Started without this guard the demo reported
    shopify_live: true and talked to the development store.

    This test runs the bootstrap in a subprocess with the variables set to
    realistic values, the way a loaded .env would leave them, and checks they
    come back blank. Asserting os.environ in this process would prove
    nothing, because the suite already blanks them.
    """
    import subprocess
    import sys

    program = (
        "import os, sys;"
        "os.environ.pop('LANE_A_DEMO_LIVE_SHOPIFY', None);"
        f"sys.path.insert(0, {str(DEMO_ROOT)!r});"
        "import lane_a_demo;"
        "print(repr(os.environ.get('SHOPIFY_STORE_DOMAIN')));"
        "print(repr(os.environ.get('SHOPIFY_STOREFRONT_TOKEN')));"
        "print(repr(os.environ.get('MOCK_AI')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, cwd=DEMO_ROOT,
        env={
            **{k: v for k, v in __import__("os").environ.items()},
            "SHOPIFY_STORE_DOMAIN": "ethyneon.myshopify.com",
            "SHOPIFY_STOREFRONT_TOKEN": "a-token-shaped-string",
        },
    )
    assert result.returncode == 0, result.stderr
    domain, token, mock_ai = result.stdout.split()

    assert domain == "''", "the demo must blank the store domain"
    assert token == "''", "the demo must blank the storefront token"
    assert mock_ai == "'1'"


def test_the_live_store_needs_an_explicit_opt_in() -> None:
    source = (DEMO_ROOT / "lane_a_demo" / "__init__.py").read_text(encoding="utf-8")

    assert "LANE_A_DEMO_LIVE_SHOPIFY" in source
    assert 'os.environ["SHOPIFY_STORE_DOMAIN"] = ""' in source
    assert 'os.environ["SHOPIFY_STOREFRONT_TOKEN"] = ""' in source


def test_the_fixture_catalogue_is_labelled_not_live(client: TestClient) -> None:
    fixture = DEMO_ROOT / "eval" / "data" / "fixture_catalog.json"
    assert fixture.is_file(), "her mock catalogue should ship with the demo"
    assert "FIXTURE" in fixture.read_text(encoding="utf-8")[:300]


def test_the_demo_does_not_import_our_backend(demo_app) -> None:
    """Two different systems. Her demo must not reach into lane C's package."""
    for name in ("app", "checkout", "shop", "state"):
        source = (DEMO_ROOT / "lane_a_demo" / f"{name}.py").read_text(encoding="utf-8")
        assert "from backend" not in source
        assert "import backend" not in source
        # Her code reads the vendored copies, never our contracts or fit engine.
        assert "from contracts" not in source
        assert "from fit import" not in source
