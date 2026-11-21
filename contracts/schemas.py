"""Shared Ethyneon contracts. Post any change to the team before editing.

Coordinates: one room-local frame in metres. Floor spans x in [0, width_m],
z in [0, depth_m], y is up. Walls:
  back  : z = 0,       runs along +x from x = 0
  right : x = width_m, runs along +z from z = 0
  front : z = depth_m, runs along +x from x = 0
  left  : x = 0,       runs along +z from z = 0
An opening's offset_m is measured from the start of its wall.
Money is always in minor units (cents) with an explicit currency.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

WallId = Literal["back", "right", "front", "left"]
Source = Literal["estimated", "confirmed", "sampled_from_photo", "catalog_fact", "inferred", "default"]
FitStatus = Literal["fits", "tight", "no_fit", "unknown"]


# ---------- Room ----------
class Dimension(BaseModel):
    value_m: float
    source: Source = "estimated"
    confidence: float = Field(0.5, ge=0, le=1)


class Wall(BaseModel):
    id: WallId
    color_hex: str = "#d9d4cc"
    color_source: Source = "default"


class DoorMotion(BaseModel):
    hinge_side: Literal["left", "right"]  # as seen from inside the room, facing the wall
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


class RoomSpec(BaseModel):
    revision: int = 1
    width_m: Dimension
    depth_m: Dimension
    height_m: Dimension
    walls: list[Wall]
    openings: list[Opening] = []
    existing_items: list[ExistingItem] = []
    floor_color_hex: str = "#8a7560"
    notes: list[str] = []  # uncertainties the model flagged


# ---------- Brief ----------
class DesignBrief(BaseModel):
    goal: str = "gaming_corner"
    budget_minor: int = 120000
    currency: str = "USD"
    categories: list[str] = ["desk", "chair", "monitor", "lighting"]
    style: list[str] = []
    locked_item_ids: list[str] = []
    keep_door_clear: bool = True
    preferred_wall: Optional[WallId] = None


# ---------- Catalog ----------
class Dims(BaseModel):
    w: float
    d: float
    h: float


class CatalogOffer(BaseModel):
    product_id: str
    variant_id: str
    title: str
    variant_title: str = ""
    category: str
    price_minor: int
    currency: str
    available: bool
    image_url: Optional[str] = None
    url: Optional[str] = None
    dims_m: Optional[Dims] = None
    colors: list[str] = []
    source: Literal["live", "fixture"] = "live"
    fetched_at: Optional[str] = None


# ---------- Scene ----------
class Placement(BaseModel):
    key: str  # unique in scene; variant_id for products, item id for existing items
    variant_id: Optional[str] = None
    category: str
    x: float  # centre of footprint
    z: float
    w: float
    d: float
    h: float = 0.75
    rot: Literal[0, 90, 180, 270] = 0
    locked: bool = False


class SceneRevision(BaseModel):
    rev: int
    parent_rev: Optional[int] = None
    room_revision: int
    placements: list[Placement] = []
    wall_colors: dict[str, str] = {}
    lighting: Literal["daylight", "evening", "rgb"] = "daylight"
    accent_hex: Optional[str] = None


class Collision(BaseModel):
    a: str
    b: str  # other placement key, "wall:<id>" or "door:<id>"
    kind: Literal["overlap", "out_of_bounds", "door_swing", "clearance"]
    detail: str = ""


class FitReport(BaseModel):
    scene_rev: int
    room_revision: int
    status: FitStatus
    collisions: list[Collision] = []
    min_gap_m: Optional[float] = None
    unknowns: list[str] = []
    solver_version: str = ""
    checked_by: list[str] = []  # e.g. ["server", "argon-sandbox", "carbon-sandbox"]


# ---------- Workflow + activity ----------
class PlanStep(BaseModel):
    id: str
    op: str
    after: list[str] = []
    args: dict = {}


class WorkflowPlan(BaseModel):
    plan_id: str = ""
    goal: str
    steps: list[PlanStep]
    base_room_revision: int = 0
    base_scene_rev: int = 0


class ActivityEvent(BaseModel):
    seq: int
    ts: float
    step: str
    status: Literal["started", "ok", "warn", "error", "needs_input", "info"]
    detail: str = ""
    data: dict = {}
