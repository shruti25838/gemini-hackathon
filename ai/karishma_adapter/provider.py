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


def _gate_answers_from_hers(gate: Any, *, mocked: bool) -> GateAnswers:
    """Her single `GateResult` into our list of `GateAnswer`.

    Her gate carries a free-form `answers` dict. Each entry becomes an
    answer; a confidence is used only when she actually supplied one, and
    otherwise the answer is recorded with zero confidence so it cannot pass
    a threshold by accident.
    """
    source = "mock" if mocked else "live"
    answers: list[GateAnswer] = []

    raw = gate.answers if isinstance(getattr(gate, "answers", None), dict) else {}
    for question, value in raw.items():
        confidence = 0.0
        answer_text = str(value)
        if isinstance(value, dict):
            confidence = float(value.get("confidence", 0.0) or 0.0)
            answer_text = str(value.get("answer", value.get("choice", value)))
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
                passed=bool(gate.passed) and confidence >= 0.80,
                source=source,
            )
        )

    if not answers:
        answers.append(
            GateAnswer(
                gate=gate.gate,
                question="passed",
                answer="yes" if gate.passed else "no",
                confidence=0.0 if gate.degraded else 1.0,
                threshold=0.80,
                passed=bool(gate.passed) and not gate.degraded,
                source=source,
            )
        )

    return GateAnswers(
        gate_set=gate.gate,
        answers=answers,
        all_passed=bool(gate.passed) and not gate.degraded,
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

        Returns a draft only. Nothing is committed here; the client corrects
        it and posts it to /api/room/confirm, where our commit_revision is
        still the only writer.
        """
        from ai.karishma import room as her_room
        from ai.karishma.gemini import mock_ai

        result = her_room.analyze_room(image, session_id)
        mocked = mock_ai()

        draft: Optional[RoomSpec] = None
        if result.room_spec is not None:
            draft = bridge.room_spec_to_ours(result.room_spec, session_id=session_id)

        return RoomAnalysis(
            analysis_id=f"analysis_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            gates=_gate_answers_from_hers(result.gate, mocked=mocked),
            room_spec_draft=draft,
            confidence=None,
            evidence_refs=[],
            # A mock must never look live.
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
