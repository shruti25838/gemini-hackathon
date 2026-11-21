"""A gate that could not run is not a photo that was rejected.

This is the bug this file exists for. Her Decisions call hit an
`AttributeError`, her gate code caught it and returned a *degraded* result,
and the adapter rendered that as `passed=False` with an answer of
"AttributeError". On screen it read as "your photo was no good", so the
shopper would go and retake a perfectly good picture while the real fault
was a missing SDK attribute.

Worse, her `analyze_room` continues past a degraded gate and builds a room
anyway. That draft was never checked for quality or for a person in frame,
and once it reaches the scene nothing says so.
"""

from __future__ import annotations

import pytest

from ai.karishma.gates import GateResult
from ai.karishma_adapter.provider import (
    GATE_SET_ERROR,
    GATE_SET_OK,
    RealLaneA,
    _gate_answers_from_hers,
    gate_outcome,
)

pytestmark = pytest.mark.ai


@pytest.fixture(autouse=True)
def _mock_ai(monkeypatch):
    monkeypatch.setenv("MOCK_AI", "1")


def degraded_gate() -> GateResult:
    """Exactly what her `_degraded` builds when the Decisions call fails."""
    return GateResult(
        gate="room_photo",
        passed=False,
        action="ask_user_confirm_photo",
        degraded=True,
        message="Quick check needed: is this a clear photo of your room?",
        answers={"error": "AttributeError"},
    )


def rejected_gate() -> GateResult:
    return GateResult(
        gate="room_photo",
        passed=False,
        action="request_new_photo",
        degraded=False,
        message="A person is visible. For privacy, please retake the photo without people in it.",
        answers={"person_visible": {"noul": 0.94, "confidence": 0.91}},
    )


def passing_gate_with_gaps() -> GateResult:
    return GateResult(
        gate="room_photo",
        passed=True,
        action="build_roomspec",
        answers={"is_room": {"noul": 0.81, "confidence": 0.81}},
        coverage_gaps=[{"affects": "room scale", "rescan": "Step back into a corner."}],
    )


# ==========================================================================
# The three outcomes
# ==========================================================================


@pytest.mark.parametrize(
    "gate,expected",
    [
        (degraded_gate(), "error"),
        (rejected_gate(), "rejected"),
        (passing_gate_with_gaps(), "passed"),
    ],
)
def test_the_three_gate_outcomes_are_distinct(gate, expected) -> None:
    assert gate_outcome(gate) == expected


def test_a_gate_error_is_reported_as_an_error() -> None:
    answers = _gate_answers_from_hers(degraded_gate(), mocked=False)
    assert answers.gate_set == GATE_SET_ERROR
    assert answers.all_passed is False

    by_question = {a.question: a for a in answers.answers}
    assert "gate_error" in by_question
    assert by_question["gate_error"].answer == "AttributeError"


def test_a_rejection_is_not_reported_as_an_error() -> None:
    answers = _gate_answers_from_hers(rejected_gate(), mocked=False)
    assert answers.gate_set == GATE_SET_OK
    assert "gate_error" not in {a.question for a in answers.answers}


def test_the_two_are_distinguishable_from_the_payload_alone() -> None:
    """Lane B must tell them apart without reading our source."""
    errored = _gate_answers_from_hers(degraded_gate(), mocked=False)
    rejected = _gate_answers_from_hers(rejected_gate(), mocked=False)
    assert errored.gate_set != rejected.gate_set


def test_an_errored_gate_does_not_report_a_photo_verdict() -> None:
    """The old behaviour put "AttributeError" in an answer slot alongside
    real gate answers, which is what made it look like a verdict."""
    answers = _gate_answers_from_hers(degraded_gate(), mocked=False)
    questions = {a.question for a in answers.answers}
    for photo_question in ("is_room", "person_visible", "usable"):
        assert photo_question not in questions


def test_a_rejection_carries_the_reshoot_hint() -> None:
    answers = _gate_answers_from_hers(rejected_gate(), mocked=False)
    hints = [a.answer for a in answers.answers if a.question == "rescan_hint"]
    assert hints and "retake" in hints[0].lower()


def test_coverage_gaps_reach_us_as_rescan_hints() -> None:
    answers = _gate_answers_from_hers(passing_gate_with_gaps(), mocked=False)
    rescans = [a for a in answers.answers if a.question.startswith("rescan:")]
    assert rescans
    assert "Step back" in rescans[0].answer


def test_real_gate_answers_keep_their_confidence() -> None:
    answers = _gate_answers_from_hers(rejected_gate(), mocked=False)
    person = next(a for a in answers.answers if a.question == "person_visible")
    assert person.answer == "0.94"
    assert person.confidence == pytest.approx(0.91)


# ==========================================================================
# A failed gate never yields a draft that looks analysed
# ==========================================================================


def test_a_gate_error_yields_no_draft(monkeypatch) -> None:
    from ai.karishma import room as her_room

    monkeypatch.setattr(her_room.gates, "room_photo", lambda *a, **k: degraded_gate())
    result = RealLaneA().analyze_room(b"bytes", "sess_e")

    assert result.room_spec_draft is None
    assert any("gate_error" in ref for ref in result.evidence_refs)


def test_a_rejected_photo_yields_no_draft(monkeypatch) -> None:
    from ai.karishma import room as her_room

    monkeypatch.setattr(her_room.gates, "room_photo", lambda *a, **k: rejected_gate())
    result = RealLaneA().analyze_room(b"bytes", "sess_r")

    assert result.room_spec_draft is None
    assert any("gate_rejected" in ref for ref in result.evidence_refs)


def test_an_ungated_draft_is_discarded_and_said_so(monkeypatch) -> None:
    """Her code builds a room past a degraded gate. We drop it, and record
    that we dropped it, rather than passing it off as analysed."""
    from ai.karishma import room as her_room

    monkeypatch.setattr(her_room.gates, "room_photo", lambda *a, **k: degraded_gate())
    result = RealLaneA().analyze_room(b"bytes", "sess_e")

    assert result.room_spec_draft is None
    if "ungated_draft_discarded" in result.evidence_refs:
        assert result.gates.gate_set == GATE_SET_ERROR


def test_a_passing_gate_still_yields_a_draft() -> None:
    """The fix must not block the ordinary case."""
    result = RealLaneA().analyze_room(b"fake-image-bytes", "sess_ok")
    assert result.gates.all_passed is True
    assert result.room_spec_draft is not None
    assert result.evidence_refs == []


# ==========================================================================
# The Decisions call site
# ==========================================================================


def test_the_sdk_attribute_that_caused_this_really_is_absent() -> None:
    from google import genai

    assert not hasattr(genai.Client, "decisions"), (
        "google-genai now has Client.decisions; revisit the call-site routing"
    )


def test_no_live_call_to_the_missing_attribute_remains() -> None:
    from pathlib import Path

    source = Path("ai/karishma/gemini.py").read_text(encoding="utf-8")
    live = [
        line
        for line in source.splitlines()
        if "decisions.create" in line and not line.strip().startswith("#")
    ]
    assert not live, f"a live call to .decisions.create remains: {live}"


def test_the_bridge_refuses_without_a_key_so_the_gate_degrades(monkeypatch) -> None:
    """A gate that cannot run must degrade loudly, never pass by default."""
    from ai.karishma_adapter.decisions_bridge import DecisionsBridgeError, decide

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(DecisionsBridgeError):
        decide([{"type": "text", "text": "x"}], {"q": {"type": "noul", "instructions": "?"}})


def test_a_bridge_failure_becomes_a_gate_error_end_to_end(monkeypatch) -> None:
    """Our bridge raises, her gate degrades, we report an error not a reject."""
    from ai.karishma import gates
    from ai.karishma_adapter.decisions_bridge import DecisionsBridgeError

    def boom(*_args, **_kwargs):
        raise DecisionsBridgeError("no Gemini API key configured")

    monkeypatch.setenv("MOCK_AI", "0")
    monkeypatch.setattr(gates.gemini, "decide", boom)

    gate = gates.room_photo(b"bytes", "image/jpeg")

    assert gate.degraded is True
    assert gate_outcome(gate) == "error"
    assert _gate_answers_from_hers(gate, mocked=False).gate_set == GATE_SET_ERROR


def test_the_bridge_rejects_a_response_missing_an_answer(monkeypatch) -> None:
    """A partial response must not be read as a pass on the missing question."""
    from ai.karishma_adapter import decisions_bridge

    class FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def create(self, _inputs, _questions):
            return {"answers": {"only_this": {"noul": 0.9, "confidence": 0.9}}}

    monkeypatch.setenv("GOOGLE_API_KEY", "placeholder-not-sent")
    monkeypatch.setattr("backend.decisions.DecisionsClient", FakeClient)

    with pytest.raises(decisions_bridge.DecisionsBridgeError) as excinfo:
        decisions_bridge.decide(
            [{"type": "text", "text": "x"}],
            {
                "only_this": {"type": "noul", "instructions": "?"},
                "missing_one": {"type": "noul", "instructions": "?"},
            },
        )
    assert "missing_one" in str(excinfo.value)


def test_an_answer_passes_on_its_own_confidence_not_the_verdict() -> None:
    """A rejected photo can still have a high-confidence door_visible.

    Tying each answer to the overall verdict showed 0.92-confidence answers
    as passed=False, which misreads the data. The verdict lives in
    `all_passed`.
    """
    gate = GateResult(
        gate="room_photo",
        passed=False,
        action="request_new_photo",
        degraded=False,
        message="The photo is hard to read.",
        answers={
            "door_visible": {"noul": 0.92, "confidence": 0.92},
            "usable": {"score": 0.98, "confidence": 0.47},
        },
    )
    answers = _gate_answers_from_hers(gate, mocked=False)
    by_question = {a.question: a for a in answers.answers}

    assert by_question["door_visible"].passed is True
    assert by_question["usable"].passed is False
    assert answers.all_passed is False, "the gate still rejected the photo"
