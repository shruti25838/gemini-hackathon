"""run_recovery (team contract, Person A).

A failed workflow step becomes a typed RecoveryRun. Gemini 4 Argon picks ONE action from the
run's allowed_actions; if Argon is unavailable or answers outside the list, a fixed rule
table decides. Code validates the choice; the caller executes only safe actions automatically
(retry a read once, ask the shopper, pause). Cart writes and checkout are never retried here.
"""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field

from ._vendor.kschemas import RecoveryAction, RecoveryRun

from . import gemini
from .models import ARGON

# error class -> (allowed actions, rule-table fallback)
POLICY: dict[str, tuple[list[RecoveryAction], RecoveryAction]] = {
    "ProviderTimeout": (["retry_read_once", "switch_input_mode", "pause"], "retry_read_once"),
    "GeminiError": (["retry_read_once", "switch_input_mode", "pause"], "retry_read_once"),
    "ShopError": (["refresh_shopify", "retry_read_once", "pause"], "refresh_shopify"),
    "HTTPStatusError": (["retry_read_once", "refresh_shopify", "pause"], "retry_read_once"),
    "RoomSpecError": (["request_rescan", "request_measurement", "pause"], "request_measurement"),
    "StaleRevision": (["replan_affected", "pause"], "replan_affected"),
    "CheckoutError": (["reconcile_cart", "request_new_review", "pause"], "reconcile_cart"),
}
DEFAULT_POLICY: tuple[list[RecoveryAction], RecoveryAction] = (["pause"], "pause")
MAX_ATTEMPTS = 1


class _Choice(BaseModel):
    action: str
    rationale: str = Field(description="one sentence, shown to the shopper")


SYSTEM = """You are Ethyneon's recovery planner. A workflow step failed. Choose exactly one action from
allowed_actions that is safest and most likely to let the shopper continue. Never choose an action that
writes to a cart or charges money. If unsure, choose "pause". Return JSON only."""


def new_run(failed_step: str, error: Exception, triggering_revision: int, last_verified: dict,
            attempt: int = 0, interaction_id: Optional[str] = None) -> RecoveryRun:
    allowed, _ = POLICY.get(type(error).__name__, DEFAULT_POLICY)
    if attempt >= MAX_ATTEMPTS:
        allowed = [a for a in allowed if a not in ("retry_read_once", "replan_affected")] or ["pause"]
    return RecoveryRun(run_id=uuid.uuid4().hex[:8], failed_step=failed_step, error_class=type(error).__name__,
                       triggering_revision=triggering_revision, interaction_id=interaction_id,
                       allowed_actions=allowed, attempt_count=attempt, last_verified_state=last_verified,
                       rationale=str(error)[:300])


def run_recovery(run: RecoveryRun) -> RecoveryRun:
    """Pick one registered action. Argon first, rule table as the fallback."""
    out = run.model_copy(deep=True)
    rule = POLICY.get(run.error_class, DEFAULT_POLICY)[1]
    if rule not in run.allowed_actions:
        rule = "pause" if "pause" in run.allowed_actions else run.allowed_actions[0]
    try:
        choice, meta = gemini.generate_structured(
            ARGON, f"Failure: {run.model_dump_json()}", _Choice, system=SYSTEM, thinking="low", retries=0)
        if choice.action in run.allowed_actions:
            out.chosen_action, out.rationale = choice.action, choice.rationale
            out.chosen_by = "mock" if meta.get("source") == "mock" else "argon"
            return out
        out.rationale = f"Argon chose '{choice.action}', which is not allowed; used the rule table."
    except Exception as e:  # Argon unavailable -> deterministic rule
        out.rationale = f"Argon unavailable ({type(e).__name__}); used the rule table."
    out.chosen_action, out.chosen_by = rule, "fallback_rule"
    return out
