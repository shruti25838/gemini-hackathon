"""Owner: A. Made by A, used by B (activity) and C (store)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class PlanStep(BaseModel):
    id: str
    op: str  # must be a registered operation (ai.workflow.OP_DOCS)
    after: list[str] = []
    args: dict = {}
    state: Literal["pending", "running", "done", "failed", "needs_input"] = "pending"


class WorkflowPlan(BaseModel):
    plan_id: str = ""
    goal: str
    steps: list[PlanStep]
    base_room_revision: int = 0
    base_scene_rev: int = 0
    source: Literal["neon", "template", "fast_path", "mock"] = "neon"
    artifacts: dict = {}  # step id -> artifact reference
