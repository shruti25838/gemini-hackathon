"""Owner: C (provisional copy in A's full build). Same code runs in the sandboxes (fit/solver.py)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from ._common import FitStatus


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
