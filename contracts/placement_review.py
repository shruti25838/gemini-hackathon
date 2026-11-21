"""PlacementReview: a Managed Agent's proposal about one move. Owner A.

Agents never write shared state. This is a proposal; C's commit_revision is the
only writer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field

from contracts.common import Confidence, EthyneonModel, FitResultValue, Pose, Revision, utc_now
from contracts.fit_report import FitReport


class StyleAssessment(EthyneonModel):
    """Argon's read on how the move looks against the shopper's stated taste."""

    summary: str
    matches_preferences: bool
    confidence: Optional[Confidence] = None
    notes: list[str] = Field(default_factory=list)


class AlternativeTested(EthyneonModel):
    """Another pose the agent tried, with the fit result it got."""

    pose: Pose
    fit_report_ref: Optional[str] = None
    fit_result: FitResultValue
    rationale: str


class PlacementReview(EthyneonModel):
    review_id: str
    session_id: str
    base_scene_revision: Revision
    selected_object_id: str
    requested_pose: Pose
    style_assessment: Optional[StyleAssessment] = None
    affected_paths: list[str] = Field(
        default_factory=list, description="Walking route IDs the move would touch."
    )
    affected_openings: list[str] = Field(
        default_factory=list, description="Opening IDs whose motion the move would touch."
    )
    fit_evidence: Optional[FitReport] = Field(
        default=None, description="The report the server reruns and verifies."
    )
    alternatives_tested: list[AlternativeTested] = Field(default_factory=list)
    accepted_pose: Optional[Pose] = Field(
        default=None, description="Set only once the shopper accepts one."
    )
    created_at: datetime = Field(default_factory=utc_now)
