"""The fit analysis: a FitReport plus the geometry lane B needs to draw it.

`check_fit` in fit/engine.py returns the FitReport alone, because that is the
shape the contract fixes. This module computes the same verdict and keeps
everything else as well: the overlap polygon, the door's swept area at each
angle tested, the walking corridor and where it narrows, the two things a
clearance was measured between, and a plain-English cause.

None of that extra material is in the FitReport, because adding a field to a
contract needs the owner's approval. It travels in `FitAnalysis.extra` and in
the `extra` block of the command line output instead. The proposed contract
addition is written up in docs/FIT_ENGINE.md.

Two checks from the PRD live here too, for the same reason: `FitCheck.name` is
a closed set that has no room for them.

  chair pull-back    the space a seat needs behind it must stay inside the
                     room and clear of the door swing and the walking route
  drawer extension   an open drawer must not hit a wall, another object or
                     the door swing

Both answer `unknown` and name the missing datum when the pull-back distance
or extension depth was not supplied. Where one of them does fail, the verdict
is still expressed through the existing FitCheck names, because a pulled-back
chair fouling the door really is a door_swing conflict and saying so is
accurate rather than a workaround.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from contracts.catalog_offer import CatalogOffer
from contracts.common import Pose, Rect2D
from contracts.fit_report import FitCheck, FitReport, RejectedPlacement
from contracts.room_spec import RoomSpec
from contracts.scene_revision import SceneRevision
from fit import (
    DEFAULT_CLEARANCE_MARGIN_M,
    DEFAULT_DOOR_SWING_STEPS,
    DEFAULT_MIN_WALKWAY_WIDTH_M,
    SOLVER_VERSION,
)
from fit.geometry import (
    Polygon,
    closest_points,
    corridor_width_profile,
    distance_to_room_edge,
    door_angles,
    door_hinge_point,
    door_leaf_segment,
    door_swing_polygon,
    inward_normal,
    normalise,
    polygon_contains,
    polygon_distance,
    polygon_intersection,
    polygons_overlap,
    rect_corners,
    rotate,
    round_polygon,
    subtract,
    wall_endpoints,
)


def _jsonable(value: Any) -> Any:
    """Recursively turn tuples into lists, leaving everything else alone."""
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


@dataclass
class Clearance:
    """A measured gap, and what it was measured between."""

    metres: float
    between: tuple[str, str]
    from_point: tuple[float, float]
    to_point: tuple[float, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "metres": round(self.metres, 4),
            "between": list(self.between),
            "from_point": list(self.from_point),
            "to_point": list(self.to_point),
        }


@dataclass
class FitAnalysis:
    """Everything the engine worked out. `report` is the contract shape."""

    report: FitReport
    geometry: dict[str, Any] = field(default_factory=dict)
    extra_checks: list[dict[str, Any]] = field(default_factory=list)
    cause: str = ""

    def as_dict(self) -> dict[str, Any]:
        """A plain JSON-ready document.

        Tuples are converted to lists so that calling this in process gives
        exactly what the command line writes. Without that, a sandbox result
        and a server result would differ in type while looking identical, and
        the test that compares them would be comparing the wrong things.
        """
        return _jsonable(
            {
                "fit_report": self.report.model_dump(mode="json"),
                "extra": {
                    "geometry": self.geometry,
                    "extra_checks": self.extra_checks,
                    "cause": self.cause,
                },
                "solver_version": SOLVER_VERSION,
            }
        )


class ProposedPlacement:
    """What the caller wants to put down.

    Defined here rather than imported from contracts.interfaces so fit/
    depends on nothing but the contract data models. The optional fields
    below drive the two extra checks and are read with getattr, so a
    contracts.interfaces.ProposedPlacement works too; it simply has no
    pull-back or drawer data and those checks report unknown.
    """

    def __init__(
        self,
        object_id: str,
        pose: Pose,
        footprint: Optional[Rect2D] = None,
        height_m: Optional[float] = None,
        offer_ref: Optional[str] = None,
        replaces_object_id: Optional[str] = None,
        *,
        category: Optional[str] = None,
        pullback_m: Optional[float] = None,
        drawer_extension_m: Optional[float] = None,
        facing_deg: Optional[float] = None,
    ) -> None:
        self.object_id = object_id
        self.pose = pose
        self.footprint = footprint
        self.height_m = height_m
        self.offer_ref = offer_ref
        self.replaces_object_id = replaces_object_id
        self.category = category
        self.pullback_m = pullback_m
        self.drawer_extension_m = drawer_extension_m
        self.facing_deg = facing_deg


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _report_id(room_revision: int, scene_revision: int, object_id: str, pose: Pose) -> str:
    seed = f"{room_revision}:{scene_revision}:{object_id}:{pose.x_m}:{pose.y_m}:{pose.rotation_deg}"
    return "fit_" + hashlib.sha256(seed.encode()).hexdigest()[:16]


def _footprint_polygon(pose: Pose, footprint: Rect2D) -> Polygon:
    return rect_corners(
        pose.x_m, pose.y_m, footprint.width_m, footprint.depth_m, pose.rotation_deg
    )


def _offer_for(
    placement: ProposedPlacement, offers: Optional[Sequence[CatalogOffer]]
) -> Optional[CatalogOffer]:
    offer_ref = getattr(placement, "offer_ref", None)
    if not offer_ref or not offers:
        return None
    for offer in offers:
        if offer.offer_id == offer_ref:
            return offer
    return None


def _resolve_footprint(
    placement: ProposedPlacement, offer: Optional[CatalogOffer]
) -> tuple[Optional[Rect2D], list[str], bool]:
    if placement.footprint is not None:
        inferred = bool(offer and offer.dimension_source == "listing_text_inferred")
        return placement.footprint, [], inferred

    if offer is None:
        return None, ["no footprint supplied and no CatalogOffer to take dimensions from"], False

    if offer.dimensions is None or not offer.dimensions.has_footprint:
        axis_state = (
            "no dimensions at all"
            if offer.dimensions is None
            else f"width_m={offer.dimensions.width_m}, depth_m={offer.dimensions.depth_m}"
        )
        return (
            None,
            [
                f"product {offer.variant_id} has no documented footprint "
                f"({axis_state}); dimension_source is {offer.dimension_source}"
            ],
            False,
        )

    footprint = Rect2D(
        x_m=placement.pose.x_m,
        y_m=placement.pose.y_m,
        width_m=offer.dimensions.width_m or 0.0,
        depth_m=offer.dimensions.depth_m or 0.0,
        rotation_deg=placement.pose.rotation_deg,
    )
    return footprint, [], offer.dimension_source == "listing_text_inferred"


def _room_scale_is_estimated(room_spec: RoomSpec) -> bool:
    return any(
        measurement.evidence_status != "confirmed"
        for measurement in (room_spec.dimensions_m.width_m, room_spec.dimensions_m.depth_m)
    )


def _extension_polygon(
    pose: Pose, footprint: Rect2D, depth_m: float, facing_deg: Optional[float]
) -> Polygon:
    """The strip a drawer or a pulled-back seat sweeps in front of an object.

    "Front" is the object's own facing, defaulting to its rotation. The strip
    starts at the front face and runs outward by depth_m.
    """
    heading = pose.rotation_deg if facing_deg is None else facing_deg
    direction = rotate((0.0, 1.0), heading)
    offset = footprint.depth_m / 2.0 + depth_m / 2.0
    centre_x = pose.x_m + direction[0] * offset
    centre_y = pose.y_m + direction[1] * offset
    return rect_corners(centre_x, centre_y, footprint.width_m, depth_m, heading)


def _nearest_wall_point(
    polygon: Polygon, room_width: float, room_depth: float
) -> tuple[tuple[float, float], tuple[float, float], str]:
    """Which wall is closest, and the two points the gap spans.

    Reported instead of a bare number so a reader can see where the
    measurement was taken, and so lane B can draw the dimension line.
    """
    best = (float("inf"), (0.0, 0.0), (0.0, 0.0), "wall_n")
    for x, y in polygon:
        for distance, point, wall in (
            (y, (x, 0.0), "wall_n"),
            (room_width - x, (room_width, y), "wall_e"),
            (room_depth - y, (x, room_depth), "wall_s"),
            (x, (0.0, y), "wall_w"),
        ):
            if distance < best[0]:
                best = (distance, (round(x, 4), round(y, 4)), (round(point[0], 4), round(point[1], 4)), wall)
    return best[1], best[2], best[3]


def _plain_cause(
    result: str, checks: list[FitCheck], clearance: Optional[Clearance], label: str
) -> str:
    """One sentence a person can read."""
    if result == "fits":
        if clearance:
            return (
                f"{label} fits, with {clearance.metres:.2f} m between "
                f"{clearance.between[0]} and {clearance.between[1]}."
            )
        return f"{label} fits."

    failing = next((c for c in checks if c.result == "fail"), None)
    tight = next((c for c in checks if c.result == "tight"), None)
    unknown = next((c for c in checks if c.result == "unknown"), None)

    if result == "does_not_fit" and failing is not None:
        if failing.name == "door_swing":
            overlap = failing.measured_clearance_m
            by = f" by {abs(overlap):.2f} m" if overlap else ""
            return f"The open door would hit {label}{by}."
        if failing.name in ("object_overlap", "locked_object_overlap"):
            blocked = ", ".join(failing.blocking_ids) or "another item"
            return f"{label} would overlap {blocked}."
        if failing.name == "room_bounds":
            return f"{label} would stick out of the room."
        if failing.name == "walking_route":
            return f"{label} would block the walking route."
        if failing.name == "free_zone_containment":
            return f"{label} would sit in an area that must stay clear."
        return f"{label} does not fit: {failing.detail}"

    if result == "tight" and tight is not None:
        gap = tight.measured_clearance_m
        measured = f" Only {gap:.2f} m to spare." if gap is not None else ""
        return f"{label} fits, but barely.{measured}"

    if result == "unknown" and unknown is not None:
        return f"Cannot tell whether {label} fits: {unknown.detail}"

    return f"{label}: {result}."


# --------------------------------------------------------------------------
# The analysis
# --------------------------------------------------------------------------


def analyze_fit(
    room_spec: RoomSpec,
    scene: SceneRevision,
    placement: ProposedPlacement,
    offers: Optional[Sequence[CatalogOffer]] = None,
    *,
    clearance_margin_m: float = DEFAULT_CLEARANCE_MARGIN_M,
    min_walkway_width_m: float = DEFAULT_MIN_WALKWAY_WIDTH_M,
    door_swing_steps: int = DEFAULT_DOOR_SWING_STEPS,
) -> FitAnalysis:
    """Decide whether a placement fits, and keep the working."""
    room_width = room_spec.dimensions_m.width_m.value_m
    room_depth = room_spec.dimensions_m.depth_m.value_m

    assumptions = [
        f"clearance margin {clearance_margin_m} m",
        f"minimum walkway width {min_walkway_width_m} m",
        f"door swing tested at {door_swing_steps} intermediate angles",
        "room-local metres, walls traversed clockwise from wall_n",
    ]

    checks: list[FitCheck] = []
    unknown_reasons: list[str] = []
    evidence: list[str] = []
    clearances: list[Clearance] = []
    geometry: dict[str, Any] = {
        "room": {"width_m": room_width, "depth_m": room_depth},
        "overlaps": [],
        "door_swings": [],
        "corridors": [],
    }
    extra_checks: list[dict[str, Any]] = []

    label = getattr(placement, "category", None) or placement.object_id

    offer = _offer_for(placement, offers)
    if offer is not None:
        evidence.append(offer.offer_id)

    footprint, missing, dimensions_inferred = _resolve_footprint(placement, offer)
    if dimensions_inferred:
        assumptions.append(
            "product dimensions were read out of listing text, so this result is tentative"
        )

    scale_estimated = _room_scale_is_estimated(room_spec)
    if scale_estimated:
        assumptions.append("room width or depth is still estimated, so this result is tentative")

    tentative = dimensions_inferred or scale_estimated

    if footprint is None:
        report = FitReport(
            report_id=_report_id(
                room_spec.room_revision, scene.scene_revision, placement.object_id, placement.pose
            ),
            room_revision=room_spec.room_revision,
            scene_revision=scene.scene_revision,
            solver_version=SOLVER_VERSION,
            assumptions=assumptions,
            checks_run=[
                FitCheck(
                    name="room_bounds",
                    result="unknown",
                    detail="Cannot test anything without a footprint.",
                )
            ],
            minimum_clearance_m=None,
            rejected_placement=None,
            evidence=evidence,
            result="unknown",
            tentative=tentative,
            unknown_reasons=missing,
        )
        return FitAnalysis(
            report=report,
            geometry=geometry,
            extra_checks=extra_checks,
            cause=f"Cannot tell whether {label} fits: {missing[0] if missing else 'data is missing'}",
        )

    polygon = _footprint_polygon(placement.pose, footprint)
    geometry["footprint"] = round_polygon(polygon)

    # ------------------------------------------------------------- bounds
    edge_gap = distance_to_room_edge(polygon, room_width, room_depth)
    if edge_gap < -1e-6:
        checks.append(
            FitCheck(
                name="room_bounds",
                result="fail",
                detail=(
                    f"The footprint extends {abs(edge_gap):.3f} m outside the "
                    f"{room_width} by {room_depth} m room."
                ),
                measured_clearance_m=0.0,
            )
        )
    else:
        corner, wall_point, wall_id = _nearest_wall_point(polygon, room_width, room_depth)
        clearances.append(
            Clearance(edge_gap, (placement.object_id, wall_id), corner, wall_point)
        )
        checks.append(
            FitCheck(
                name="room_bounds",
                result="pass" if edge_gap >= clearance_margin_m else "tight",
                detail=f"Nearest wall is {edge_gap:.3f} m away.",
                measured_clearance_m=edge_gap,
            )
        )

    # --------------------------------------------------------- free zones
    placement_areas = [z for z in room_spec.free_zones if z.purpose == "placement_area"]
    if placement_areas:
        contained = any(
            polygon_contains([(p.x_m, p.y_m) for p in zone.area.points], polygon)
            for zone in placement_areas
        )
        checks.append(
            FitCheck(
                name="free_zone_containment",
                result="pass" if contained else "fail",
                detail=(
                    "Inside a permitted placement area."
                    if contained
                    else "Outside every permitted placement area."
                ),
                blocking_ids=[] if contained else [z.zone_id for z in placement_areas],
            )
        )

    for zone in [z for z in room_spec.free_zones if z.purpose == "keep_clear"]:
        zone_polygon = [(p.x_m, p.y_m) for p in zone.area.points]
        if polygons_overlap(polygon, zone_polygon, margin=clearance_margin_m):
            overlap = polygon_intersection(polygon, zone_polygon)
            if overlap:
                geometry["overlaps"].append(
                    {"with": zone.zone_id, "kind": "keep_clear", "polygon": overlap}
                )
            checks.append(
                FitCheck(
                    name="free_zone_containment",
                    result="fail",
                    detail=f"Intrudes into the keep-clear zone {zone.zone_id}.",
                    measured_clearance_m=0.0,
                    blocking_ids=[zone.zone_id],
                )
            )

    # ------------------------------------------------------------ overlap
    for existing in scene.placements:
        if existing.object_id == placement.object_id:
            continue
        if existing.object_id == getattr(placement, "replaces_object_id", None):
            continue
        other = _footprint_polygon(existing.pose, existing.footprint)
        name = "locked_object_overlap" if existing.locked else "object_overlap"

        if polygons_overlap(polygon, other):
            overlap = polygon_intersection(polygon, other)
            if overlap:
                geometry["overlaps"].append(
                    {
                        "with": existing.object_id,
                        "kind": "locked_item" if existing.locked else "item",
                        "polygon": overlap,
                    }
                )
            checks.append(
                FitCheck(
                    name=name,
                    result="fail",
                    detail=f"Overlaps {existing.label} ({existing.object_id}).",
                    measured_clearance_m=0.0,
                    blocking_ids=[existing.object_id],
                )
            )
            continue

        from_point, to_point, gap = closest_points(polygon, other)
        clearances.append(
            Clearance(gap, (placement.object_id, existing.object_id), from_point, to_point)
        )
        if gap < clearance_margin_m:
            checks.append(
                FitCheck(
                    name=name,
                    result="tight",
                    detail=(
                        f"Only {gap:.3f} m from {existing.label}, below the "
                        f"{clearance_margin_m} m margin."
                    ),
                    measured_clearance_m=gap,
                    blocking_ids=[existing.object_id],
                )
            )
        else:
            checks.append(
                FitCheck(
                    name=name,
                    result="pass",
                    detail=f"{gap:.3f} m clear of {existing.label}.",
                    measured_clearance_m=gap,
                )
            )

    # ------------------------------------------------------- door swings
    door_regions: list[tuple[str, Polygon]] = []
    for opening in room_spec.openings:
        if opening.type != "door":
            continue
        hinge_spec = opening.hinge

        if hinge_spec is None:
            unknown_reasons.append(f"door {opening.opening_id} has no hinge information")
            checks.append(
                FitCheck(
                    name="door_swing",
                    result="unknown",
                    detail=f"No hinge recorded for {opening.opening_id}.",
                    blocking_ids=[opening.opening_id],
                )
            )
            continue
        if hinge_spec.swing_direction in ("unknown", None):
            unknown_reasons.append(f"door {opening.opening_id} has no swing direction")
            checks.append(
                FitCheck(
                    name="door_swing",
                    result="unknown",
                    detail=f"Swing direction for {opening.opening_id} is unknown.",
                    blocking_ids=[opening.opening_id],
                )
            )
            continue
        if hinge_spec.hinge_side == "unknown":
            unknown_reasons.append(f"door {opening.opening_id} has no hinge side")
            checks.append(
                FitCheck(
                    name="door_swing",
                    result="unknown",
                    detail=f"Hinge side for {opening.opening_id} is unknown.",
                    blocking_ids=[opening.opening_id],
                )
            )
            continue
        if hinge_spec.swing_direction in ("sliding", "none"):
            checks.append(
                FitCheck(
                    name="door_swing",
                    result="pass",
                    detail=f"{opening.opening_id} is {hinge_spec.swing_direction}; nothing sweeps.",
                )
            )
            continue
        if hinge_spec.swing_direction == "outward":
            checks.append(
                FitCheck(
                    name="door_swing",
                    result="pass",
                    detail=f"{opening.opening_id} opens away from the room.",
                )
            )
            continue

        hinge = door_hinge_point(
            opening.wall_id,
            opening.offset_m,
            opening.width_m,
            hinge_spec.hinge_side,
            room_width,
            room_depth,
        )
        start, end = wall_endpoints(opening.wall_id, room_width, room_depth)
        along = normalise(subtract(end, start))
        closed = along if hinge_spec.hinge_side == "left" else (-along[0], -along[1])
        sweep = inward_normal(opening.wall_id)

        swept = door_swing_polygon(
            hinge, closed, sweep, opening.width_m, hinge_spec.swing_angle_max_deg, door_swing_steps
        )
        door_regions.append((opening.opening_id, swept))
        evidence.append(opening.opening_id)
        if hinge_spec.motion_evidence_ref:
            evidence.append(hinge_spec.motion_evidence_ref)

        leaves = []
        for angle in door_angles(hinge_spec.swing_angle_max_deg, door_swing_steps):
            leaf_start, leaf_end = door_leaf_segment(
                hinge, closed, sweep, opening.width_m, angle
            )
            leaves.append(
                {
                    "angle_deg": round(angle, 2),
                    "leaf": [list(leaf_start), list(leaf_end)],
                    "hits_placement": polygon_distance(polygon, [leaf_start, leaf_end])
                    <= clearance_margin_m,
                }
            )
        geometry["door_swings"].append(
            {
                "opening_id": opening.opening_id,
                "hinge": list(hinge),
                "max_angle_deg": hinge_spec.swing_angle_max_deg,
                "swept_polygon": round_polygon(swept),
                "angles": leaves,
            }
        )

        if polygons_overlap(polygon, swept, margin=clearance_margin_m):
            blocking_angle = next(
                (leaf["angle_deg"] for leaf in leaves if leaf["hits_placement"]), None
            )
            overlap = polygon_intersection(polygon, swept)
            depth = 0.0
            if overlap:
                geometry["overlaps"].append(
                    {"with": opening.opening_id, "kind": "door_swing", "polygon": overlap}
                )
                _a, _b, depth = closest_points(overlap, polygon)
                xs = [p[0] for p in overlap]
                ys = [p[1] for p in overlap]
                depth = min(max(xs) - min(xs), max(ys) - min(ys))
            where = f" at {blocking_angle:.0f} degrees" if blocking_angle is not None else ""
            checks.append(
                FitCheck(
                    name="door_swing",
                    result="fail",
                    detail=(
                        f"The footprint is inside the swept area of "
                        f"{opening.opening_id}{where}."
                    ),
                    measured_clearance_m=round(depth, 4),
                    blocking_ids=[opening.opening_id],
                )
            )
        else:
            from_point, to_point, gap = closest_points(polygon, swept)
            clearances.append(
                Clearance(gap, (placement.object_id, opening.opening_id), from_point, to_point)
            )
            checks.append(
                FitCheck(
                    name="door_swing",
                    result="pass" if gap >= clearance_margin_m else "tight",
                    detail=f"{gap:.3f} m clear of the swing of {opening.opening_id}.",
                    measured_clearance_m=gap,
                )
            )

    # ----------------------------------------------------- walking routes
    routes = [z for z in room_spec.free_zones if z.purpose == "walking_route"]
    for route in routes:
        route_polygon = [(p.x_m, p.y_m) for p in route.area.points]
        obstacles = [polygon] + [
            _footprint_polygon(p.pose, p.footprint)
            for p in scene.placements
            if p.object_id != placement.object_id
        ]
        profile = corridor_width_profile(route_polygon, obstacles)
        narrowest = min(profile, key=lambda s: s["free_width_m"]) if profile else None
        geometry["corridors"].append(
            {
                "zone_id": route.zone_id,
                "polygon": round_polygon(route_polygon),
                "required_width_m": min_walkway_width_m,
                "profile": profile,
                "narrowest": narrowest,
            }
        )

        if polygons_overlap(polygon, route_polygon):
            overlap = polygon_intersection(polygon, route_polygon)
            if overlap:
                geometry["overlaps"].append(
                    {"with": route.zone_id, "kind": "walking_route", "polygon": overlap}
                )
            remaining = narrowest["free_width_m"] if narrowest else 0.0
            checks.append(
                FitCheck(
                    name="walking_route",
                    result="fail",
                    detail=(
                        f"Blocks the route {route.zone_id}, which must stay "
                        f"{min_walkway_width_m} m wide; narrowest point is "
                        f"{remaining:.3f} m."
                    ),
                    measured_clearance_m=remaining,
                    blocking_ids=[route.zone_id],
                )
            )
            continue

        from_point, to_point, gap = closest_points(polygon, route_polygon)
        clearances.append(
            Clearance(gap, (placement.object_id, route.zone_id), from_point, to_point)
        )
        checks.append(
            FitCheck(
                name="walking_route",
                result="pass",
                detail=f"{gap:.3f} m clear of the route {route.zone_id}.",
                measured_clearance_m=gap,
            )
        )

    # ------------------------------------------- chair pull-back, drawers
    extra_checks.extend(
        _extension_checks(
            placement,
            footprint,
            scene,
            room_spec,
            door_regions,
            routes,
            room_width,
            room_depth,
            clearance_margin_m,
            checks,
            unknown_reasons,
            geometry,
        )
    )

    # ------------------------------------------------------------ verdict
    results = {check.result for check in checks}
    minimum = min(clearances, key=lambda c: c.metres) if clearances else None

    if "fail" in results:
        result = "does_not_fit"
    elif "unknown" in results:
        result = "unknown"
    elif "tight" in results:
        result = "tight"
    else:
        result = "fits"

    rejected = None
    if result == "does_not_fit":
        failing = next(c for c in checks if c.result == "fail")
        rejected = RejectedPlacement(
            object_id=placement.object_id,
            pose=placement.pose,
            footprint=Rect2D(
                x_m=placement.pose.x_m,
                y_m=placement.pose.y_m,
                width_m=footprint.width_m,
                depth_m=footprint.depth_m,
                rotation_deg=placement.pose.rotation_deg,
            ),
            cause=failing.detail,
            failing_check=failing.name,
        )

    if result == "unknown" and not unknown_reasons:
        unknown_reasons.append("a required input was missing")

    if minimum is not None:
        geometry["minimum_clearance"] = minimum.as_dict()

    report = FitReport(
        report_id=_report_id(
            room_spec.room_revision, scene.scene_revision, placement.object_id, placement.pose
        ),
        room_revision=room_spec.room_revision,
        scene_revision=scene.scene_revision,
        solver_version=SOLVER_VERSION,
        assumptions=assumptions,
        checks_run=checks,
        minimum_clearance_m=minimum.metres if minimum else None,
        rejected_placement=rejected,
        evidence=sorted(set(evidence)),
        result=result,
        tentative=tentative,
        unknown_reasons=unknown_reasons if result == "unknown" else [],
    )

    return FitAnalysis(
        report=report,
        geometry=geometry,
        extra_checks=extra_checks,
        cause=_plain_cause(result, checks, minimum, label),
    )


def _extension_checks(
    placement: ProposedPlacement,
    footprint: Rect2D,
    scene: SceneRevision,
    room_spec: RoomSpec,
    door_regions: list[tuple[str, Polygon]],
    routes: list,
    room_width: float,
    room_depth: float,
    clearance_margin_m: float,
    checks: list[FitCheck],
    unknown_reasons: list[str],
    geometry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Chair pull-back and drawer extension.

    Each is skipped unless the category asks for it. When it is asked for but
    the measurement is missing, the result is unknown and the missing datum is
    named rather than assumed.
    """
    results: list[dict[str, Any]] = []
    category = (getattr(placement, "category", None) or "").lower()

    wants_pullback = category in ("chair", "seat", "stool", "desk_chair")
    wants_drawer = category in ("drawer_unit", "dresser", "cabinet", "nightstand")

    for kind, wanted, depth, datum in (
        ("chair_pullback", wants_pullback, getattr(placement, "pullback_m", None), "pullback_m"),
        (
            "drawer_extension",
            wants_drawer,
            getattr(placement, "drawer_extension_m", None),
            "drawer_extension_m",
        ),
    ):
        if not wanted:
            continue

        if depth is None or depth <= 0:
            message = (
                f"{kind} cannot be checked: {datum} was not supplied for "
                f"{placement.object_id}"
            )
            results.append({"name": kind, "result": "unknown", "detail": message, "missing": datum})
            unknown_reasons.append(message)
            checks.append(
                FitCheck(
                    name="room_bounds" if kind == "drawer_extension" else "walking_route",
                    result="unknown",
                    detail=message,
                    blocking_ids=[placement.object_id],
                )
            )
            continue

        region = _extension_polygon(
            placement.pose, footprint, depth, getattr(placement, "facing_deg", None)
        )
        geometry.setdefault("extensions", []).append(
            {"kind": kind, "depth_m": depth, "polygon": round_polygon(region)}
        )

        problems: list[str] = []
        blocking: list[str] = []

        edge_gap = distance_to_room_edge(region, room_width, room_depth)
        if edge_gap < -1e-6:
            problems.append(f"extends {abs(edge_gap):.3f} m outside the room")
            blocking.append("room_bounds")

        for existing in scene.placements:
            if existing.object_id in (
                placement.object_id,
                getattr(placement, "replaces_object_id", None),
            ):
                continue
            other = _footprint_polygon(existing.pose, existing.footprint)
            if polygons_overlap(region, other):
                problems.append(f"hits {existing.label}")
                blocking.append(existing.object_id)

        for opening_id, swept in door_regions:
            if polygons_overlap(region, swept, margin=clearance_margin_m):
                problems.append(f"hits the swing of {opening_id}")
                blocking.append(opening_id)

        for route in routes:
            route_polygon = [(p.x_m, p.y_m) for p in route.area.points]
            if polygons_overlap(region, route_polygon):
                problems.append(f"blocks the route {route.zone_id}")
                blocking.append(route.zone_id)

        if problems:
            detail = f"{kind.replace('_', ' ')} of {depth:.2f} m " + " and ".join(problems) + "."
            results.append(
                {"name": kind, "result": "fail", "detail": detail, "blocking_ids": blocking}
            )
            # Expressed through the closest existing check name, which is
            # accurate: a pulled-back chair fouling the door IS a door_swing
            # conflict. See the contract proposal in docs/FIT_ENGINE.md.
            mapped = "door_swing" if any("swing" in p for p in problems) else (
                "object_overlap" if any("hits" in p for p in problems) else "room_bounds"
            )
            checks.append(
                FitCheck(
                    name=mapped,
                    result="fail",
                    detail=detail,
                    measured_clearance_m=0.0,
                    blocking_ids=blocking,
                )
            )
        else:
            detail = f"{kind.replace('_', ' ')} of {depth:.2f} m is clear."
            results.append({"name": kind, "result": "pass", "detail": detail})

    return results
