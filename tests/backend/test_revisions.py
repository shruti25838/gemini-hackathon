"""State and revisions: staleness, idempotency, validation and undo."""

from __future__ import annotations

import uuid

import pytest

from backend.services import Services
from tests.backend.conftest import make_room_payload

pytestmark = pytest.mark.backend


# ==========================================================================
# Stale base revisions
# ==========================================================================


def test_a_stale_base_revision_is_refused_with_the_latest(
    services: Services, session_id: str, room_payload
) -> None:
    services.revisions.commit_revision(
        "room", room_payload, 0, str(uuid.uuid4()), session_id=session_id
    )

    result = services.revisions.commit_revision(
        "room", make_room_payload(width=3.0), 0, str(uuid.uuid4()), session_id=session_id
    )

    assert result.committed is False
    assert result.conflict is not None
    assert result.conflict.code == "stale_base_revision"
    assert result.conflict.latest_revision == 1
    assert result.conflict.retryable is True


def test_a_stale_commit_leaves_the_existing_revision_untouched(
    services: Services, session_id: str, room_payload
) -> None:
    services.revisions.commit_revision(
        "room", room_payload, 0, str(uuid.uuid4()), session_id=session_id
    )
    services.revisions.commit_revision(
        "room", make_room_payload(width=3.0), 0, str(uuid.uuid4()), session_id=session_id
    )

    current = services.revisions.current_room(session_id)
    assert current.room_revision == 1
    assert current.dimensions_m.width_m.value_m == 4.0


def test_rebasing_after_a_conflict_succeeds(
    services: Services, session_id: str, room_payload
) -> None:
    services.revisions.commit_revision(
        "room", room_payload, 0, str(uuid.uuid4()), session_id=session_id
    )
    conflict = services.revisions.commit_revision(
        "room", make_room_payload(width=3.0), 0, str(uuid.uuid4()), session_id=session_id
    )
    retried = services.revisions.commit_revision(
        "room",
        make_room_payload(width=3.0),
        conflict.conflict.latest_revision,
        str(uuid.uuid4()),
        session_id=session_id,
    )
    assert retried.committed is True
    assert retried.revision == 2


# ==========================================================================
# Repeated action_id
# ==========================================================================


def test_a_repeated_action_id_returns_the_first_result(
    services: Services, session_id: str, room_payload
) -> None:
    action_id = str(uuid.uuid4())
    first = services.revisions.commit_revision(
        "room", room_payload, 0, action_id, session_id=session_id
    )
    second = services.revisions.commit_revision(
        "room", room_payload, 0, action_id, session_id=session_id
    )

    assert first.committed and second.committed
    assert first.revision == second.revision == 1
    assert second.replayed is True
    assert first.replayed is False


def test_a_repeated_action_id_does_not_apply_the_write_twice(
    services: Services, session_id: str, room_payload
) -> None:
    action_id = str(uuid.uuid4())
    for _ in range(4):
        services.revisions.commit_revision(
            "room", room_payload, 0, action_id, session_id=session_id
        )
    assert services.db.current_revision(session_id, "room") == 1


def test_a_repeat_wins_even_when_the_payload_differs(
    services: Services, session_id: str, room_payload
) -> None:
    """The action_id identifies the intent. A retry carrying different data
    is still the same action, and the stored result is what comes back."""
    action_id = str(uuid.uuid4())
    services.revisions.commit_revision(
        "room", room_payload, 0, action_id, session_id=session_id
    )
    second = services.revisions.commit_revision(
        "room", make_room_payload(width=9.0), 0, action_id, session_id=session_id
    )
    assert second.replayed is True
    assert services.revisions.current_room(session_id).dimensions_m.width_m.value_m == 4.0


def test_a_write_with_no_action_id_is_refused(
    services: Services, session_id: str, room_payload
) -> None:
    from backend.errors import EthyneonError

    with pytest.raises(EthyneonError) as excinfo:
        services.revisions.commit_revision(
            "room", room_payload, 0, "", session_id=session_id
        )
    assert excinfo.value.code == "action_id_required"


# ==========================================================================
# Validation keeps the previous room
# ==========================================================================


def test_an_opening_outside_its_wall_is_rejected(
    services: Services, session_id: str
) -> None:
    payload = make_room_payload(width=3.2)
    payload["openings"][0]["offset_m"] = 3.0
    payload["openings"][0]["width_m"] = 0.82

    result = services.revisions.commit_revision(
        "room", payload, 0, str(uuid.uuid4()), session_id=session_id
    )

    assert result.committed is False
    assert result.validation is not None and not result.validation.ok
    codes = {error.code for error in result.validation.errors}
    assert "opening_outside_wall" in codes


def test_an_invalid_room_never_replaces_a_valid_one(
    services: Services, session_id: str, room_payload
) -> None:
    services.revisions.commit_revision(
        "room", room_payload, 0, str(uuid.uuid4()), session_id=session_id
    )

    bad = make_room_payload(width=3.2)
    bad["openings"][0]["offset_m"] = 3.0
    result = services.revisions.commit_revision(
        "room", bad, 1, str(uuid.uuid4()), session_id=session_id
    )

    assert result.committed is False
    kept = services.revisions.current_room(session_id)
    assert kept.room_revision == 1
    assert kept.dimensions_m.width_m.value_m == 4.0
    assert kept.openings[0].offset_m == 0.2


def test_a_room_outside_the_supported_size_bounds_is_rejected(
    services: Services, session_id: str
) -> None:
    result = services.revisions.commit_revision(
        "room", make_room_payload(width=50.0), 0, str(uuid.uuid4()), session_id=session_id
    )
    assert result.committed is False
    assert "out_of_supported_bounds" in {e.code for e in result.validation.errors}


def test_a_non_finite_dimension_is_rejected(services: Services, session_id: str) -> None:
    payload = make_room_payload()
    payload["dimensions_m"]["width_m"]["value_m"] = float("inf")
    result = services.revisions.commit_revision(
        "room", payload, 0, str(uuid.uuid4()), session_id=session_id
    )
    assert result.committed is False


def test_a_free_zone_outside_the_room_is_rejected(
    services: Services, session_id: str
) -> None:
    payload = make_room_payload(width=4.0, depth=4.0)
    payload["free_zones"] = [
        {
            "zone_id": "zone_bad",
            "label": "Outside",
            "area": {
                "points": [
                    {"x_m": 10.0, "y_m": 10.0},
                    {"x_m": 11.0, "y_m": 10.0},
                    {"x_m": 11.0, "y_m": 11.0},
                ]
            },
            "purpose": "keep_clear",
        }
    ]
    result = services.revisions.commit_revision(
        "room", payload, 0, str(uuid.uuid4()), session_id=session_id
    )
    assert result.committed is False
    assert "zone_outside_room" in {e.code for e in result.validation.errors}


def test_a_rejected_room_emits_an_event_so_the_shopper_learns_why(
    services: Services, session_id: str
) -> None:
    bad = make_room_payload(width=3.2)
    bad["openings"][0]["offset_m"] = 3.0
    services.revisions.commit_revision(
        "room", bad, 0, str(uuid.uuid4()), session_id=session_id
    )
    events = services.db.events_since(session_id)
    assert any(e["type"] == "waiting_for_measurement" for e in events)


# ==========================================================================
# Confirmed values survive a later estimate
# ==========================================================================


def test_a_later_estimate_never_overwrites_a_confirmed_dimension(
    services: Services, session_id: str, room_payload
) -> None:
    """The shopper measured the wall by hand. A new photo must not undo that."""
    services.revisions.commit_revision(
        "room", room_payload, 0, str(uuid.uuid4()), session_id=session_id
    )

    redraft = make_room_payload(width=3.1)
    redraft["dimensions_m"]["width_m"]["evidence_status"] = "estimated"
    redraft["dimensions_m"]["width_m"]["source"] = "inferred"

    result = services.revisions.commit_revision(
        "room", redraft, 1, str(uuid.uuid4()), session_id=session_id
    )

    assert result.committed is True
    assert result.room_spec.dimensions_m.width_m.value_m == 4.0
    assert result.room_spec.dimensions_m.width_m.evidence_status == "confirmed"


def test_a_new_confirmed_value_does_replace_an_old_one(
    services: Services, session_id: str, room_payload
) -> None:
    services.revisions.commit_revision(
        "room", room_payload, 0, str(uuid.uuid4()), session_id=session_id
    )
    corrected = make_room_payload(width=3.6)
    result = services.revisions.commit_revision(
        "room", corrected, 1, str(uuid.uuid4()), session_id=session_id
    )
    assert result.committed is True
    assert result.room_spec.dimensions_m.width_m.value_m == 3.6


# ==========================================================================
# Scene patches
# ==========================================================================


def _add_desk_patch(variant_id: str = "gid://shopify/ProductVariant/49000000000001"):
    return {
        "actions": [
            {
                "type": "add_object",
                "object_id": "obj_desk",
                "payload": {
                    "object_id": "obj_desk",
                    "label": "Walnut desk",
                    "x_m": 2.0,
                    "y_m": 0.5,
                    "width_m": 1.2,
                    "depth_m": 0.6,
                    "product_id": "gid://shopify/Product/9000000000001",
                    "variant_id": variant_id,
                    "store_domain": "ethyneon-dev.myshopify.com",
                    "offer_ref": "offer_dev_desk_walnut",
                    "asset_id": "asset_desk_rect",
                },
            }
        ],
        "origin": "user",
    }


def test_a_scene_patch_commits_and_increments_the_revision(
    services: Services, session_id: str, committed_room
) -> None:
    result = services.revisions.commit_revision(
        "scene",
        _add_desk_patch(),
        0,
        str(uuid.uuid4()),
        session_id=session_id,
        known_variant_ids={"gid://shopify/ProductVariant/49000000000001"},
        known_asset_ids={"asset_desk_rect"},
    )
    assert result.committed is True
    assert result.revision == 1
    assert len(result.scene.placements) == 1


def test_an_unverified_variant_cannot_enter_the_scene(
    services: Services, session_id: str, committed_room
) -> None:
    """Shopify is the only product source, enforced at commit time."""
    result = services.revisions.commit_revision(
        "scene",
        _add_desk_patch(variant_id="gid://shopify/ProductVariant/does-not-exist"),
        0,
        str(uuid.uuid4()),
        session_id=session_id,
        known_variant_ids={"gid://shopify/ProductVariant/49000000000001"},
        known_asset_ids={"asset_desk_rect"},
    )
    assert result.committed is False
    assert "unverified_variant" in {e.code for e in result.validation.errors}


def test_an_unknown_action_type_is_refused(
    services: Services, session_id: str, committed_room
) -> None:
    patch = {
        "actions": [{"type": "delete_everything", "payload": {}}],
        "origin": "agent",
    }
    result = services.revisions.commit_revision(
        "scene", patch, 0, str(uuid.uuid4()), session_id=session_id
    )
    assert result.committed is False


def test_a_position_outside_the_room_is_refused(
    services: Services, session_id: str, committed_room
) -> None:
    patch = _add_desk_patch()
    patch["actions"][0]["payload"]["x_m"] = 99.0
    result = services.revisions.commit_revision(
        "scene",
        patch,
        0,
        str(uuid.uuid4()),
        session_id=session_id,
        known_variant_ids={"gid://shopify/ProductVariant/49000000000001"},
        known_asset_ids={"asset_desk_rect"},
    )
    assert result.committed is False
    assert "outside_room" in {e.code for e in result.validation.errors}


# ==========================================================================
# Undo
# ==========================================================================


def test_undo_writes_a_new_revision_rather_than_rewinding(
    services: Services, session_id: str, committed_room
) -> None:
    services.revisions.commit_revision(
        "scene",
        _add_desk_patch(),
        0,
        str(uuid.uuid4()),
        session_id=session_id,
        known_variant_ids={"gid://shopify/ProductVariant/49000000000001"},
        known_asset_ids={"asset_desk_rect"},
    )
    services.revisions.commit_revision(
        "scene",
        {
            "actions": [
                {
                    "type": "move_object",
                    "object_id": "obj_desk",
                    "payload": {"x_m": 3.0, "y_m": 1.0},
                }
            ]
        },
        1,
        str(uuid.uuid4()),
        session_id=session_id,
    )

    undone = services.revisions.undo_scene(session_id, 1, str(uuid.uuid4()))

    assert undone.committed is True
    assert undone.revision == 3, "undo must move the counter forward, never back"
    assert undone.scene.undo_of == 1
    assert undone.scene.placements[0].pose.x_m == 2.0


def test_undo_is_idempotent_on_a_repeated_action_id(
    services: Services, session_id: str, committed_room
) -> None:
    services.revisions.commit_revision(
        "scene",
        _add_desk_patch(),
        0,
        str(uuid.uuid4()),
        session_id=session_id,
        known_variant_ids={"gid://shopify/ProductVariant/49000000000001"},
        known_asset_ids={"asset_desk_rect"},
    )
    action_id = str(uuid.uuid4())
    first = services.revisions.undo_scene(session_id, 1, action_id)
    second = services.revisions.undo_scene(session_id, 1, action_id)
    assert first.revision == second.revision
    assert second.replayed is True
