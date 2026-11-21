"""RoomSpec: the validated procedural room. Owner A, edited by B, stored by C.

One room-local coordinate system in metres. The room occupies the rectangle
from (0, 0) to (width_m, depth_m). Each opening's offset is measured from the
start of its wall.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field

from contracts.common import (
    ColorSpec,
    EthyneonModel,
    EvidenceStatus,
    Measurement,
    Metres,
    Polygon2D,
    Rect2D,
    Revision,
    SourceLabel,
    WallId,
    utc_now,
)

OpeningType = Literal["door", "window", "archway", "pass_through"]
SwingDirection = Literal["inward", "outward", "sliding", "none", "unknown"]
HingeSide = Literal["left", "right", "unknown"]

# Supported room-size bounds for the hackathon build. validate_room_spec
# rejects anything outside these.
MIN_ROOM_DIMENSION_M = 1.0
MAX_ROOM_DIMENSION_M = 20.0
MIN_ROOM_HEIGHT_M = 1.8
MAX_ROOM_HEIGHT_M = 6.0


class RoomDimensions(EthyneonModel):
    """Width, depth and height in metres, each with its own source."""

    width_m: Measurement
    depth_m: Measurement
    height_m: Measurement


class ScaleReference(EthyneonModel):
    """The one confirmed real length that anchors the model.

    A confirmed width only confirms the dimension it measures; an estimated
    depth stays estimated after that width is confirmed.
    """

    anchor_id: str
    description: str
    length_m: Metres
    confirmed: bool = False
    evidence_status: EvidenceStatus = "estimated"
    measures: Literal["width", "depth", "height", "other"] = "other"


class Wall(EthyneonModel):
    """One wall with a stable ID. Walls are never renamed once set."""

    wall_id: WallId
    length_m: Metres
    color: ColorSpec
    label: Optional[str] = None


class Floor(EthyneonModel):
    floor_id: str = "floor"
    color: ColorSpec
    material: Optional[str] = None


class HingeSpec(EthyneonModel):
    """Door hinge and motion. Missing hinge or swing direction forces a fit
    result of unknown; the fit engine never guesses them."""

    hinge_side: HingeSide = "unknown"
    swing_direction: SwingDirection = "unknown"
    swing_angle_max_deg: float = Field(default=90.0, ge=0.0, le=180.0)
    evidence_status: EvidenceStatus = "unknown"
    motion_evidence_ref: Optional[str] = Field(
        default=None, description="Reference to the clip or frame showing the motion."
    )


class Opening(EthyneonModel):
    """A door, window or archway on one wall.

    offset_m is measured from the start of its wall. An opening must lie
    entirely inside its wall: offset_m + width_m <= that wall's length_m.
    validate_room_spec enforces that against the wall, which this model
    cannot see on its own.
    """

    opening_id: str
    wall_id: WallId
    type: OpeningType
    offset_m: Metres
    width_m: Metres
    height_m: Metres
    sill_m: Optional[Metres] = Field(
        default=None, description="Height of the sill above the floor, for windows."
    )
    hinge: Optional[HingeSpec] = Field(
        default=None, description="Doors only. Required before a door swing check can run."
    )
    evidence_status: EvidenceStatus = "estimated"


class ExistingItem(EthyneonModel):
    """Furniture already in the room. Locked items are placement constraints."""

    item_id: str
    label: str
    footprint: Optional[Rect2D] = Field(
        default=None, description="None when the bounds are not known."
    )
    height_m: Optional[Metres] = None
    locked: bool = False
    keep: bool = True
    evidence_status: EvidenceStatus = "estimated"


class FreeZone(EthyneonModel):
    """An area that must stay clear, or a region placements are confined to.

    Code clips free zones to the valid room; a zone outside the room is invalid.
    """

    zone_id: str
    label: str
    area: Polygon2D
    purpose: Literal["keep_clear", "placement_area", "walking_route"] = "keep_clear"


class RoomSpec(EthyneonModel):
    """The validated room. Only C's commit_revision writes one."""

    room_revision: Revision
    session_id: str
    dimensions_m: RoomDimensions
    scale_reference: Optional[ScaleReference] = None
    walls: list[Wall] = Field(min_length=4, max_length=4)
    floor: Floor
    openings: list[Opening] = Field(default_factory=list)
    existing_items: list[ExistingItem] = Field(default_factory=list)
    free_zones: list[FreeZone] = Field(default_factory=list)
    evidence_status: EvidenceStatus = "estimated"
    source: SourceLabel = "generated"
    snapshot_ref: Optional[str] = Field(
        default=None, description="RoomSnapshot this spec was derived from."
    )
    created_at: datetime = Field(default_factory=utc_now)
