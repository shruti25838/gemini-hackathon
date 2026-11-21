"""RealLaneA: Karishma's analyze_room, everything else delegated.

Scope for this phase is deliberately narrow. Only `analyze_room` runs her
code. The other six functions delegate, and every one of them says so in
`/api/health` so nobody on stage mistakes a stub for a real agent.

| function            | this phase                                      |
| ------------------- | ----------------------------------------------- |
| analyze_room        | real, Karishma's ai/karishma/room.py            |
| validate_room_spec  | delegated to our backend.validation             |
| run_checkout_gates  | delegated to our backend.decisions              |
| handle_command      | delegated to our stub (out of scope)            |
| plan_workflow       | delegated to our stub (out of scope)            |
| run_workflow        | delegated to our stub (out of scope)            |
| run_recovery        | delegated to our stub (out of scope)            |

Lane A only proposes. `analyze_room` returns a draft; nothing here writes,
and her `Session.commit_scene` and `Session.set_room` raise rather than
pretend (see ai/karishma/shim.py).
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from contracts.interfaces import (
    GateAnswer,
    GateAnswers,
    RoomAnalysis,
    ValidationResult,
)
from contracts.room_spec import RoomSpec

from ai.karishma_adapter import contracts_bridge as bridge
from ai.stubs import StubLaneA

# What each function actually is, surfaced in /api/health.
DELEGATION: dict[str, str] = {
    "analyze_room": "real: karishma",
    "validate_room_spec": "delegated: ours (backend.validation)",
    "run_checkout_gates": "delegated: ours (backend.decisions)",
    "handle_command": "delegated: stub (out of scope this phase)",
    "plan_workflow": "delegated: stub (out of scope this phase)",
    "run_workflow": "delegated: stub (out of scope this phase)",
    "run_recovery": "delegated: stub (out of scope this phase)",
}

# Gate outcomes, kept apart on purpose. A gate that could not run is not a
# photo that was rejected, and showing the first as the second sends the
# shopper off to retake a perfectly good picture.
GATE_PASSED = "passed"
GATE_REJECTED = "rejected"
GATE_ERROR = "error"

GATE_SET_OK = "room_photo"
GATE_SET_ERROR = "room_photo_error"


def gate_outcome(gate: Any) -> str:
    """Her `GateResult` into one of three states.

    She sets `degraded` when the Decisions call itself failed, and then
    `passed` is False for a reason that has nothing to do with the photo.
    That distinction is the whole point of this function.
    """
    if getattr(gate, "degraded", False):
        return GATE_ERROR
    return GATE_PASSED if getattr(gate, "passed", False) else GATE_REJECTED


def _answer(gate: Any, question: str, text: str, source: str) -> GateAnswer:
    return GateAnswer(
        gate=getattr(gate, "gate", "room_photo"),
        question=question,
        answer=str(text),
        confidence=0.0,
        threshold=0.80,
        passed=False,
        source=source,
    )


def _gate_answers_from_hers(gate: Any, *, mocked: bool) -> GateAnswers:
    """Her single `GateResult` into our list of `GateAnswer`.

    Her `answers` dict holds one entry per question, each with a `noul`,
    `score` or `choice` and a `confidence`. Those come across as they are.
    The re-shoot hints from `coverage_gaps` come across too, because they
    are the actionable part and would otherwise be dropped on the floor.
    """
    source = "mock" if mocked else "live"
    outcome = gate_outcome(gate)
    raw = gate.answers if isinstance(getattr(gate, "answers", None), dict) else {}
    answers: list[GateAnswer] = []

    if outcome == GATE_ERROR:
        # Not a verdict about the photo. Say that, and nothing else.
        answers.append(
            _answer(gate, "gate_error", raw.get("error", "unknown error"), source)
        )
        if getattr(gate, "message", ""):
            answers.append(_answer(gate, "gate_error_message", gate.message, source))
        return GateAnswers(gate_set=GATE_SET_ERROR, answers=answers, all_passed=False)

    for question, value in raw.items():
        confidence = 0.0
        answer_text = str(value)
        if isinstance(value, dict):
            confidence = float(value.get("confidence", 0.0) or 0.0)
            for key in ("noul", "score", "choice"):
                if key in value:
                    answer_text = str(value[key])
                    break
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            confidence = float(value)
            answer_text = "yes" if confidence >= 0.5 else "no"

        answers.append(
            GateAnswer(
                gate=gate.gate,
                question=str(question),
                answer=answer_text,
                confidence=min(max(confidence, 0.0), 1.0),
                threshold=0.80,
                # Per answer, `passed` means "confident enough to rely on
                # this one". It is deliberately not tied to the overall
                # verdict: a rejected photo can still have a high-confidence
                # door_visible, and showing that as False misreads the data.
                # The gate's verdict lives in `all_passed`.
                passed=confidence >= 0.80,
                source=source,
            )
        )

    for gap in getattr(gate, "coverage_gaps", None) or []:
        answers.append(
            _answer(
                gate,
                f"rescan:{gap.get('affects', 'coverage')}",
                gap.get("rescan", ""),
                source,
            )
        )

    if outcome == GATE_REJECTED and getattr(gate, "message", ""):
        answers.append(_answer(gate, "rescan_hint", gate.message, source))

    if not answers:
        answers.append(
            GateAnswer(
                gate=gate.gate,
                question="passed",
                answer="yes" if outcome == GATE_PASSED else "no",
                confidence=1.0,
                threshold=0.80,
                passed=outcome == GATE_PASSED,
                source=source,
            )
        )

    return GateAnswers(
        gate_set=GATE_SET_OK,
        answers=answers,
        all_passed=outcome == GATE_PASSED,
    )


class RealLaneA:
    """The provider installed with `set_lane_a`."""

    is_stub = False
    name = "karishma+ours"
    delegation = DELEGATION

    def __init__(self) -> None:
        self._stub = StubLaneA()

    # -- real -------------------------------------------------------------

    def analyze_room(self, image: bytes, session_id: str) -> RoomAnalysis:
        """Karishma's photo gate and Neon RoomSpec draft, translated.

        A draft comes back only when the gate actually passed. If the gate
        was rejected, or could not run at all, the result carries the gate
        and the re-shoot hint and **no draft**.

        That last part matters. Her `analyze_room` continues past a degraded
        gate and builds a room anyway, which is reasonable in her app and
        wrong here: an ungated draft is indistinguishable from an analysed
        one once it reaches the scene, and nothing on the record would say
        the photo was never checked for quality or for a person in frame.
        """
        from ai.karishma import room as her_room
        from ai.karishma.gemini import mock_ai

        result = her_room.analyze_room(image, session_id)
        mocked = mock_ai()
        outcome = gate_outcome(result.gate)
        gates = _gate_answers_from_hers(result.gate, mocked=mocked)

        draft: Optional[RoomSpec] = None
        evidence: list[str] = []

        if outcome == GATE_PASSED and result.accepted and result.room_spec is not None:
            draft = bridge.room_spec_to_ours(result.room_spec, session_id=session_id)
        elif outcome == GATE_ERROR:
            evidence.append("gate_error: the photo was never checked")
        else:
            evidence.append("gate_rejected: no draft was built")

        if result.room_spec is not None and draft is None:
            evidence.append("ungated_draft_discarded")

        return RoomAnalysis(
            analysis_id=f"analysis_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            gates=gates,
            room_spec_draft=draft,
            confidence=None,
            evidence_refs=evidence,
            source="mock" if mocked else "live",
        )

    # -- delegated to our own implementations ------------------------------

    def validate_room_spec(self, draft: RoomSpec) -> ValidationResult:
        """Ours, not hers. C cannot commit a room without a real validator,
        and ours checks wall bounds and openings that hers does not."""
        from backend.validation import validate_room_spec as ours

        return ours(draft)

    def run_checkout_gates(self, evidence_packet) -> GateAnswers:
        """Ours: the live Decisions API with a key, a labelled stand-in
        without one. Hers is a different return type and is not wired yet."""
        from backend.decisions import run_checkout_gates as ours

        return ours(evidence_packet)

    # -- delegated to the labelled stub ------------------------------------

    def handle_command(self, session_id: str, command, revisions: dict):
        return self._stub.handle_command(session_id, command, revisions)

    def plan_workflow(self, brief, room_spec, scene):
        return self._stub.plan_workflow(brief, room_spec, scene)

    def run_workflow(self, plan_id: str):
        return self._stub.run_workflow(plan_id)

    def run_recovery(self, recovery_run):
        return self._stub.run_recovery(recovery_run)
