"""WorkflowPlan: a typed plan the backend verifies before anything runs.

Owner A. B shows it in the activity panel, C stores it.

The planner may only name operations from REGISTERED_OPERATIONS. The backend
checks operation names, input types, dependencies and base revisions before a
single step executes, so a generated plan cannot invent an action.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field, model_validator

from contracts.common import EthyneonModel, Money, Revision, utc_now

# The registered operation set. A plan naming anything outside this is rejected.
REGISTERED_OPERATIONS: frozenset[str] = frozenset(
    {
        "inspect_room_motion",
        "discover_shopify_variants",
        "confirm_measurement_if_needed",
        "search_layouts",
        "test_layout_motion",
        "prepare_scene_preview",
        "refresh_offer",
        "check_fit",
        "build_cart_review",
        "run_checkout_audit",
    }
)

PlanState = Literal["draft", "validated", "running", "paused", "completed", "failed"]
StepState = Literal["pending", "running", "waiting_input", "done", "failed", "skipped"]


class PlanConstraints(EthyneonModel):
    """The hard numbers the plan must respect."""

    merchandise_budget: Money
    locked_objects: list[str] = Field(default_factory=list)
    keep_door_clear: bool = False
    min_walkway_width_m: Optional[float] = Field(default=None, ge=0.0)


class WorkflowStep(EthyneonModel):
    id: str
    op: str = Field(description="Must be in REGISTERED_OPERATIONS.")
    after: list[str] = Field(
        default_factory=list, description="IDs of steps that must finish first."
    )
    inputs: dict = Field(default_factory=dict)
    state: StepState = "pending"
    artifact_refs: list[str] = Field(default_factory=list)


class WorkflowPlan(EthyneonModel):
    plan_id: str
    session_id: str
    goal: str
    constraints: PlanConstraints
    steps: list[WorkflowStep] = Field(min_length=1)
    room_revision: Revision = Field(description="Base revision the plan was built against.")
    scene_revision: Revision = Field(description="Base revision the plan was built against.")
    state: PlanState = "draft"
    artifact_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _steps_are_a_valid_dag(self) -> "WorkflowPlan":
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate step id in plan")
        known = set(ids)
        for step in self.steps:
            for dep in step.after:
                if dep not in known:
                    raise ValueError(f"step {step.id} depends on unknown step {dep}")
                if dep == step.id:
                    raise ValueError(f"step {step.id} depends on itself")
        # Topological sort, to reject a dependency cycle.
        remaining = {s.id: set(s.after) for s in self.steps}
        while remaining:
            ready = [sid for sid, deps in remaining.items() if not deps]
            if not ready:
                raise ValueError("plan steps contain a dependency cycle")
            for sid in ready:
                del remaining[sid]
            for deps in remaining.values():
                deps.difference_update(ready)
        return self
