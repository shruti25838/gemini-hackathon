"""Owner: A. Made by A (intake), used by C (budget, cart)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from ._common import WallId


class DesignBrief(BaseModel):
    goal: str = "gaming_corner"  # room use
    budget_minor: int = 120000  # merchandise budget, minor units
    currency: str = "USD"
    categories: list[str] = ["desk", "chair", "monitor", "lighting"]  # required items
    style: list[str] = []  # preferences
    locked_item_ids: list[str] = []  # locked objects (shopper keeps them)
    keep_door_clear: bool = True
    preferred_wall: Optional[WallId] = None
