"""Round trips between Karishma's room contracts and ours.

A translation layer is only trustworthy if it is honest about what it drops.
These tests pin the fields that survive, and assert the documented LOSSY list
is accurate rather than stale.
"""

from __future__ import annotations

import pytest

from ai.karishma._vendor import kschemas as k
from ai.karishma_adapter import contracts_bridge as bridge
from backend.validation import validate_room_spec

pytestmark = pytest.mark.ai


def her_room(**overrides) -> k.RoomSpec:
    payload = dict(
        room_revision=3,
        width_m=k.Dimension(value_m=4.0, source="confirmed", confidence=1.0),
        depth_m=k.Dimension(value_m=3.5, source="estimated", confidence=0.6),
        height_m=k.Dimension(value_m=2.5, source="estimated", confidence=0.5),
        scale_reference="desk wall measured by the shopper",
        walls=[
            k.Wall(id="wall_n", color_hex="#e8e4dc", color_source="sampled_from_photo"),
            k.Wall(id="wall_s", color_hex="#6b7a4f", color_source="confirmed"),
        ],
        openings=[
            k.Opening(
                id="door_main",
                wall="wall_s",
                type="door",
                offset_m=1.0,
                width_m=0.9,
                height_m=2.03,
                sill_m=0.0,
                confidence=0.95,
                motion=k.DoorMotion(
                    hinge_side="left",
                    swing="in",
                    max_angle_deg=90.0,
                    source="confirmed",
                    confidence=0.93,
                    evidence_ts="00:03",
                ),
            ),
            k.Opening(
                id="window_n",
                wall="wall_n",
                type="window",
                offset_m=1.5,
                width_m=1.1,
                height_m=1.2,
                sill_m=0.9,
                confidence=0.7,
            ),
        ],
        existing_items=[
            k.ExistingItem(
                id="obj_monitor", label="Monitor", x=1.6, z=0.4,
                w=0.62, d=0.22, h=0.46, keep=True, confidence=0.95,
            )
        ],
        free_zones=[k.FreeZone(x0=0.2, z0=0.8, x1=1.1, z1=3.4, label="route")],
        floor_color_hex="#9c7b53",
    )
    payload.update(overrides)
    return k.RoomSpec(**payload)


# ==========================================================================
# Hers to ours
# ==========================================================================


def test_a_room_crosses_and_validates() -> None:
    """The real test of the bridge: our own validator accepts the result."""
    ours = bridge.room_spec_to_ours(her_room(), session_id="sess_x")
    assert validate_room_spec(ours).ok is True


def test_dimensions_and_evidence_survive() -> None:
    ours = bridge.room_spec_to_ours(her_room())
    assert ours.dimensions_m.width_m.value_m == 4.0
    assert ours.dimensions_m.width_m.evidence_status == "confirmed"
    assert ours.dimensions_m.depth_m.evidence_status == "estimated"
    assert ours.dimensions_m.depth_m.confidence == 0.6


def test_all_four_walls_are_produced_with_lengths() -> None:
    """She supplied two walls and no lengths; ours needs four, with lengths."""
    ours = bridge.room_spec_to_ours(her_room())
    assert [w.wall_id for w in ours.walls] == ["wall_n", "wall_e", "wall_s", "wall_w"]
    lengths = {w.wall_id: w.length_m for w in ours.walls}
    assert lengths["wall_n"] == lengths["wall_s"] == 4.0
    assert lengths["wall_e"] == lengths["wall_w"] == 3.5


def test_a_wall_she_omitted_gets_a_default_colour() -> None:
    ours = bridge.room_spec_to_ours(her_room())
    east = next(w for w in ours.walls if w.wall_id == "wall_e")
    assert east.color.hex == bridge.DEFAULT_HEX


def test_her_coordinates_become_ours() -> None:
    """x/z to x_m/y_m. A silent swap here would move every object."""
    ours = bridge.room_spec_to_ours(her_room())
    item = ours.existing_items[0]
    assert item.footprint.x_m == 1.6
    assert item.footprint.y_m == 0.4
    assert item.footprint.width_m == 0.62
    assert item.footprint.depth_m == 0.22
    assert item.height_m == 0.46


def test_a_rectangle_free_zone_becomes_a_four_point_polygon() -> None:
    ours = bridge.room_spec_to_ours(her_room())
    zone = ours.free_zones[0]
    assert len(zone.area.points) == 4
    xs = {p.x_m for p in zone.area.points}
    ys = {p.y_m for p in zone.area.points}
    assert xs == {0.2, 1.1}
    assert ys == {0.8, 3.4}


def test_an_opening_type_she_calls_opening_becomes_archway() -> None:
    room = her_room(
        openings=[
            k.Opening(id="arch", wall="wall_e", type="opening", offset_m=0.5,
                      width_m=1.0, height_m=2.1, sill_m=0.0, confidence=0.8)
        ]
    )
    ours = bridge.room_spec_to_ours(room)
    assert ours.openings[0].type == "archway"


def test_a_window_has_no_hinge() -> None:
    ours = bridge.room_spec_to_ours(her_room())
    window = next(o for o in ours.openings if o.opening_id == "window_n")
    assert window.hinge is None
    assert window.sill_m == 0.9


# ==========================================================================
# Door motion, the part the fit engine depends on
# ==========================================================================


@pytest.mark.parametrize("hinge_side", ["left", "right"])
@pytest.mark.parametrize("swing,expected", [("in", "inward"), ("out", "outward")])
def test_door_motion_maps_exactly(hinge_side: str, swing: str, expected: str) -> None:
    motion = k.DoorMotion(
        hinge_side=hinge_side, swing=swing, max_angle_deg=95.0,
        source="confirmed", confidence=0.9, evidence_ts="00:07",
    )
    hinge = bridge.door_motion_to_hinge(motion)
    assert hinge.hinge_side == hinge_side
    assert hinge.swing_direction == expected
    assert hinge.swing_angle_max_deg == 95.0
    assert hinge.evidence_status == "confirmed"
    assert hinge.motion_evidence_ref == "door_clip@00:07"


def test_no_motion_stays_unknown_rather_than_guessing() -> None:
    """She leaves motion unset when Neon and Decision 1 disagree. Our engine
    must then answer unknown, not assume a standard door."""
    assert bridge.door_motion_to_hinge(None) is None


def test_an_unknown_hinge_side_does_not_go_back_to_her() -> None:
    """A wrong hinge is worse than a missing one: ours answers unknown for a
    missing one and confidently wrong for a mirrored one."""
    from contracts.room_spec import HingeSpec

    assert bridge.hinge_to_door_motion(HingeSpec(hinge_side="unknown", swing_direction="inward")) is None
    assert bridge.hinge_to_door_motion(HingeSpec(hinge_side="left", swing_direction="unknown")) is None
    assert bridge.hinge_to_door_motion(HingeSpec(hinge_side="left", swing_direction="sliding")) is None


@pytest.mark.parametrize(
    "her_source,ours",
    [
        ("estimated", "estimated"),
        ("confirmed", "confirmed"),
        ("unknown", "unknown"),
        ("sampled_from_photo", "sampled_from_photo"),
        ("inferred", "estimated"),
        ("catalog_fact", "confirmed"),
        ("default", "estimated"),
        ("preset", "estimated"),
    ],
)
def test_every_one_of_her_source_values_maps(her_source: str, ours: str) -> None:
    assert bridge.SOURCE_TO_EVIDENCE[her_source] == ours


# ==========================================================================
# Round trip
# ==========================================================================


def test_what_survives_a_full_round_trip() -> None:
    original = her_room()
    returned = bridge.room_spec_to_hers(bridge.room_spec_to_ours(original))

    assert returned.room_revision == original.room_revision
    assert returned.width_m.value_m == original.width_m.value_m
    assert returned.width_m.source == original.width_m.source
    assert returned.depth_m.value_m == original.depth_m.value_m
    assert returned.floor_color_hex == original.floor_color_hex

    door_before = original.openings[0]
    door_after = next(o for o in returned.openings if o.id == "door_main")
    assert door_after.wall == door_before.wall
    assert door_after.offset_m == door_before.offset_m
    assert door_after.width_m == door_before.width_m
    assert door_after.motion.hinge_side == door_before.motion.hinge_side
    assert door_after.motion.swing == door_before.motion.swing
    assert door_after.motion.max_angle_deg == door_before.motion.max_angle_deg

    item_after = returned.existing_items[0]
    assert (item_after.x, item_after.z, item_after.w) == (1.6, 0.4, 0.62)


def test_the_round_trip_gains_the_two_walls_she_omitted() -> None:
    returned = bridge.room_spec_to_hers(bridge.room_spec_to_ours(her_room()))
    assert len(returned.walls) == 4


def test_the_clip_timestamp_does_not_survive() -> None:
    """Ours stores a reference to the clip, not a timestamp inside it."""
    returned = bridge.room_spec_to_hers(bridge.room_spec_to_ours(her_room()))
    assert returned.openings[0].motion.evidence_ts is None


def test_a_walking_route_does_not_survive() -> None:
    """Her FreeZone has no purpose, so everything becomes keep_clear. This is
    the loss most likely to change a fit result, so it is pinned."""
    ours = bridge.room_spec_to_ours(her_room())
    assert all(zone.purpose == "keep_clear" for zone in ours.free_zones)


def test_locked_items_do_not_survive_from_her_side() -> None:
    ours = bridge.room_spec_to_ours(her_room())
    assert all(item.locked is False for item in ours.existing_items)


def test_the_lossy_list_names_the_fields_the_tests_found() -> None:
    """Keeps the documentation honest as the bridge changes."""
    for key in (
        "RoomSpec.session_id",
        "RoomSpec.scale_reference",
        "Wall.length_m",
        "FreeZone.purpose",
        "ExistingItem.locked",
        "DoorMotion.evidence_ts",
        "HingeSpec.hinge_side=unknown",
    ):
        assert key in bridge.LOSSY, f"{key} is lossy but undocumented"
        assert bridge.LOSSY[key].strip(), f"{key} has no reason recorded"


def test_nothing_of_hers_leaks_into_our_model() -> None:
    """The result must be our type, not a structural look-alike."""
    from contracts.room_spec import RoomSpec as OurRoomSpec

    ours = bridge.room_spec_to_ours(her_room())
    assert isinstance(ours, OurRoomSpec)
    assert type(ours).__module__.startswith("contracts.")
