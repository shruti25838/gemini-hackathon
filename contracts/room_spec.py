"""Owner: A (AI and agents). A drafts, B edits, C stores.

Coordinates: one room-local frame in metres. Floor spans x in [0, width_m], z in [0, depth_m], y up.
  wall_n : z = 0,       runs along +x from x = 0   (the far wall the main photo faces)
  wall_e : x = width_m, runs along +z from z = 0   (right-hand wall in the photo)
  wall_s : z = depth_m, runs along +x from x = 0   (behind the camera)
  wall_w : x = 0,       runs along +z from z = 0   (left-hand wall in the photo)
An opening's offset_m is measured from the start of its wall.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from ._common import Source, WallId


class Dimension(BaseModel):
    value_m: float
    source: Source = "estimated"
    confidence: float = Field(0.5, ge=0, le=1)


class Wall(BaseModel):
    id: WallId
    color_hex: str = "#d9d4cc"
    color_source: Source = "default"


class DoorMotion(BaseModel):
    hinge_side: Literal["left", "right"]  # as seen from inside the room, facing that wall
    swing: Literal["in", "out"]
    max_angle_deg: float = 90
    source: Source = "inferred"
    confidence: float = 0.5
    evidence_ts: Optional[str] = None  # "mm:ss" in the clip where the swing is clearest


class Opening(BaseModel):
    id: str
    wall: WallId
    type: Literal["door", "window", "opening"]
    offset_m: float
    width_m: float
    height_m: float = 2.0
    sill_m: float = 0.0
    motion: Optional[DoorMotion] = None
    confidence: float = 0.5


class ExistingItem(BaseModel):
    id: str
    label: str
    x: float
    z: float
    w: float
    d: float
    h: float = 0.75
    keep: bool = True
    confidence: float = 0.5


class FreeZone(BaseModel):
    x0: float
    z0: float
    x1: float
    z1: float
    label: str = ""


class RoomSpec(BaseModel):
    room_revision: int = 1
    width_m: Dimension
    depth_m: Dimension
    height_m: Dimension
    scale_reference: Optional[str] = None  # e.g. "width_m confirmed by shopper at 3.2"
    walls: list[Wall]
    openings: list[Opening] = []
    existing_items: list[ExistingItem] = []
    free_zones: list[FreeZone] = []
    floor_color_hex: str = "#8a7560"
    status: Literal["draft", "validated", "preset"] = "draft"
    notes: list[str] = []  # uncertainties and evidence notes the model flagged
