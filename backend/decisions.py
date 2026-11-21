"""The Gemini Decisions API, called over plain HTTP.

Why not the SDK. The hackathon builder guide shows
`genai.Client(http_options={"api_version": "v1beta"}).decisions.create(...)`
and says to install google-genai>=3.2.0. That release does not exist: the
newest on PyPI is 2.33.0, which is installed, and it has no `.decisions`
attribute. So the call is made directly against v1beta instead, using the
exact request and response shape the guide documents.

The three question types, verbatim from the guide:

  noul    yes or no. Returns a probability between 0.01 and 0.99, plus a
          confidence score.
  choice  2 to 255 options. Returns the winning key, per-option
          probabilities summing to 1, and a confidence score.
  score   an ordered rubric of 2 to 255 levels. Returns a
          probability-weighted continuous score, level probabilities, a
          legend and a confidence score.

Confidence is always read in code. A low-confidence answer never passes a
gate; it pauses checkout instead.

When no key is configured, or MOCK_AI is on, a local stand-in answers. It is
labelled `local_stand_in` on every answer it produces and it can never return
a passing verdict it did not derive from the deterministic facts it was given.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from ai.model_ids import API_VERSION_BETA, GENERATIVE_LANGUAGE_BASE, MODEL_IDS
from contracts.interfaces import AuditInputs, GateAnswer, GateAnswers

QuestionType = Literal["noul", "choice", "score"]

# The gate questions PRD v3.0 defines for the checkout audit.
CHECKOUT_QUESTIONS: dict[str, dict[str, Any]] = {
    "purchase_scope_matches": {
        "type": "noul",
        "instructions": (
            "Does the cart contain exactly what the shopper selected and approved, "
            "with the chosen finishes, and exclude items they marked as owned and kept?"
        ),
    },
    "required_constraints_resolved": {
        "type": "noul",
        "instructions": (
            "Did the accepted workflow resolve the shopper's hard constraints for "
            "these products, or is fit, compatibility or another required condition "
            "still unresolved?"
        ),
    },
    "next_action": {
        "type": "choice",
        "instructions": "Which route should this checkout take?",
        "criteria": {
            "ready": "Everything reconciles and the checkout can be opened.",
            "refresh_cart": "The cart or its amounts are out of date and must be re-read.",
            "repair_plan": "The layout or selection needs repair before purchase.",
            "ask_shopper": "A detail needs the shopper to confirm it.",
            "hold_checkout": "Something is wrong; do not hand off.",
        },
    },
}

# Thresholds. Tuned on observed cases; a gate below its threshold does not pass.
DEFAULT_THRESHOLD = 0.80
NEXT_ACTION_THRESHOLD = 0.75


class DecisionsUnavailable(Exception):
    """The Decisions API could not be reached or answered. Checkout pauses."""


class DecisionsClient:
    """Minimal v1beta client. Read-only with respect to app state."""

    def __init__(
        self,
        api_key: Optional[str],
        *,
        model: str = MODEL_IDS["decisions"],
        base_url: str = GENERATIVE_LANGUAGE_BASE,
        api_version: str = API_VERSION_BETA,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version
        self.timeout_seconds = timeout_seconds

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/{self.api_version}/decisions"

    def create(
        self, inputs: list[dict[str, Any]], questions: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        if not self._api_key:
            raise DecisionsUnavailable("no API key configured")

        import httpx

        payload = {"model": self.model, "input": inputs, "questions": questions}
        try:
            response = httpx.post(
                self.endpoint,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self._api_key,
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            raise DecisionsUnavailable(f"request failed: {exc!r}") from exc

        if response.status_code != 200:
            raise DecisionsUnavailable(
                f"HTTP {response.status_code}: {response.text[:200]}"
            )
        return response.json()


def _answers_from_response(body: dict[str, Any]) -> GateAnswers:
    """Read the documented response shape, and nothing beyond it."""
    answers: list[GateAnswer] = []
    raw_answers = body.get("answers") or {}

    for key, spec in CHECKOUT_QUESTIONS.items():
        raw = raw_answers.get(key)
        if raw is None:
            raise DecisionsUnavailable(f"the response is missing the {key} answer")

        confidence = float(raw.get("confidence", 0.0))
        question_type = spec["type"]
        threshold = NEXT_ACTION_THRESHOLD if key == "next_action" else DEFAULT_THRESHOLD

        if question_type == "noul":
            probability = float(raw.get("noul", 0.0))
            answer = "yes" if probability >= 0.5 else "no"
            passed = answer == "yes" and confidence >= threshold
        else:
            answer = str(raw.get("choice", ""))
            passed = answer == "ready" and confidence >= threshold

        answers.append(
            GateAnswer(
                gate="checkout_audit",
                question=key,
                answer=answer,
                confidence=min(max(confidence, 0.0), 1.0),
                threshold=threshold,
                passed=passed,
                source="live",
            )
        )

    return GateAnswers(
        gate_set="checkout_audit",
        answers=answers,
        all_passed=all(a.passed for a in answers),
    )


def run_checkout_gates(
    evidence_packet: AuditInputs,
    *,
    client: Optional[DecisionsClient] = None,
    use_stand_in: bool = True,
) -> GateAnswers:
    """Decision 1 for the checkout audit.

    The evidence packet deliberately carries no contact details, credentials,
    checkout link or payment data. See build_audit_inputs.
    """
    if client is not None and client.available:
        body = client.create(
            inputs=[{"type": "text", "text": _packet_as_text(evidence_packet)}],
            questions=CHECKOUT_QUESTIONS,
        )
        return _answers_from_response(body)

    if not use_stand_in:
        raise DecisionsUnavailable("no Decisions client and the stand-in is disabled")

    return _stand_in(evidence_packet)


def _packet_as_text(packet: AuditInputs) -> str:
    """The evidence packet, rendered for the model. Facts only."""
    approved = ", ".join(
        f"{line.variant_id} x{line.quantity} @ {line.unit_price.amount_minor} "
        f"{line.unit_price.currency}"
        for line in packet.approved_lines
    )
    live = ", ".join(
        f"{line.variant_id} x{line.quantity} @ {line.unit_price.amount_minor} "
        f"{line.unit_price.currency}"
        for line in packet.live_cart_lines
    )
    budget = (
        f"{packet.budget.amount_minor} {packet.budget.currency}"
        if packet.budget
        else "none stated"
    )
    return (
        f"Approved lines: {approved or 'none'}\n"
        f"Live cart lines: {live or 'none'}\n"
        f"Merchandise subtotal: {packet.merchandise_subtotal.amount_minor} "
        f"{packet.merchandise_subtotal.currency}\n"
        f"Budget: {budget}\n"
        f"Hard constraints: {', '.join(packet.hard_constraints) or 'none'}\n"
        f"Fit status: {packet.fit_status}\n"
        f"Scene revision: {packet.scene_revision}, brief revision: {packet.brief_revision}"
    )


def _stand_in(packet: AuditInputs) -> GateAnswers:
    """A labelled local stand-in, used while MOCK_AI is on or no key exists.

    It derives its answers from the deterministic facts in the packet rather
    than inventing a verdict. Every answer is marked local_stand_in so it can
    never be mistaken for a real Decision 1 result, and confidence is capped
    below 1.0 because a rule is not a calibrated probability.
    """
    approved = {(line.variant_id, line.quantity) for line in packet.approved_lines}
    live = {(line.variant_id, line.quantity) for line in packet.live_cart_lines}
    scope_matches = bool(approved) and approved == live

    within_budget = True
    if packet.budget is not None:
        within_budget = (
            packet.merchandise_subtotal.currency == packet.budget.currency
            and packet.merchandise_subtotal.amount_minor <= packet.budget.amount_minor
        )

    constraints_resolved = packet.fit_status in ("fits", "tight") and within_budget

    if scope_matches and constraints_resolved:
        next_action, confidence = "ready", 0.85
    elif not scope_matches:
        next_action, confidence = "refresh_cart", 0.85
    elif packet.fit_status in ("does_not_fit", "unknown"):
        next_action, confidence = "repair_plan", 0.85
    else:
        next_action, confidence = "hold_checkout", 0.85

    answers = [
        GateAnswer(
            gate="checkout_audit",
            question="purchase_scope_matches",
            answer="yes" if scope_matches else "no",
            confidence=0.85,
            threshold=DEFAULT_THRESHOLD,
            passed=scope_matches,
            source="local_stand_in",
        ),
        GateAnswer(
            gate="checkout_audit",
            question="required_constraints_resolved",
            answer="yes" if constraints_resolved else "no",
            confidence=0.85,
            threshold=DEFAULT_THRESHOLD,
            passed=constraints_resolved,
            source="local_stand_in",
        ),
        GateAnswer(
            gate="checkout_audit",
            question="next_action",
            answer=next_action,
            confidence=confidence,
            threshold=NEXT_ACTION_THRESHOLD,
            passed=next_action == "ready",
            source="local_stand_in",
        ),
    ]

    return GateAnswers(
        gate_set="checkout_audit",
        answers=answers,
        all_passed=all(a.passed for a in answers),
    )
