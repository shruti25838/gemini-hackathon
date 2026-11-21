"""Owner: B (provisional copy in A's full build; B's file replaces this on merge).
A and B propose, C commits."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


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
