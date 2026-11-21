"""Does our swept area land where Karishma says the hinge is?

Her definition, confirmed: **hinge_side "left" means the left as seen from
inside the room facing the door.**

Ours is stated differently: left is the end of the opening nearer the wall's
start, with walls traversed clockwise from wall_n. Those two descriptions
could easily disagree on some walls and not others, and a mirrored door
swing is the worst kind of bug here: it produces a confident, plausible,
wrong "fits" rather than an error.

So this file does not trust the derivation. For each wall it works out the
viewer's left **from first principles**, independently of the engine:

    the viewer stands inside, facing the wall
    facing = -inward_normal
    in our frame, where y increases from wall_n toward wall_s, the viewer's
    left is facing rotated by -90 degrees: (x, y) -> (y, -x)

then asserts the engine hinged the door at that end, and that a probe placed
just inside the arc on the hinge side is blocked while one on the far side
is not.

Sixteen cases: four walls, hinge left and right, swinging inward and outward.
"""

from __future__ import annotations

import pytest

from contracts.common import ColorSpec, Measurement, Pose, Rect2D
from contracts.room_spec import (
    Floor,
    HingeSpec,
    Opening,
    RoomDimensions,
    RoomSpec,
    Wall,
)
from contracts.scene_revision import SceneRevision
from fit.analysis import ProposedPlacement, analyze_fit

pytestmark = pytest.mark.ai

ROOM = 4.0
OFFSET = 1.0
DOOR_WIDTH = 0.9
GREY = ColorSpec(hex="#cccccc", evidence_status="confirmed", source="generated")

# Walls traversed clockwise, and the inward normal for each. Written out
# here rather than imported, so the test does not inherit the engine's own
# idea of the geometry.
WALL_GEOMETRY = {
    "wall_n": {"start": (0.0, 0.0), "along": (1.0, 0.0), "inward": (0.0, 1.0)},
    "wall_e": {"start": (ROOM, 0.0), "along": (0.0, 1.0), "inward": (-1.0, 0.0)},
    "wall_s": {"start": (ROOM, ROOM), "along": (-1.0, 0.0), "inward": (0.0, -1.0)},
    "wall_w": {"start": (0.0, ROOM), "along": (0.0, -1.0), "inward": (1.0, 0.0)},
}

WALLS = list(WALL_GEOMETRY)


def viewer_left(wall_id: str) -> tuple[float, float]:
    """The direction of the viewer's left hand, standing inside facing the wall.

    facing = -inward. Our y increases from wall_n toward wall_s, so a
    rotation of -90 degrees, (x, y) -> (y, -x), gives the left hand.
    Sanity: facing north (0,-1) gives (-1,0), which is west. Correct.
    """
    inward = WALL_GEOMETRY[wall_id]["inward"]
    facing = (-inward[0], -inward[1])
    return (facing[1], -facing[0])


def point_on_wall(wall_id: str, along_m: float, inward_m: float = 0.0) -> tuple[float, float]:
    geometry = WALL_GEOMETRY[wall_id]
    start, along, inward = geometry["start"], geometry["along"], geometry["inward"]
    return (
        start[0] + along[0] * along_m + inward[0] * inward_m,
        start[1] + along[1] * along_m + inward[1] * inward_m,
    )


def expected_hinge(wall_id: str, hinge_side: str) -> tuple[float, float]:
    """Karishma's definition, applied without reference to our engine.

    The opening runs between two points. The "left" hinge is whichever of
    them lies further in the viewer's left direction.
    """
    near = point_on_wall(wall_id, OFFSET)
    far = point_on_wall(wall_id, OFFSET + DOOR_WIDTH)
    left_dir = viewer_left(wall_id)

    near_score = near[0] * left_dir[0] + near[1] * left_dir[1]
    far_score = far[0] * left_dir[0] + far[1] * left_dir[1]
    leftmost, rightmost = (near, far) if near_score > far_score else (far, near)
    return leftmost if hinge_side == "left" else rightmost


def make_room(wall_id: str, hinge_side: str, swing: str) -> RoomSpec:
    return RoomSpec(
        room_revision=1,
        session_id="sess_golden",
        dimensions_m=RoomDimensions(
            width_m=Measurement(value_m=ROOM, evidence_status="confirmed", source="live"),
            depth_m=Measurement(value_m=ROOM, evidence_status="confirmed", source="live"),
            height_m=Measurement(value_m=2.5, evidence_status="confirmed", source="live"),
        ),
        walls=[Wall(wall_id=w, length_m=ROOM, color=GREY) for w in WALLS],
        floor=Floor(color=GREY),
        openings=[
            Opening(
                opening_id="door_main",
                wall_id=wall_id,
                type="door",
                offset_m=OFFSET,
                width_m=DOOR_WIDTH,
                height_m=2.03,
                hinge=HingeSpec(
                    hinge_side=hinge_side,
                    swing_direction=swing,
                    swing_angle_max_deg=90.0,
                    evidence_status="confirmed",
                ),
                evidence_status="confirmed",
            )
        ],
        evidence_status="confirmed",
    )


def probe_at(point: tuple[float, float]) -> ProposedPlacement:
    """A small object, so the test is about position rather than size."""
    return ProposedPlacement(
        object_id="probe",
        pose=Pose(x_m=point[0], y_m=point[1]),
        footprint=Rect2D(x_m=point[0], y_m=point[1], width_m=0.08, depth_m=0.08),
    )


def empty_scene() -> SceneRevision:
    return SceneRevision(scene_revision=0, session_id="sess_golden", room_revision=1)


def run(room: RoomSpec, point: tuple[float, float]):
    # Margin zero so the result is pure geometry rather than the margin.
    return analyze_fit(room, empty_scene(), probe_at(point), clearance_margin_m=0.0)


def door_swing_checks(analysis):
    return [c for c in analysis.report.checks_run if c.name == "door_swing"]


# The two probes: just inside the arc at each end of the opening, 0.8 m into
# the room. Each is within 0.9 m of its own end and ~1.17 m from the other,
# so only the arc hinged at its own end can reach it.
NEAR_LEFT_ALONG = OFFSET + 0.05
NEAR_RIGHT_ALONG = OFFSET + DOOR_WIDTH - 0.05
PROBE_DEPTH = 0.8


def probe_points(wall_id: str) -> tuple[tuple[float, float], tuple[float, float]]:
    """(probe by the left end, probe by the right end), in our coordinates."""
    left_dir = viewer_left(wall_id)
    a = point_on_wall(wall_id, NEAR_LEFT_ALONG, PROBE_DEPTH)
    b = point_on_wall(wall_id, NEAR_RIGHT_ALONG, PROBE_DEPTH)
    a_score = a[0] * left_dir[0] + a[1] * left_dir[1]
    b_score = b[0] * left_dir[0] + b[1] * left_dir[1]
    return (a, b) if a_score > b_score else (b, a)


# ==========================================================================
# The derivation itself
# ==========================================================================


@pytest.mark.parametrize(
    "wall_id,expected",
    [
        ("wall_n", (-1.0, 0.0)),  # facing north, left is west
        ("wall_e", (0.0, -1.0)),  # facing east, left is north
        ("wall_s", (1.0, 0.0)),   # facing south, left is east
        ("wall_w", (0.0, 1.0)),   # facing west, left is south
    ],
)
def test_the_viewers_left_is_what_a_person_would_say(wall_id, expected) -> None:
    """Sanity-check the test's own geometry before trusting its verdicts."""
    assert viewer_left(wall_id) == pytest.approx(expected)


@pytest.mark.parametrize("wall_id", WALLS)
def test_her_definition_and_ours_pick_the_same_end(wall_id: str) -> None:
    """Her "left from inside" against our "nearer the wall start"."""
    ours_left = point_on_wall(wall_id, OFFSET)
    assert expected_hinge(wall_id, "left") == pytest.approx(ours_left), (
        f"on {wall_id} her definition and ours disagree about which end is left"
    )


# ==========================================================================
# Sixteen goldens
# ==========================================================================


@pytest.mark.parametrize("wall_id", WALLS)
@pytest.mark.parametrize("hinge_side", ["left", "right"])
def test_inward_door_hinges_where_she_says(wall_id: str, hinge_side: str) -> None:
    room = make_room(wall_id, hinge_side, "inward")
    analysis = run(room, point_on_wall(wall_id, 2.5, 1.5))  # a probe well clear

    swings = analysis.geometry["door_swings"]
    assert len(swings) == 1
    assert tuple(swings[0]["hinge"]) == pytest.approx(expected_hinge(wall_id, hinge_side))


@pytest.mark.parametrize("wall_id", WALLS)
@pytest.mark.parametrize("hinge_side", ["left", "right"])
def test_inward_door_sweeps_the_hinge_side_and_not_the_other(
    wall_id: str, hinge_side: str
) -> None:
    """The golden that would catch a mirrored swing."""
    room = make_room(wall_id, hinge_side, "inward")
    by_left, by_right = probe_points(wall_id)
    on_hinge_side = by_left if hinge_side == "left" else by_right
    on_far_side = by_right if hinge_side == "left" else by_left

    blocked = run(room, on_hinge_side)
    clear = run(room, on_far_side)

    assert any(c.result == "fail" for c in door_swing_checks(blocked)), (
        f"{wall_id} hinge {hinge_side}: the arc did not reach its own hinge side"
    )
    assert blocked.report.result == "does_not_fit"
    assert blocked.report.rejected_placement.failing_check == "door_swing"

    assert all(c.result != "fail" for c in door_swing_checks(clear)), (
        f"{wall_id} hinge {hinge_side}: the arc reached the far end, so the "
        f"swing is mirrored"
    )


@pytest.mark.parametrize("wall_id", WALLS)
@pytest.mark.parametrize("hinge_side", ["left", "right"])
def test_outward_door_sweeps_nothing_inside_the_room(
    wall_id: str, hinge_side: str
) -> None:
    """An outward door takes its arc outside, so neither probe is blocked."""
    room = make_room(wall_id, hinge_side, "outward")
    by_left, by_right = probe_points(wall_id)

    for point in (by_left, by_right):
        analysis = run(room, point)
        checks = door_swing_checks(analysis)
        assert checks and all(c.result == "pass" for c in checks)
        assert analysis.report.result in ("fits", "tight")

    assert run(room, by_left).geometry["door_swings"] == []


# ==========================================================================
# The same thing, arriving through her door-clip output
# ==========================================================================


@pytest.mark.parametrize("wall_id", WALLS)
@pytest.mark.parametrize("hinge_side", ["left", "right"])
def test_her_door_motion_produces_the_same_swing(wall_id: str, hinge_side: str) -> None:
    """End to end: her DoorMotion through the bridge into our engine."""
    from ai.karishma._vendor import kschemas as k
    from ai.karishma_adapter import contracts_bridge as bridge

    motion = k.DoorMotion(
        hinge_side=hinge_side, swing="in", max_angle_deg=90.0,
        source="confirmed", confidence=0.95, evidence_ts="00:04",
    )
    hinge = bridge.door_motion_to_hinge(motion)
    assert hinge.hinge_side == hinge_side
    assert hinge.swing_direction == "inward"

    room = make_room(wall_id, hinge.hinge_side, hinge.swing_direction)
    by_left, by_right = probe_points(wall_id)
    on_hinge_side = by_left if hinge_side == "left" else by_right

    analysis = run(room, on_hinge_side)
    assert analysis.report.result == "does_not_fit"
    assert tuple(analysis.geometry["door_swings"][0]["hinge"]) == pytest.approx(
        expected_hinge(wall_id, hinge_side)
    )
