"""CheckoutAudit: the gate in front of the hosted checkout. Owner C, built with A.

Two independent things must agree before a checkout URL is released:

  1. The deterministic reconciliation the server runs in plain code: store,
     exact variant IDs, quantities, currency, stock, amounts, budget, fit
     status, approval and revisions.
  2. A confident ready answer from the Decision 1 gate.

The audit is bound to one approval fingerprint and one cart fingerprint and it
expires. A changed cart, a later edit, a refreshed amount or an expired audit
forces a new one.

The answer type names below are taken from the PRD and are NOT yet verified
against the live Decisions API. See decision_answer_type on DecisionAnswer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field, model_validator

from contracts.common import Confidence, EthyneonModel, EvidenceStatus, Revision, utc_now

# The next_action choices the PRD defines for the checkout gate.
NextAction = Literal["ready", "refresh_cart", "repair_plan", "ask_shopper", "hold_checkout"]

AuditOutcome = Literal["ready", "needs_review", "blocked", "unavailable", "expired"]

DeterministicCheckName = Literal[
    "store_matches",
    "variant_ids_match",
    "quantities_match",
    "currency_matches",
    "stock_available",
    "amounts_match",
    "within_budget",
    "fit_status_acceptable",
    "approval_valid",
    "revisions_current",
]


class DeterministicCheck(EthyneonModel):
    """One server-side check, run in plain code with no model involved."""

    name: DeterministicCheckName
    passed: bool
    detail: str
    expected: Optional[str] = None
    observed: Optional[str] = None


class DecisionAnswer(EthyneonModel):
    """One Decision 1 answer with the confidence the code actually reads."""

    question: str = Field(
        description="For example purchase_scope_matches or required_constraints_resolved."
    )
    answer_type: str = Field(
        description=(
            "The Decisions API type name. The PRD says 'noul' for yes/no and "
            "'choice' for a rubric; both must be verified against the live docs "
            "before Lane A calls the real API."
        )
    )
    answer: str
    confidence: Confidence
    threshold: Confidence = Field(
        description="The tuned threshold this answer was judged against."
    )
    passed_threshold: bool
    source: Literal["live", "mock", "local_stand_in"] = "mock"


class CheckoutAudit(EthyneonModel):
    audit_id: str
    session_id: str
    workflow_run_id: Optional[str] = None
    scene_revision: Revision
    brief_revision: Revision
    approval_fingerprint: str
    cart_fingerprint: str
    deterministic_results: list[DeterministicCheck] = Field(min_length=1)
    decision_answers: list[DecisionAnswer] = Field(default_factory=list)
    next_action: Optional[NextAction] = None
    outcome: AuditOutcome
    expires_at: datetime
    evidence_status: EvidenceStatus = "confirmed"
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def deterministic_passed(self) -> bool:
        return all(check.passed for check in self.deterministic_results)

    @model_validator(mode="after")
    def _ready_requires_both_halves(self) -> "CheckoutAudit":
        if self.outcome != "ready":
            return self
        if not all(check.passed for check in self.deterministic_results):
            raise ValueError(
                "outcome ready requires every deterministic check to pass"
            )
        if not self.decision_answers:
            raise ValueError("outcome ready requires a Decision 1 answer")
        if not all(answer.passed_threshold for answer in self.decision_answers):
            raise ValueError(
                "outcome ready requires every Decision 1 answer to meet its threshold"
            )
        if self.next_action != "ready":
            raise ValueError("outcome ready requires next_action ready")
        return self
