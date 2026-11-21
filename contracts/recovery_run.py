"""Owner: A. Made by A, used by B (status) and C (holds the affected action)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

RecoveryAction = Literal[
    "request_rescan", "request_measurement", "switch_input_mode", "retry_read_once", "resume_saved_job",
    "refresh_shopify", "replan_affected", "request_new_review", "reconcile_cart", "pause",
]


class RecoveryRun(BaseModel):
    run_id: str
    failed_step: str
    error_class: str
    triggering_revision: int
    interaction_id: Optional[str] = None
    allowed_actions: list[RecoveryAction]
    attempt_count: int = 0
    last_verified_state: dict = {}  # {"room_revision": n, "scene_rev": m, "cart_id": ...}
    chosen_action: Optional[RecoveryAction] = None
    chosen_by: Optional[Literal["argon", "fallback_rule", "mock"]] = None
    rationale: str = ""
    outcome: Optional[Literal["resolved", "pending_input", "paused", "failed"]] = None
    pending_input: Optional[dict] = None
