"""RecoveryRun: a bounded, typed recovery attempt. Owner A, used by B and C.

Recovery picks only from ALLOWED_RECOVERY_ACTIONS. If the Managed Agent is
unavailable the application falls back to the recorded action for that error
class, and says so.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field, model_validator

from contracts.common import EthyneonModel, Revision, utc_now

ALLOWED_RECOVERY_ACTIONS: frozenset[str] = frozenset(
    {
        "request_rescan",
        "request_measurement",
        "switch_input_mode",
        "retry_idempotent_read",
        "resume_saved_job",
        "refresh_shopify",
        "replan_affected_nodes",
        "request_new_review",
        "reconcile_cart_by_read",
        "pause",
    }
)

RecoveryActionName = Literal[
    "request_rescan",
    "request_measurement",
    "switch_input_mode",
    "retry_idempotent_read",
    "resume_saved_job",
    "refresh_shopify",
    "replan_affected_nodes",
    "request_new_review",
    "reconcile_cart_by_read",
    "pause",
]

RecoveryOutcome = Literal["pending", "resolved", "failed", "awaiting_input", "exhausted"]

ErrorClass = Literal[
    "unreadable_photo",
    "invalid_room_geometry",
    "agora_interruption",
    "model_timeout",
    "managed_agent_outage",
    "infeasible_layout",
    "stale_shopify_variant",
    "duplicate_or_stale_action",
    "cart_mismatch",
    "low_confidence_audit",
    "unclear_cart_write",
    "payment_status_unknown",
    "unrecognized",
]


class PendingInput(EthyneonModel):
    """What the shopper is being asked for while the run waits."""

    interaction_id: str
    prompt: str
    expects: Literal["measurement", "tap", "choice", "confirmation"]
    frame_ref: Optional[str] = None
    options: list[str] = Field(default_factory=list)


class RecoveryRun(EthyneonModel):
    run_id: str
    session_id: str
    failed_step_id: str
    error_class: ErrorClass
    triggering_revision: Revision
    interaction_id: Optional[str] = Field(
        default=None, description="Managed Agent interaction, when one is active."
    )
    allowed_actions: list[RecoveryActionName] = Field(min_length=1)
    chosen_action: Optional[RecoveryActionName] = None
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    last_verified_state: dict = Field(
        default_factory=dict, description="Enough context to restore after a failure."
    )
    outcome: RecoveryOutcome = "pending"
    pending_input: Optional[PendingInput] = None
    agent_available: bool = Field(
        default=True, description="False means the recorded fallback was used."
    )
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _chosen_action_must_be_allowed(self) -> "RecoveryRun":
        if self.chosen_action is not None and self.chosen_action not in self.allowed_actions:
            raise ValueError(f"chosen_action {self.chosen_action} is not in allowed_actions")
        return self

    @model_validator(mode="after")
    def _awaiting_input_needs_a_prompt(self) -> "RecoveryRun":
        if self.outcome == "awaiting_input" and self.pending_input is None:
            raise ValueError("outcome awaiting_input requires pending_input")
        return self
