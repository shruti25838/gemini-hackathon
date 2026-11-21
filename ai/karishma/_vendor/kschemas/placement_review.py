"""Owner: A. Made by A (Argon/Carbon review of a requested move), used by B and C.
A proposal only: C's server commits nothing until the shopper accepts and the fit re-check passes."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .fit_report import Collision, FitReport


class Pose(BaseModel):
    x: float
    z: float
    rot: int = 0


class PlacementReview(BaseModel):
    base_scene_rev: int
    selected_key: str
    requested_pose: Pose
    requested_fit: FitReport
    conflicts: list[Collision] = []  # affected paths and openings
    style_assessment: Optional[str] = None
    alternatives_tested: int = 0
    alternative_placements: Optional[list[dict]] = None
    alternative_fit: Optional[FitReport] = None
    tester_findings: list[dict] = []  # Carbon findings with reproduced_by_server flags
    accepted_pose: Optional[Pose] = None
    checked_by: list[str] = []
