"""Translate between Karishma's room contracts and ours.

Scope for this phase: `RoomSpec` and `Opening`/`DoorMotion` only.

Her models live in `ai/karishma/_vendor/kschemas/` and are private to her
code. Nothing of hers crosses into `contracts/`; everything that leaves the
adapter is one of our models.

The two dialects differ in three systematic ways:

  she uses `x`/`z`, we use `x_m`/`y_m`
  she flattens where we nest (`width_m` vs `dimensions_m.width_m`)
  she has no `session_id`, and her `source` is one field where we keep an
  evidence status and a source label apart

`LOSSY` below names every field that does not survive a round trip, and the
tests assert that list is accurate rather than aspirational.
"""

from __future__ import annotations

from typing import Any, Optional

from contracts.common import ColorSpec, Measurement, Point2D, Polygon2D, Rect2D
from contracts.room_spec import (
    ExistingItem,
    Floor,
    FreeZone,
    HingeSpec,
    Opening,
    RoomDimensions,
    RoomSpec,
    ScaleReference,
    Wall,
)

from ai.karishma._vendor import kschemas as k

WALL_IDS = ("wall_n", "wall_e", "wall_s", "wall_w")

# Her `Source` has eight values; our `EvidenceStatus` has four. These four
# extra ones collapse, and the collapse is not reversible.
SOURCE_TO_EVIDENCE: dict[str, str] = {
    "estimated": "estimated",
    "confirmed": "confirmed",
    "unknown": "unknown",
    "sampled_from_photo": "sampled_from_photo",
    "inferred": "estimated",
    "catalog_fact": "confirmed",
    "default": "estimated",
    "preset": "estimated",
}

EVIDENCE_TO_SOURCE: dict[str, str] = {
    "estimated": "estimated",
    "confirmed": "confirmed",
    "unknown": "unknown",
    "sampled_from_photo": "sampled_from_photo",
}

OPENING_TYPE_TO_OURS = {"door": "door", "window": "window", "opening": "archway"}
OPENING_TYPE_TO_HERS = {
    "door": "door",
    "window": "window",
    "archway": "opening",
    "pass_through": "opening",
}

# Hers is "in"/"out"; ours also has sliding, none and unknown.
SWING_TO_OURS = {"in": "inward", "out": "outward"}
SWING_TO_HERS = {"inward": "in", "outward": "out"}

DEFAULT_HEX = "#cccccc"

# Fields that do not survive a round trip, and why. The tests check this
# list is complete, so it cannot quietly go stale.
LOSSY: dict[str, str] = {
    "RoomSpec.session_id": "hers has no session; supplied by the caller and lost going back",
    "RoomSpec.scale_reference": "hers is a bare string, ours is an anchor with a length and a confirmed flag",
    "RoomSpec.created_at": "ours only",
    "RoomSpec.snapshot_ref": "ours only",
    "RoomSpec.source": "ours only; hers folds provenance into each field's `source`",
    "RoomSpec.status": "hers only (draft/validated/preset)",
    "RoomSpec.notes": "hers only",
    "Wall.length_m": "hers has none; derived from the room dimensions",
    "Wall.label": "ours only",
    "Floor.floor_id": "ours only",
    "Floor.material": "ours only",
    "Measurement.source": "ours splits evidence_status from source label; hers has one field",
    "Dimension.confidence": "hers only; ours keeps it on Measurement but hers is per-dimension",
    "FreeZone.purpose": "ours only; hers cannot distinguish a walking route from a keep-clear zone",
    "FreeZone.area": "hers is an axis-aligned rectangle; ours is a polygon",
    "ExistingItem.locked": "ours only",
    "ExistingItem.confidence": "hers only",
    "Opening.confidence": "hers only",
    "Opening.evidence_status": "ours only; derived from her motion source or confidence",
    "DoorMotion.evidence_ts": "a timestamp in the clip; ours is a reference to the clip",
    "HingeSpec.hinge_side=unknown": "hers cannot express unknown; absent motion is the only way",
    "HingeSpec.swing_direction=sliding|none|unknown": "hers is in/out only",
    "Source.inferred|catalog_fact|default|preset": "collapse into estimated or confirmed",
}


# --------------------------------------------------------------------------
# Door motion, which the fit engine depends on
# --------------------------------------------------------------------------


def door_motion_to_hinge(motion: Optional[Any]) -> Optional[HingeSpec]:
    """Her `DoorMotion` to our `HingeSpec`.

    `None` stays `None`. Our fit engine then answers `unknown` and names the
    missing datum, which is the correct outcome: she leaves motion unset
    precisely when Neon and Decision 1 did not agree.

    Handedness: her `hinge_side` is "left as seen from inside the room facing
    the door". Ours is "the end nearer the wall's start", traversing walls
    clockwise. Those coincide on all four walls, and the golden tests in
    tests/ai/test_door_goldens.py assert it per wall rather than trusting the
    derivation.
    """
    if motion is None:
        return None

    evidence_ts = getattr(motion, "evidence_ts", None)
    return HingeSpec(
        hinge_side=getattr(motion, "hinge_side", None) or "unknown",
        swing_direction=SWING_TO_OURS.get(getattr(motion, "swing", ""), "unknown"),
        swing_angle_max_deg=float(getattr(motion, "max_angle_deg", 90.0) or 90.0),
        evidence_status=SOURCE_TO_EVIDENCE.get(
            getattr(motion, "source", "estimated"), "estimated"
        ),
        motion_evidence_ref=(f"door_clip@{evidence_ts}" if evidence_ts else None),
    )


def hinge_to_door_motion(hinge: Optional[HingeSpec]) -> Optional[Any]:
    """Our `HingeSpec` back to her `DoorMotion`.

    Anything our side can say but hers cannot, meaning an unknown hinge side
    or a sliding door, becomes `None` rather than a guess. A wrong hinge is
    worse than a missing one: our engine answers `unknown` for a missing one
    and confidently wrong for a mirrored one.
    """
    if hinge is None:
        return None
    if hinge.hinge_side not in ("left", "right"):
        return None
    if hinge.swing_direction not in SWING_TO_HERS:
        return None

    return k.DoorMotion(
        hinge_side=hinge.hinge_side,
        swing=SWING_TO_HERS[hinge.swing_direction],
        max_angle_deg=hinge.swing_angle_max_deg,
        source=EVIDENCE_TO_SOURCE.get(hinge.evidence_status, "estimated"),
        confidence=1.0 if hinge.evidence_status == "confirmed" else 0.5,
        evidence_ts=None,
    )


def opening_to_ours(opening: Any) -> Opening:
    hinge = door_motion_to_hinge(getattr(opening, "motion", None))
    evidence = "estimated"
    if hinge is not None:
        evidence = hinge.evidence_status
    elif float(getattr(opening, "confidence", 0.0) or 0.0) >= 0.9:
        evidence = "confirmed"

    return Opening(
        opening_id=opening.id,
        wall_id=opening.wall,
        type=OPENING_TYPE_TO_OURS.get(opening.type, "archway"),
        offset_m=float(opening.offset_m),
        width_m=float(opening.width_m),
        height_m=float(opening.height_m),
        sill_m=float(getattr(opening, "sill_m", 0.0) or 0.0) or None,
        hinge=hinge,
        evidence_status=evidence,
    )


def opening_to_hers(opening: Opening) -> Any:
    return k.Opening(
        id=opening.opening_id,
        wall=opening.wall_id,
        type=OPENING_TYPE_TO_HERS.get(opening.type, "opening"),
        offset_m=opening.offset_m,
        width_m=opening.width_m,
        height_m=opening.height_m,
        sill_m=opening.sill_m or 0.0,
        motion=hinge_to_door_motion(opening.hinge),
        confidence=1.0 if opening.evidence_status == "confirmed" else 0.5,
    )


# --------------------------------------------------------------------------
# RoomSpec
# --------------------------------------------------------------------------


def _hex_or_default(value: Optional[str]) -> str:
    text = (value or "").strip()
    if len(text) == 7 and text.startswith("#"):
        try:
            int(text[1:], 16)
            return text
        except ValueError:
            pass
    return DEFAULT_HEX


def _measurement(dimension: Any) -> Measurement:
    return Measurement(
        value_m=float(dimension.value_m),
        evidence_status=SOURCE_TO_EVIDENCE.get(dimension.source, "estimated"),
        # Her single `source` field carried the evidence status, so the
        # source label is the honest "inferred" rather than a claim of live.
        source="inferred",
        confidence=float(getattr(dimension, "confidence", 0.0) or 0.0) or None,
    )


def _dimension(measurement: Measurement) -> Any:
    return k.Dimension(
        value_m=measurement.value_m,
        source=EVIDENCE_TO_SOURCE.get(measurement.evidence_status, "estimated"),
        confidence=measurement.confidence if measurement.confidence is not None else 0.5,
    )


def room_spec_to_ours(room: Any, *, session_id: str = "adapter") -> RoomSpec:
    """Her `RoomSpec` to ours.

    Two things are constructed rather than copied, because she has no field
    for them: wall lengths come from the room dimensions, and the floor is
    built from her single `floor_color_hex`.
    """
    width = float(room.width_m.value_m)
    depth = float(room.depth_m.value_m)

    her_walls = {w.id: w for w in (room.walls or [])}
    walls: list[Wall] = []
    for wall_id in WALL_IDS:
        her = her_walls.get(wall_id)
        walls.append(
            Wall(
                wall_id=wall_id,
                length_m=width if wall_id in ("wall_n", "wall_s") else depth,
                color=ColorSpec(
                    hex=_hex_or_default(getattr(her, "color_hex", None)),
                    evidence_status=SOURCE_TO_EVIDENCE.get(
                        getattr(her, "color_source", "estimated"), "estimated"
                    ),
                    # source is a SourceLabel, not an evidence status.
                    # "sampled_from_photo" is the evidence status above.
                    source="inferred",
                ),
            )
        )

    free_zones = [
        FreeZone(
            zone_id=f"zone_{index}",
            label=zone.label or f"zone_{index}",
            area=Polygon2D(
                points=[
                    Point2D(x_m=min(zone.x0, zone.x1), y_m=min(zone.z0, zone.z1)),
                    Point2D(x_m=max(zone.x0, zone.x1), y_m=min(zone.z0, zone.z1)),
                    Point2D(x_m=max(zone.x0, zone.x1), y_m=max(zone.z0, zone.z1)),
                    Point2D(x_m=min(zone.x0, zone.x1), y_m=max(zone.z0, zone.z1)),
                ]
            ),
            # She has no purpose field, so everything becomes keep_clear.
            # A walking route cannot survive the crossing; see LOSSY.
            purpose="keep_clear",
        )
        for index, zone in enumerate(room.free_zones or [])
    ]

    items = [
        ExistingItem(
            item_id=item.id,
            label=item.label,
            footprint=Rect2D(x_m=item.x, y_m=item.z, width_m=item.w, depth_m=item.d),
            height_m=item.h or None,
            locked=False,
            keep=bool(item.keep),
            evidence_status="confirmed"
            if float(getattr(item, "confidence", 0.0) or 0.0) >= 0.9
            else "estimated",
        )
        for item in (room.existing_items or [])
    ]

    return RoomSpec(
        room_revision=int(room.room_revision),
        session_id=session_id,
        dimensions_m=RoomDimensions(
            width_m=_measurement(room.width_m),
            depth_m=_measurement(room.depth_m),
            height_m=_measurement(room.height_m),
        ),
        scale_reference=_scale_reference_to_ours(room),
        walls=walls,
        floor=Floor(color=ColorSpec(
            hex=_hex_or_default(getattr(room, "floor_color_hex", None)),
            evidence_status="sampled_from_photo",
            source="inferred",
        )),
        openings=[opening_to_ours(o) for o in (room.openings or [])],
        existing_items=items,
        free_zones=free_zones,
        evidence_status=SOURCE_TO_EVIDENCE.get(room.width_m.source, "estimated"),
        source="generated",
    )


def _scale_reference_to_ours(room: Any) -> Optional[ScaleReference]:
    """Her scale_reference is a bare string, so most of ours is unknowable.

    It is carried as a description with the confirmed width as the length
    when the width is confirmed, and dropped otherwise rather than invented.
    """
    text = getattr(room, "scale_reference", None)
    if not text:
        return None
    confirmed = room.width_m.source == "confirmed"
    if not confirmed:
        return None
    return ScaleReference(
        anchor_id="anchor_from_karishma",
        description=str(text),
        length_m=float(room.width_m.value_m),
        confirmed=True,
        evidence_status="confirmed",
        measures="width",
    )


def room_spec_to_hers(room: RoomSpec) -> Any:
    """Ours back to hers. Only for feeding her own code."""
    return k.RoomSpec(
        room_revision=room.room_revision,
        width_m=_dimension(room.dimensions_m.width_m),
        depth_m=_dimension(room.dimensions_m.depth_m),
        height_m=_dimension(room.dimensions_m.height_m),
        scale_reference=(
            room.scale_reference.description if room.scale_reference else None
        ),
        walls=[
            k.Wall(
                id=wall.wall_id,
                color_hex=wall.color.hex,
                color_source=EVIDENCE_TO_SOURCE.get(
                    wall.color.evidence_status, "estimated"
                ),
            )
            for wall in room.walls
        ],
        openings=[opening_to_hers(o) for o in room.openings],
        existing_items=[
            k.ExistingItem(
                id=item.item_id,
                label=item.label,
                x=item.footprint.x_m if item.footprint else 0.0,
                z=item.footprint.y_m if item.footprint else 0.0,
                w=item.footprint.width_m if item.footprint else 0.0,
                d=item.footprint.depth_m if item.footprint else 0.0,
                h=item.height_m or 0.0,
                keep=item.keep,
                confidence=1.0 if item.evidence_status == "confirmed" else 0.5,
            )
            for item in room.existing_items
        ],
        free_zones=[
            k.FreeZone(
                x0=min(p.x_m for p in zone.area.points),
                z0=min(p.y_m for p in zone.area.points),
                x1=max(p.x_m for p in zone.area.points),
                z1=max(p.y_m for p in zone.area.points),
                label=zone.label,
            )
            for zone in room.free_zones
        ],
        floor_color_hex=room.floor.color.hex,
        status="validated",
        notes=[],
    )
