"""check_fit: the one place a fit result is decided.

Pure geometry. No web, database or network imports, because the same code runs
on the server and inside the Argon and Carbon sandboxes and must produce an
identical FitReport from identical inputs.

Three rules the tests pin down:

  unknown wins. If a dimension, hinge or swing direction is missing, the
  result is unknown and the reasons say exactly what was missing. The engine
  never fills a gap with an assumption.

  estimated scale never yields a confident fits. If the room dimension the
  check depends on is still estimated, or the product dimensions were read
  out of listing text, the report is marked tentative.

  does_not_fit always carries the rejected placement and its cause, so the
  replay and the bounded repair have something concrete to work with.
"""

from __future__ import annotations

import hashlib
from typing import Optional, Sequence

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
    distance_to_room_edge,
    door_angles,
    door_hinge_point,
    door_leaf_segment,
    door_swing_polygon,
    inward_normal,
    normalise,
    point_on_wall,
    polygon_contains,
    polygon_distance,
    polygons_overlap,
    rect_corners,
    subtract,
    wall_endpoints,
)


class ProposedPlacement:
    """What the caller wants to put down.

    Defined here rather than imported from contracts.interfaces so that fit/
    depends on nothing but the contract data models.
    """

    def __init__(
        self,
        object_id: str,
        pose: Pose,
        footprint: Optional[Rect2D] = None,
        height_m: Optional[float] = None,
        offer_ref: Optional[str] = None,
        replaces_object_id: Optional[str] = None,
    ) -> None:
        self.object_id = object_id
        self.pose = pose
        self.footprint = footprint
        self.height_m = height_m
        self.offer_ref = offer_ref
        self.replaces_object_id = replaces_object_id


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
    if not placement.offer_ref or not offers:
        return None
    for offer in offers:
        if offer.offer_id == placement.offer_ref:
            return offer
    return None


def _resolve_footprint(
    placement: ProposedPlacement, offer: Optional[CatalogOffer]
) -> tuple[Optional[Rect2D], list[str], bool]:
    """Work out the footprint to test, and say what is missing if we cannot.

    Returns (footprint, missing_reasons, dimensions_are_inferred).
    """
    if placement.footprint is not None:
        inferred = bool(offer and offer.dimension_source == "listing_text_inferred")
        return placement.footprint, [], inferred

    if offer is None:
        return None, ["no footprint supplied and no CatalogOffer to take dimensions from"], False

    if offer.dimensions is None or not offer.dimensions.has_footprint:
        axis_state = (
            "no dimensions at all"
            if offer.dimensions is None
            else (
                f"width_m={offer.dimensions.width_m}, depth_m={offer.dimensions.depth_m}"
            )
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
        for measurement in (
            room_spec.dimensions_m.width_m,
            room_spec.dimensions_m.depth_m,
        )
    )


def check_fit(
    room_spec: RoomSpec,
    scene: SceneRevision,
    placement: ProposedPlacement,
    offers: Optional[Sequence[CatalogOffer]] = None,
    *,
    clearance_margin_m: float = DEFAULT_CLEARANCE_MARGIN_M,
    min_walkway_width_m: float = DEFAULT_MIN_WALKWAY_WIDTH_M,
    door_swing_steps: int = DEFAULT_DOOR_SWING_STEPS,
) -> FitReport:
    """Decide whether a placement fits, and say exactly why."""
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
    clearances: list[float] = []

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
        assumptions.append(
            "room width or depth is still estimated, so this result is tentative"
        )

    tentative = dimensions_inferred or scale_estimated

    if footprint is None:
        return FitReport(
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

    polygon = _footprint_polygon(placement.pose, footprint)

    # ---------------------------------------------------------------- bounds
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
        clearances.append(edge_gap)
        checks.append(
            FitCheck(
                name="room_bounds",
                result="pass" if edge_gap >= clearance_margin_m else "tight",
                detail=f"Nearest wall is {edge_gap:.3f} m away.",
                measured_clearance_m=edge_gap,
            )
        )

    # ------------------------------------------------------------ free zones
    placement_areas = [
        zone for zone in room_spec.free_zones if zone.purpose == "placement_area"
    ]
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

    keep_clear = [zone for zone in room_spec.free_zones if zone.purpose == "keep_clear"]
    for zone in keep_clear:
        zone_polygon = [(p.x_m, p.y_m) for p in zone.area.points]
        if polygons_overlap(polygon, zone_polygon, margin=clearance_margin_m):
            checks.append(
                FitCheck(
                    name="free_zone_containment",
                    result="fail",
                    detail=f"Intrudes into the keep-clear zone {zone.zone_id}.",
                    measured_clearance_m=0.0,
                    blocking_ids=[zone.zone_id],
                )
            )

    # --------------------------------------------------------------- overlap
    for existing in scene.placements:
        if existing.object_id == placement.object_id:
            continue
        if existing.object_id == placement.replaces_object_id:
            continue
        other = _footprint_polygon(existing.pose, existing.footprint)
        gap = polygon_distance(polygon, other)
        name = "locked_object_overlap" if existing.locked else "object_overlap"
        if polygons_overlap(polygon, other):
            checks.append(
                FitCheck(
                    name=name,
                    result="fail",
                    detail=f"Overlaps {existing.label} ({existing.object_id}).",
                    measured_clearance_m=0.0,
                    blocking_ids=[existing.object_id],
                )
            )
        elif gap < clearance_margin_m:
            clearances.append(gap)
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
            clearances.append(gap)
            checks.append(
                FitCheck(
                    name=name,
                    result="pass",
                    detail=f"{gap:.3f} m clear of {existing.label}.",
                    measured_clearance_m=gap,
                )
            )

    # ----------------------------------------------------------- door swings
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
            unknown_reasons.append(
                f"door {opening.opening_id} has no swing direction"
            )
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
        along_wall = normalise(subtract(end, start))
        closed = along_wall if hinge_spec.hinge_side == "left" else (-along_wall[0], -along_wall[1])
        sweep = inward_normal(opening.wall_id)

        swept = door_swing_polygon(
            hinge, closed, sweep, opening.width_m, hinge_spec.swing_angle_max_deg, door_swing_steps
        )
        evidence.append(opening.opening_id)
        if hinge_spec.motion_evidence_ref:
            evidence.append(hinge_spec.motion_evidence_ref)

        if polygons_overlap(polygon, swept, margin=clearance_margin_m):
            # Find the first angle at which the leaf actually meets the object,
            # which is far more useful in a report than "it overlaps".
            blocking_angle: Optional[float] = None
            for angle in door_angles(hinge_spec.swing_angle_max_deg, door_swing_steps):
                leaf_start, leaf_end = door_leaf_segment(
                    hinge, closed, sweep, opening.width_m, angle
                )
                if polygon_distance(polygon, [leaf_start, leaf_end]) <= clearance_margin_m:
                    blocking_angle = angle
                    break
            where = (
                f" at {blocking_angle:.0f} degrees" if blocking_angle is not None else ""
            )
            checks.append(
                FitCheck(
                    name="door_swing",
                    result="fail",
                    detail=(
                        f"The footprint is inside the swept area of "
                        f"{opening.opening_id}{where}."
                    ),
                    measured_clearance_m=0.0,
                    blocking_ids=[opening.opening_id],
                )
            )
        else:
            gap = polygon_distance(polygon, swept)
            clearances.append(gap)
            checks.append(
                FitCheck(
                    name="door_swing",
                    result="pass" if gap >= clearance_margin_m else "tight",
                    detail=f"{gap:.3f} m clear of the swing of {opening.opening_id}.",
                    measured_clearance_m=gap,
                )
            )

    # --------------------------------------------------------- walking route
    routes = [zone for zone in room_spec.free_zones if zone.purpose == "walking_route"]
    for route in routes:
        route_polygon = [(p.x_m, p.y_m) for p in route.area.points]
        if polygons_overlap(polygon, route_polygon):
            checks.append(
                FitCheck(
                    name="walking_route",
                    result="fail",
                    detail=(
                        f"Blocks the route {route.zone_id}, which must stay "
                        f"{min_walkway_width_m} m wide."
                    ),
                    measured_clearance_m=0.0,
                    blocking_ids=[route.zone_id],
                )
            )
            continue

        gap = polygon_distance(polygon, route_polygon)
        clearances.append(gap)
        checks.append(
            FitCheck(
                name="walking_route",
                result="pass",
                detail=f"{gap:.3f} m clear of the route {route.zone_id}.",
                measured_clearance_m=gap,
            )
        )

    # ----------------------------------------------------------------- verdict
    results = {check.result for check in checks}
    minimum_clearance = min(clearances) if clearances else None

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
        failing = next(check for check in checks if check.result == "fail")
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

    return FitReport(
        report_id=_report_id(
            room_spec.room_revision, scene.scene_revision, placement.object_id, placement.pose
        ),
        room_revision=room_spec.room_revision,
        scene_revision=scene.scene_revision,
        solver_version=SOLVER_VERSION,
        assumptions=assumptions,
        checks_run=checks,
        minimum_clearance_m=minimum_clearance,
        rejected_placement=rejected,
        evidence=sorted(set(evidence)),
        result=result,
        tentative=tentative,
        unknown_reasons=unknown_reasons if result == "unknown" else [],
    )
