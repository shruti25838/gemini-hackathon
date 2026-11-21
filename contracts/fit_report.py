"""FitReport: the result of a geometry check. Owner C.

The same fit/ code produces this on the server and inside the Argon and Carbon
sandboxes, so the report must be self-describing: solver version, assumptions,
and every check that ran.

Rules that are not negotiable:
  - unknown whenever dimensions, hinge or swing direction are missing.
  - a result built on estimated room scale is tentative, never a plain "fits".
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field, model_validator

from contracts.common import (
    EthyneonModel,
    FitResultValue,
    Metres,
    Pose,
    Rect2D,
    Revision,
    utc_now,
)

CheckName = Literal[
    "room_bounds",
    "free_zone_containment",
    "object_overlap",
    "locked_object_overlap",
    "door_swing",
    "walking_route",
]

CheckResult = Literal["pass", "tight", "fail", "unknown"]


class FitCheck(EthyneonModel):
    """One geometry check and what it found."""

    name: CheckName
    result: CheckResult
    detail: str
    measured_clearance_m: Optional[Metres] = Field(
        default=None, description="Only set when the check actually measured a distance."
    )
    blocking_ids: list[str] = Field(
        default_factory=list,
        description="Item, opening or zone IDs involved in a tight or failing check.",
    )


class RejectedPlacement(EthyneonModel):
    """The placement that did not work and why, kept for the replay and the
    bounded repair."""

    object_id: str
    pose: Pose
    footprint: Rect2D
    cause: str
    failing_check: CheckName


class FitReport(EthyneonModel):
    report_id: str
    room_revision: Revision
    scene_revision: Revision
    solver_version: str = Field(description="fit/ package version that produced this.")
    assumptions: list[str] = Field(
        default_factory=list,
        description="Stated plainly, for example 'clearance margin 0.05 m'.",
    )
    checks_run: list[FitCheck] = Field(default_factory=list)
    minimum_clearance_m: Optional[Metres] = Field(
        default=None, description="Only when something was actually measured."
    )
    rejected_placement: Optional[RejectedPlacement] = None
    evidence: list[str] = Field(
        default_factory=list, description="References to frames, clips or offers used."
    )
    result: FitResultValue
    tentative: bool = Field(
        default=False,
        description=(
            "True when the result rests on estimated room scale or on product "
            "dimensions inferred from listing text."
        ),
    )
    unknown_reasons: list[str] = Field(
        default_factory=list,
        description="Required when result is unknown: what was missing.",
    )
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _unknown_must_say_why(self) -> "FitReport":
        if self.result == "unknown" and not self.unknown_reasons:
            raise ValueError("an unknown fit result must list unknown_reasons")
        return self

    @model_validator(mode="after")
    def _failure_needs_a_rejected_placement(self) -> "FitReport":
        if self.result == "does_not_fit" and self.rejected_placement is None:
            raise ValueError("does_not_fit must carry the rejected_placement and its cause")
        return self
