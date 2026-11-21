"""Room understanding with Gemini 4 Neon.

photo  -> RoomSpec draft (structured output) -> server validation -> RoomSpec
clip   -> DoorMotion (agentic video understanding)
user measurement -> scale anchoring of every still-estimated length
"""
from __future__ import annotations

import math
from typing import Literal, Optional

from pydantic import BaseModel, Field

from contracts.schemas import (Dimension, DoorMotion, ExistingItem, Opening, RoomSpec, Wall)

from . import gemini
from .models import NEON

ROOM_BOUNDS = {"width_m": (1.5, 12.0), "depth_m": (1.5, 12.0), "height_m": (2.0, 4.5)}


# ---------- model-facing draft schema (kept flat and simple) ----------
class DraftDim(BaseModel):
    value_m: float
    confidence: float = Field(ge=0, le=1)


class DraftOpening(BaseModel):
    wall: Literal["back", "right", "front", "left"]
    type: Literal["door", "window", "opening"]
    offset_m: float = Field(description="distance from the start of the wall to the near edge of the opening")
    width_m: float
    height_m: float
    sill_m: float = 0.0
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(description="where in the photo you see it, e.g. 'left third, behind chair'")


class DraftItem(BaseModel):
    label: str
    x: float
    z: float
    w: float
    d: float
    h: float
    confidence: float = Field(ge=0, le=1)


class DraftWall(BaseModel):
    id: Literal["back", "right", "front", "left"]
    color_hex: str
    visible: bool


class RoomDraft(BaseModel):
    width_m: DraftDim
    depth_m: DraftDim
    height_m: DraftDim
    walls: list[DraftWall]
    openings: list[DraftOpening]
    existing_items: list[DraftItem]
    floor_color_hex: str
    uncertainties: list[str]


ROOM_SYSTEM = """You convert one room photo into an approximate rectangular room model.
Coordinate frame (metres): the camera looks roughly at the BACK wall. Floor x runs left->right
along the back wall from 0 to width_m; z runs from the back wall (z=0) toward the camera (z=depth_m).
Walls: back (z=0), right (x=width_m), front (z=depth_m, usually behind the camera), left (x=0).
Opening offset_m is measured from x=0 for back/front walls and from z=0 for left/right walls.
Existing item x,z are the centre of its floor footprint; w is along x, d along z.
Estimate sizes from familiar references (doors ~0.8-0.9 m wide and ~2.0 m tall, desks ~0.75 m high,
outlets ~0.3 m above floor). Do not invent things you cannot see: list them in uncertainties instead.
Give honest confidences; a wall you cannot see gets visible=false and confidence stays low."""


class RoomSpecError(ValueError):
    pass


def validate_room(spec: RoomSpec) -> RoomSpec:
    """Reject non-finite or out-of-range geometry; clip openings/items into the room."""
    for key, (lo, hi) in ROOM_BOUNDS.items():
        v = getattr(spec, key).value_m
        if not math.isfinite(v) or not lo <= v <= hi:
            raise RoomSpecError(f"{key}={v} outside supported range {lo}-{hi} m")
    W, D, H = spec.width_m.value_m, spec.depth_m.value_m, spec.height_m.value_m
    if {w.id for w in spec.walls} != {"back", "right", "front", "left"}:
        have = {w.id for w in spec.walls}
        spec.walls += [Wall(id=w) for w in ("back", "right", "front", "left") if w not in have]
    kept = []
    for o in spec.openings:
        length = W if o.wall in ("back", "front") else D
        vals = [o.offset_m, o.width_m, o.height_m, o.sill_m]
        if not all(math.isfinite(v) for v in vals) or o.width_m <= 0.2 or o.offset_m < -0.05 or o.offset_m + o.width_m > length + 0.05:
            spec.notes.append(f"Dropped {o.type} {o.id}: lies outside the {o.wall} wall")
            continue
        o.offset_m = max(0.0, min(o.offset_m, length - o.width_m))
        o.height_m = min(o.height_m, H)
        kept.append(o)
    spec.openings = kept
    items = []
    for it in spec.existing_items:
        if not all(math.isfinite(v) for v in (it.x, it.z, it.w, it.d)) or it.w <= 0 or it.d <= 0:
            spec.notes.append(f"Dropped item {it.label}: invalid size")
            continue
        it.x = min(max(it.x, it.w / 2), W - it.w / 2)
        it.z = min(max(it.z, it.d / 2), D - it.d / 2)
        items.append(it)
    spec.existing_items = items
    return spec


def draft_to_spec(d: RoomDraft, revision: int = 1) -> RoomSpec:
    spec = RoomSpec(
        revision=revision,
        width_m=Dimension(value_m=d.width_m.value_m, confidence=d.width_m.confidence),
        depth_m=Dimension(value_m=d.depth_m.value_m, confidence=d.depth_m.confidence),
        height_m=Dimension(value_m=d.height_m.value_m, confidence=d.height_m.confidence),
        walls=[Wall(id=w.id, color_hex=w.color_hex, color_source="sampled_from_photo" if w.visible else "default")
               for w in d.walls],
        openings=[Opening(id=f"{o.type}{i + 1}", wall=o.wall, type=o.type, offset_m=o.offset_m, width_m=o.width_m,
                          height_m=o.height_m, sill_m=o.sill_m, confidence=o.confidence)
                  for i, o in enumerate(d.openings)],
        existing_items=[ExistingItem(id=f"item{i + 1}", label=it.label, x=it.x, z=it.z, w=it.w, d=it.d, h=it.h,
                                     confidence=it.confidence) for i, it in enumerate(d.existing_items)],
        floor_color_hex=d.floor_color_hex,
        notes=list(d.uncertainties),
    )
    return validate_room(spec)


def photo_to_roomspec(image: bytes, mime: str, thinking: str = "medium") -> tuple[RoomSpec, dict]:
    draft, meta = gemini.generate_structured(
        NEON, [gemini.image_part(image, mime), gemini.text_part("Build the room model for this photo.")],
        RoomDraft, system=ROOM_SYSTEM, thinking=thinking)
    return draft_to_spec(draft), meta


# ---------- door clip ----------
class DoorDraft(BaseModel):
    door_wall: Literal["back", "right", "front", "left", "unknown"]
    hinge_side: Literal["left", "right", "unknown"]
    swing: Literal["in", "out", "unknown"]
    max_angle_deg: float
    evidence_ts: str = Field(description="mm:ss where the hinge and swing are clearest")
    confidence: float = Field(ge=0, le=1)
    reasoning: str


DOOR_SYSTEM = """You analyse a short clip of a door opening, filmed from inside the room.
Report: which room wall it is on (same frame as the room model: back wall is the one the main
photo faced), the hinge side as seen from inside the room facing that wall, whether the door swings
into the room or out of it, the maximum opening angle you observe, and the timestamp where this is
clearest. Scrub the clip and zoom on the hinge. If something is not visible, answer 'unknown'."""


def clip_to_door_motion(video_part: dict, room_hint: str = "") -> tuple[DoorDraft, dict]:
    return gemini.generate_structured(
        NEON, [video_part, gemini.text_part(f"Room context: {room_hint}\nAnalyse the door.")],
        DoorDraft, system=DOOR_SYSTEM, thinking="high")


def apply_door(spec: RoomSpec, door: DoorDraft, opening_id: Optional[str] = None) -> tuple[RoomSpec, str]:
    """Attach motion to the matching door. Unknown hinge/swing is never filled in by guessing."""
    doors = [o for o in spec.openings if o.type == "door"]
    if opening_id:
        doors = [o for o in doors if o.id == opening_id]
    elif door.door_wall != "unknown":
        doors = [o for o in doors if o.wall == door.door_wall] or doors
    if not doors:
        return spec, "No matching door in the room model. Add or correct the door first."
    if door.hinge_side == "unknown" or door.swing == "unknown":
        return spec, "Hinge or swing direction not visible. Tap which side the hinge is on."
    target = doors[0]
    target.motion = DoorMotion(hinge_side=door.hinge_side, swing=door.swing,
                               max_angle_deg=max(15.0, min(door.max_angle_deg, 180.0)),
                               source="inferred", confidence=door.confidence, evidence_ts=door.evidence_ts)
    spec.revision += 1
    return spec, ""


# ---------- measurement anchoring ----------
def anchor_measurement(spec: RoomSpec, field: Literal["width_m", "depth_m", "height_m"], value_m: float) -> RoomSpec:
    """User confirmed one real length. A single photo has one unknown scale, so rescale every
    still-estimated length by the same factor; previously confirmed values are preserved."""
    old = getattr(spec, field).value_m
    if old <= 0 or not math.isfinite(value_m):
        raise RoomSpecError("invalid measurement")
    k = value_m / old
    s = spec.model_copy(deep=True)
    for name in ("width_m", "depth_m", "height_m"):
        dim: Dimension = getattr(s, name)
        if name == field:
            setattr(s, name, Dimension(value_m=value_m, source="confirmed", confidence=1.0))
        elif dim.source != "confirmed":
            dim.value_m = round(dim.value_m * k, 3)
    for o in s.openings:
        o.offset_m, o.width_m, o.height_m, o.sill_m = (round(v * k, 3) for v in (o.offset_m, o.width_m, o.height_m, o.sill_m))
    for it in s.existing_items:
        it.x, it.z, it.w, it.d, it.h = (round(v * k, 3) for v in (it.x, it.z, it.w, it.d, it.h))
    s.revision += 1
    s.notes.append(f"Scale anchored on {field}={value_m} m (factor {k:.2f})")
    return validate_room(s)
