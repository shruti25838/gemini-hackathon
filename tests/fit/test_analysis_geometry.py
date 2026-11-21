"""The richer output: polygons, swept areas, corridors, clearances, causes.

Lane B draws from this, so a bounding box where an intersection should be, or
a clearance with no stated endpoints, is a real defect rather than cosmetic.
"""

from __future__ import annotations

import pytest

from contracts.common import Point2D, Polygon2D, Pose, Rect2D
from contracts.room_spec import FreeZone, Opening
from fit.analysis import ProposedPlacement, analyze_fit
from fit.geometry import (
    closest_points,
    corridor_width_profile,
    polygon_area,
    polygon_intersection,
    rect_corners,
)
from tests.fit.test_engine import (
    GREY,
    empty_scene,
    inward_door,
    make_room,
    placed,
    proposal,
)

pytestmark = pytest.mark.fit


# ==========================================================================
# Geometry primitives
# ==========================================================================


def test_intersection_of_two_overlapping_squares() -> None:
    a = rect_corners(1, 1, 2, 2)
    b = rect_corners(2, 2, 2, 2)
    overlap = polygon_intersection(a, b)
    assert polygon_area(overlap) == pytest.approx(1.0, abs=1e-6)


def test_disjoint_shapes_intersect_in_nothing() -> None:
    a = rect_corners(0, 0, 1, 1)
    b = rect_corners(5, 5, 1, 1)
    assert polygon_intersection(a, b) == []


def test_a_contained_shape_intersects_as_itself() -> None:
    outer = rect_corners(2, 2, 4, 4)
    inner = rect_corners(2, 2, 1, 1)
    overlap = polygon_intersection(inner, outer)
    assert polygon_area(overlap) == pytest.approx(1.0, abs=1e-6)


def test_closest_points_reports_both_ends_of_the_gap() -> None:
    a = rect_corners(1, 1, 2, 2)
    b = rect_corners(5, 1, 2, 2)
    from_point, to_point, gap = closest_points(a, b)
    assert gap == pytest.approx(2.0, abs=1e-6)
    assert from_point[0] == pytest.approx(2.0, abs=1e-6)
    assert to_point[0] == pytest.approx(4.0, abs=1e-6)


def test_a_corridor_with_nothing_in_it_keeps_its_full_width() -> None:
    corridor = [(0.0, 0.0), (1.0, 0.0), (1.0, 4.0), (0.0, 4.0)]
    profile = corridor_width_profile(corridor, [])
    assert profile
    assert all(s["free_width_m"] == pytest.approx(1.0, abs=1e-3) for s in profile)


def test_a_corridor_reports_where_it_narrows() -> None:
    corridor = [(0.0, 0.0), (1.0, 0.0), (1.0, 4.0), (0.0, 4.0)]
    obstacle = rect_corners(0.85, 2.0, 0.3, 0.4)
    profile = corridor_width_profile(corridor, [obstacle])
    narrowest = min(profile, key=lambda s: s["free_width_m"])
    assert narrowest["free_width_m"] < 1.0
    assert 1.6 <= narrowest["at_m"] <= 2.4


# ==========================================================================
# What the analysis returns
# ==========================================================================


def test_an_overlap_gives_the_real_intersection_polygon() -> None:
    scene = empty_scene([placed("shelf", 2.0, 1.0, 1.0, 1.0)])
    analysis = analyze_fit(make_room(), scene, proposal("desk", 2.5, 1.0, 1.0, 1.0))

    overlaps = analysis.geometry["overlaps"]
    assert len(overlaps) == 1
    assert overlaps[0]["with"] == "shelf"
    assert polygon_area([tuple(p) for p in overlaps[0]["polygon"]]) == pytest.approx(
        0.5, abs=1e-3
    )


def test_the_door_swept_area_and_every_angle_are_returned() -> None:
    room = make_room(openings=[inward_door()])
    analysis = analyze_fit(room, empty_scene(), proposal("desk", 3.4, 3.4, 1.0, 0.6))

    swings = analysis.geometry["door_swings"]
    assert len(swings) == 1
    swing = swings[0]
    assert swing["opening_id"] == "door_main"
    assert len(swing["hinge"]) == 2
    assert len(swing["swept_polygon"]) >= 3
    assert len(swing["angles"]) >= 3
    assert swing["angles"][0]["angle_deg"] == 0.0
    assert swing["angles"][-1]["angle_deg"] == pytest.approx(90.0)
    assert any(angle["hits_placement"] for angle in swing["angles"])


def test_a_clear_door_reports_no_angle_hitting() -> None:
    room = make_room(openings=[inward_door()])
    analysis = analyze_fit(room, empty_scene(), proposal("desk", 2.0, 0.5, 1.0, 0.6))
    swing = analysis.geometry["door_swings"][0]
    assert not any(angle["hits_placement"] for angle in swing["angles"])


def test_the_corridor_profile_shows_the_narrowing() -> None:
    route = FreeZone(
        zone_id="route_door_to_desk",
        label="Door to desk",
        area=Polygon2D(
            points=[
                Point2D(x_m=0.2, y_m=3.9),
                Point2D(x_m=1.1, y_m=3.9),
                Point2D(x_m=1.1, y_m=0.8),
                Point2D(x_m=0.2, y_m=0.8),
            ]
        ),
        purpose="walking_route",
    )
    analysis = analyze_fit(
        make_room(free_zones=[route]), empty_scene(), proposal("desk", 0.7, 2.0, 1.2, 0.6)
    )
    corridor = analysis.geometry["corridors"][0]
    assert corridor["zone_id"] == "route_door_to_desk"
    assert corridor["required_width_m"] == 0.75
    assert corridor["profile"]
    assert corridor["narrowest"]["free_width_m"] < 0.75


def test_the_minimum_clearance_names_what_it_was_measured_between() -> None:
    """A bare number does not tell anyone anything."""
    scene = empty_scene([placed("shelf", 2.0, 1.0, 1.0, 0.4)])
    analysis = analyze_fit(make_room(), scene, proposal("desk", 2.0, 2.0, 1.2, 0.6))

    clearance = analysis.geometry["minimum_clearance"]
    assert clearance["metres"] == analysis.report.minimum_clearance_m
    assert len(clearance["between"]) == 2
    assert clearance["from_point"] != clearance["to_point"]
    assert all(isinstance(v, (int, float)) for v in clearance["from_point"])


def test_the_nearest_wall_clearance_names_the_wall_and_real_points() -> None:
    analysis = analyze_fit(make_room(), empty_scene(), proposal("desk", 2.0, 0.4, 1.2, 0.6))
    clearance = analysis.geometry["minimum_clearance"]
    assert clearance["between"][1].startswith("wall_")
    assert clearance["from_point"] != [0.0, 0.0]


# ==========================================================================
# The plain-English cause
# ==========================================================================


def test_a_door_swing_failure_reads_like_a_sentence() -> None:
    room = make_room(openings=[inward_door()])
    analysis = analyze_fit(room, empty_scene(), proposal("desk", 3.4, 3.4, 1.0, 0.6))
    assert "open door" in analysis.cause
    assert analysis.cause.endswith(".")


def test_an_overlap_cause_names_the_thing_it_hits() -> None:
    scene = empty_scene([placed("monitor", 2.0, 1.0, 0.6, 0.25, locked=True)])
    analysis = analyze_fit(make_room(), scene, proposal("desk", 2.0, 1.0, 1.2, 0.6))
    assert "monitor" in analysis.cause


def test_an_unknown_cause_says_it_cannot_tell() -> None:
    door = Opening(
        opening_id="door_main", wall_id="wall_s", type="door",
        offset_m=0.2, width_m=0.9, height_m=2.03, hinge=None,
    )
    analysis = analyze_fit(
        make_room(openings=[door]), empty_scene(), proposal("desk", 2.0, 0.5, 1.0, 0.6)
    )
    assert "Cannot tell" in analysis.cause


def test_a_fitting_placement_says_so_with_its_clearance() -> None:
    analysis = analyze_fit(make_room(), empty_scene(), proposal("desk", 2.0, 1.0, 1.2, 0.6))
    assert "fits" in analysis.cause
    assert "m between" in analysis.cause


def test_every_cause_is_one_sentence() -> None:
    cases = [
        proposal("desk", 2.0, 1.0, 1.2, 0.6),
        proposal("desk", 3.8, 2.0, 1.2, 0.6),
    ]
    for placement in cases:
        analysis = analyze_fit(make_room(openings=[inward_door()]), empty_scene(), placement)
        assert analysis.cause.count(".") <= 2
        assert "\n" not in analysis.cause


# ==========================================================================
# Chair pull-back
# ==========================================================================


def chair(x: float, y: float, pullback=None, facing=None) -> ProposedPlacement:
    return ProposedPlacement(
        object_id="obj_chair",
        pose=Pose(x_m=x, y_m=y),
        footprint=Rect2D(x_m=x, y_m=y, width_m=0.6, depth_m=0.6),
        category="chair",
        pullback_m=pullback,
        facing_deg=facing,
    )


def extra(analysis, name: str):
    return next((c for c in analysis.extra_checks if c["name"] == name), None)


def test_a_chair_with_room_to_pull_back_passes() -> None:
    analysis = analyze_fit(
        make_room(openings=[inward_door()]), empty_scene(), chair(2.5, 1.0, pullback=0.6)
    )
    check = extra(analysis, "chair_pullback")
    assert check is not None and check["result"] == "pass"
    assert analysis.report.result == "fits"


def test_a_chair_with_no_pullback_distance_is_unknown() -> None:
    analysis = analyze_fit(make_room(), empty_scene(), chair(2.0, 2.0))
    check = extra(analysis, "chair_pullback")
    assert check["result"] == "unknown"
    assert check["missing"] == "pullback_m"
    assert analysis.report.result == "unknown"
    assert any("pullback_m" in reason for reason in analysis.report.unknown_reasons)


def test_a_chair_pullback_into_the_door_swing_fails() -> None:
    analysis = analyze_fit(
        make_room(openings=[inward_door()]), empty_scene(), chair(3.5, 2.55, pullback=0.6)
    )
    check = extra(analysis, "chair_pullback")
    assert check["result"] == "fail"
    assert "swing" in check["detail"]
    assert analysis.report.result == "does_not_fit"


def test_a_chair_pullback_through_a_wall_fails() -> None:
    analysis = analyze_fit(make_room(), empty_scene(), chair(2.0, 3.6, pullback=0.8))
    check = extra(analysis, "chair_pullback")
    assert check["result"] == "fail"
    assert "outside the room" in check["detail"]


def test_the_pullback_region_is_returned_for_drawing() -> None:
    analysis = analyze_fit(make_room(), empty_scene(), chair(2.0, 2.0, pullback=0.5))
    regions = analysis.geometry.get("extensions", [])
    assert any(r["kind"] == "chair_pullback" and r["depth_m"] == 0.5 for r in regions)


def test_an_object_that_is_not_a_chair_gets_no_pullback_check() -> None:
    analysis = analyze_fit(make_room(), empty_scene(), proposal("desk", 2.0, 2.0, 1.2, 0.6))
    assert extra(analysis, "chair_pullback") is None


# ==========================================================================
# Drawer extension
# ==========================================================================


def drawers(x: float, y: float, extension=None, facing=None) -> ProposedPlacement:
    return ProposedPlacement(
        object_id="obj_drawers",
        pose=Pose(x_m=x, y_m=y),
        footprint=Rect2D(x_m=x, y_m=y, width_m=0.4, depth_m=0.5),
        category="drawer_unit",
        drawer_extension_m=extension,
        facing_deg=facing,
    )


def test_a_drawer_with_room_to_open_passes() -> None:
    analysis = analyze_fit(make_room(), empty_scene(), drawers(2.0, 2.0, extension=0.5))
    check = extra(analysis, "drawer_extension")
    assert check["result"] == "pass"


def test_a_drawer_with_no_extension_depth_is_unknown() -> None:
    analysis = analyze_fit(make_room(), empty_scene(), drawers(2.0, 2.0))
    check = extra(analysis, "drawer_extension")
    assert check["result"] == "unknown"
    assert check["missing"] == "drawer_extension_m"
    assert analysis.report.result == "unknown"


def test_a_drawer_opening_into_a_wall_fails() -> None:
    analysis = analyze_fit(make_room(), empty_scene(), drawers(2.0, 3.75, extension=0.5))
    check = extra(analysis, "drawer_extension")
    assert check["result"] == "fail"
    assert "outside the room" in check["detail"]
    assert analysis.report.result == "does_not_fit"


def test_a_drawer_opening_into_another_object_fails() -> None:
    scene = empty_scene([placed("obj_box", 2.0, 2.9, 1.0, 0.4)])
    analysis = analyze_fit(make_room(), scene, drawers(2.0, 2.0, extension=0.6))
    check = extra(analysis, "drawer_extension")
    assert check["result"] == "fail"
    assert "hits" in check["detail"]


def test_the_facing_direction_changes_where_the_drawer_opens() -> None:
    """Facing the other way turns a wall collision into a clear opening."""
    into_wall = analyze_fit(make_room(), empty_scene(), drawers(2.0, 3.75, extension=0.5))
    away = analyze_fit(
        make_room(), empty_scene(), drawers(2.0, 3.75, extension=0.5, facing=180.0)
    )
    assert extra(into_wall, "drawer_extension")["result"] == "fail"
    assert extra(away, "drawer_extension")["result"] == "pass"


# ==========================================================================
# Determinism
# ==========================================================================


def test_the_full_analysis_is_byte_identical_across_runs() -> None:
    import json

    room = make_room(openings=[inward_door()])
    scene = empty_scene([placed("shelf", 1.0, 1.0, 0.8, 0.4)])
    placement = proposal("desk", 3.4, 3.4, 1.0, 0.6)

    first = analyze_fit(room, scene, placement).as_dict()
    second = analyze_fit(room, scene, placement).as_dict()
    first["fit_report"].pop("created_at")
    second["fit_report"].pop("created_at")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_report_from_analyze_matches_check_fit() -> None:
    from fit.engine import check_fit

    room = make_room(openings=[inward_door()])
    placement = proposal("desk", 3.4, 3.4, 1.0, 0.6)
    direct = check_fit(room, empty_scene(), placement)
    through = analyze_fit(room, empty_scene(), placement).report
    assert direct.model_dump(exclude={"created_at"}) == through.model_dump(
        exclude={"created_at"}
    )
