"""Shared fixtures for the backend tests.

Every test gets its own in-memory database and fully mocked settings, so the
suite is deterministic and needs no network.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from contracts.common import ColorSpec, Measurement, Money
from contracts.interfaces import BudgetLine
from contracts.room_spec import (
    Floor,
    HingeSpec,
    Opening,
    RoomDimensions,
    RoomSpec,
    Wall,
)
from backend.config import Settings
from backend.db import Database
from backend.services import Services, build_services

REPO_ROOT = Path(__file__).resolve().parents[2]

GREY = ColorSpec(hex="#cccccc", evidence_status="confirmed", source="generated")


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """The offline suite must never reach a real service.

    A process environment variable outranks an env_file, so a shell that has
    exported a key, or any import that calls load_dotenv, would otherwise turn
    a stand-in test into a live API call without saying so.
    """
    for name in (
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "SHOPIFY_STOREFRONT_TOKEN",
        "SHOPIFY_STOREFRONT_PRIVATE_TOKEN",
        "SHOPIFY_STORE_DOMAIN",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings() -> Settings:
    """Fully mocked, no credentials, no network."""
    return Settings(
        _env_file=None,
        MOCK_AI=True,
        MOCK_COMMERCE=True,
        MOCK_ROOM_INPUT=True,
        MOCK_AGORA=True,
        SHOP_MCP=False,
        GOOGLE_API_KEY=None,
        GEMINI_API_KEY=None,
    )


@pytest.fixture
def db() -> Database:
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def services(settings: Settings, db: Database) -> Services:
    return build_services(settings, db)


@pytest.fixture
def session_id(services: Services) -> str:
    sid = f"sess_{uuid.uuid4().hex[:8]}"
    services.db.create_session(sid, "shopper_test")
    return sid


def make_room_payload(width: float = 4.0, depth: float = 4.0, *, door: bool = True) -> dict[str, Any]:
    """A valid RoomSpec as a plain dict, ready for commit_revision."""
    spec = RoomSpec(
        room_revision=0,
        session_id="placeholder",
        dimensions_m=RoomDimensions(
            width_m=Measurement(value_m=width, evidence_status="confirmed", source="live"),
            depth_m=Measurement(value_m=depth, evidence_status="confirmed", source="live"),
            height_m=Measurement(value_m=2.5, evidence_status="confirmed", source="live"),
        ),
        walls=[
            Wall(wall_id="wall_n", length_m=width, color=GREY),
            Wall(wall_id="wall_e", length_m=depth, color=GREY),
            Wall(wall_id="wall_s", length_m=width, color=GREY),
            Wall(wall_id="wall_w", length_m=depth, color=GREY),
        ],
        floor=Floor(color=GREY),
        openings=[
            Opening(
                opening_id="door_main",
                wall_id="wall_s",
                type="door",
                offset_m=0.2,
                width_m=0.9,
                height_m=2.03,
                hinge=HingeSpec(
                    hinge_side="left",
                    swing_direction="inward",
                    evidence_status="confirmed",
                ),
                evidence_status="confirmed",
            )
        ]
        if door
        else [],
        evidence_status="confirmed",
    )
    return spec.model_dump(mode="json")


@pytest.fixture
def room_payload() -> dict[str, Any]:
    return make_room_payload()


@pytest.fixture
def committed_room(services: Services, session_id: str, room_payload: dict[str, Any]):
    result = services.revisions.commit_revision(
        "room", room_payload, 0, str(uuid.uuid4()), session_id=session_id
    )
    assert result.committed, result.validation
    return result


@pytest.fixture
def mock_offers() -> list[dict[str, Any]]:
    return json.loads(
        (REPO_ROOT / "fixtures" / "catalog_offers.sample.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def desk_selection() -> list[BudgetLine]:
    """The walnut desk and the monitor, both shoppable in the mock catalog."""
    return [
        BudgetLine(
            variant_id="gid://shopify/ProductVariant/49000000000001",
            quantity=1,
            unit_price=Money(amount_minor=42900, currency="USD"),
        ),
        BudgetLine(
            variant_id="gid://shopify/ProductVariant/49000000000004",
            quantity=1,
            unit_price=Money(amount_minor=31900, currency="USD"),
        ),
    ]


@pytest.fixture
def approved_review(services: Services, session_id: str, desk_selection):
    """A cart review that has been built and confirmed."""
    review = services.carts.build_cart_review(
        session_id,
        "shopper_test",
        desk_selection,
        scene_revision=0,
        brief_revision=0,
        budget=Money(amount_minor=120000, currency="USD"),
    )
    return services.carts.confirm_cart(
        review.review_id, 0, str(uuid.uuid4()), current_scene_revision=0
    )
