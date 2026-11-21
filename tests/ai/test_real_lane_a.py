"""RealLaneA: Karishma's analyze_room, the rest delegated and labelled.

The point of these is that nobody can mistake a delegated stub for a working
agent, and that lane A still cannot write state.
"""

from __future__ import annotations

import uuid

import pytest

from ai.karishma_adapter.provider import DELEGATION, RealLaneA
from backend.lane_a import (
    LANE_A_FUNCTIONS,
    check_lane_a,
    lane_a_delegation,
    lane_a_is_stubbed,
    lane_a_name,
    reset_lane_a,
    set_lane_a,
)

pytestmark = pytest.mark.ai


@pytest.fixture(autouse=True)
def _mock_ai(monkeypatch):
    """Her gemini module reads MOCK_AI directly from the environment."""
    monkeypatch.setenv("MOCK_AI", "1")
    yield
    reset_lane_a()


# ==========================================================================
# It installs
# ==========================================================================


def test_it_satisfies_every_lane_a_function() -> None:
    assert check_lane_a(RealLaneA()) == []


def test_set_lane_a_installs_it() -> None:
    set_lane_a(RealLaneA())
    assert lane_a_is_stubbed() is False
    assert lane_a_name() == "karishma+ours"


def test_every_function_is_labelled() -> None:
    """A blank or missing label would be worse than an honest 'stub'."""
    set_lane_a(RealLaneA())
    labels = lane_a_delegation()
    assert set(labels) == set(LANE_A_FUNCTIONS)
    for name, label in labels.items():
        assert label and label != "unknown", f"{name} has no label"


def test_the_labels_say_which_are_real() -> None:
    set_lane_a(RealLaneA())
    labels = lane_a_delegation()
    assert labels["analyze_room"].startswith("real")
    for name in ("handle_command", "plan_workflow", "run_workflow", "run_recovery"):
        assert "stub" in labels[name]
    for name in ("validate_room_spec", "run_checkout_gates"):
        assert "ours" in labels[name]


def test_health_reports_the_delegation(monkeypatch, services) -> None:
    import backend.services as services_module
    from fastapi.testclient import TestClient

    from backend.app import app

    monkeypatch.setattr(services_module, "_services", services)
    set_lane_a(RealLaneA())

    body = TestClient(app).get("/api/health").json()
    assert body["lane_a"] == "karishma+ours"
    assert body["lane_a_is_stub"] is False
    assert body["lane_a_functions"]["analyze_room"].startswith("real")
    assert "stub" in body["lane_a_functions"]["handle_command"]


# ==========================================================================
# analyze_room, for real, offline
# ==========================================================================


def test_analyze_room_returns_our_contract() -> None:
    from contracts.interfaces import RoomAnalysis
    from contracts.room_spec import RoomSpec

    result = RealLaneA().analyze_room(b"fake-image-bytes", "sess_a")

    assert isinstance(result, RoomAnalysis)
    assert result.session_id == "sess_a"
    assert isinstance(result.room_spec_draft, RoomSpec)
    assert result.room_spec_draft.session_id == "sess_a"


def test_the_draft_passes_our_own_validator() -> None:
    """The real test of the bridge: her draft, our rules."""
    from backend.validation import validate_room_spec

    provider = RealLaneA()
    result = provider.analyze_room(b"fake-image-bytes", "sess_a")
    validation = provider.validate_room_spec(result.room_spec_draft)
    assert validation.ok is True, [e.model_dump() for e in validation.errors]


def test_a_mocked_analysis_is_labelled_mock() -> None:
    """A mock must never look live."""
    result = RealLaneA().analyze_room(b"fake-image-bytes", "sess_a")
    assert result.source == "mock"
    assert all(answer.source == "mock" for answer in result.gates.answers)


def test_the_gates_come_across_with_answers() -> None:
    result = RealLaneA().analyze_room(b"fake-image-bytes", "sess_a")
    assert result.gates.answers
    assert result.gates.gate_set


def test_the_draft_has_four_walls_and_our_field_names() -> None:
    draft = RealLaneA().analyze_room(b"fake-image-bytes", "sess_a").room_spec_draft
    assert len(draft.walls) == 4
    assert draft.dimensions_m.width_m.value_m > 0
    for opening in draft.openings:
        assert opening.opening_id
        assert opening.wall_id.startswith("wall_")


def test_analyze_room_commits_nothing(services) -> None:
    """Lane A proposes. Only commit_revision writes."""
    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    services.db.create_session(session_id, "shopper")

    RealLaneA().analyze_room(b"fake-image-bytes", session_id)

    assert services.db.current_revision(session_id, "room") == 0
    assert services.revisions.current_room(session_id) is None


# ==========================================================================
# Lane A cannot write
# ==========================================================================


def test_her_session_refuses_to_commit_a_scene() -> None:
    from ai.karishma.shim import LaneANotWired, Session

    with pytest.raises(LaneANotWired):
        Session().commit_scene(object())


def test_her_session_refuses_to_set_the_room() -> None:
    from ai.karishma.shim import LaneANotWired, Session

    with pytest.raises(LaneANotWired):
        Session().set_room(object())


def test_a_refusal_is_not_a_silent_no_op() -> None:
    """A no-op commit would be worse: her workflow would carry on from a
    state that does not exist."""
    from ai.karishma.shim import LaneANotWired, Session

    with pytest.raises(LaneANotWired) as excinfo:
        Session().commit_scene(object())
    assert "commit_revision" in str(excinfo.value)


# ==========================================================================
# Delegation actually delegates
# ==========================================================================


def test_validate_room_spec_is_ours(services) -> None:
    from tests.backend.conftest import make_room_payload
    from contracts.room_spec import RoomSpec

    provider = RealLaneA()
    bad = make_room_payload(width=3.2)
    bad["openings"][0]["offset_m"] = 3.0

    result = provider.validate_room_spec(RoomSpec.model_validate(bad))
    assert result.ok is False
    assert "opening_outside_wall" in {e.code for e in result.errors}


def test_run_checkout_gates_is_ours() -> None:
    from contracts.common import Money
    from contracts.interfaces import AuditInputs, BudgetLine

    line = BudgetLine(
        variant_id="gid://shopify/ProductVariant/1",
        quantity=1,
        unit_price=Money(amount_minor=1000, currency="USD"),
    )
    packet = AuditInputs(
        session_id="s",
        scene_revision=1,
        brief_revision=1,
        approved_lines=[line],
        merchandise_subtotal=Money(amount_minor=1000, currency="USD"),
        fit_status="fits",
        live_cart_lines=[line],
        approval_fingerprint="a",
        cart_fingerprint="b",
    )
    answers = RealLaneA().run_checkout_gates(packet)
    assert answers.gate_set == "checkout_audit"
    assert {a.source for a in answers.answers} <= {"live", "local_stand_in", "mock"}


def test_the_stub_delegations_still_work() -> None:
    from contracts.recovery_run import RecoveryRun

    provider = RealLaneA()
    action = provider.run_recovery(
        RecoveryRun(
            run_id="r",
            session_id="s",
            failed_step_id="x",
            error_class="model_timeout",
            triggering_revision=1,
            allowed_actions=["pause"],
        )
    )
    assert action.action == "pause"
    assert action.chosen_by == "fallback"


def test_the_delegation_map_matches_the_methods() -> None:
    """Keeps the /api/health labels honest as the adapter grows."""
    assert set(DELEGATION) == set(LANE_A_FUNCTIONS)
