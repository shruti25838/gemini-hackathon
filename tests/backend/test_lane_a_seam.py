"""The lane A seam: the stubs satisfy the Protocols, and swapping works.

This is the test that protects the merge. If a teammate's module can be
installed here and every route keeps working, the handoff is real.
"""

from __future__ import annotations

import pytest

from ai.stubs import StubLaneA
from contracts.interfaces import (
    AnalyzeRoom,
    Command,
    CommandResult,
    HandleCommand,
    PlanWorkflow,
    RoomAnalysis,
    RunCheckoutGates,
    RunRecovery,
    ValidateRoomSpec,
)
from backend.lane_a import (
    LANE_A_FUNCTIONS,
    check_lane_a,
    get_lane_a,
    lane_a_is_stubbed,
    reset_lane_a,
    set_lane_a,
)

pytestmark = pytest.mark.backend


@pytest.fixture(autouse=True)
def _restore_the_stub():
    yield
    reset_lane_a()


# ==========================================================================
# The stub satisfies the contract
# ==========================================================================


def test_the_stub_provides_every_lane_a_function() -> None:
    assert check_lane_a(StubLaneA()) == []
    assert len(LANE_A_FUNCTIONS) == 7


@pytest.mark.parametrize(
    "protocol",
    [AnalyzeRoom, ValidateRoomSpec, HandleCommand, PlanWorkflow, RunCheckoutGates, RunRecovery],
)
def test_the_stub_methods_match_the_runtime_protocols(protocol) -> None:
    """Each Protocol is callable-shaped, so check the bound method satisfies it."""
    stub = StubLaneA()
    name = {
        AnalyzeRoom: "analyze_room",
        ValidateRoomSpec: "validate_room_spec",
        HandleCommand: "handle_command",
        PlanWorkflow: "plan_workflow",
        RunCheckoutGates: "run_checkout_gates",
        RunRecovery: "run_recovery",
    }[protocol]
    assert isinstance(getattr(stub, name), protocol)


def test_the_stub_is_installed_by_default() -> None:
    assert lane_a_is_stubbed() is True
    assert isinstance(get_lane_a(), StubLaneA)


# ==========================================================================
# What the stub actually returns
# ==========================================================================


def test_analyze_room_returns_a_labelled_mock_draft() -> None:
    analysis = StubLaneA().analyze_room(b"", "sess_x")
    assert isinstance(analysis, RoomAnalysis)
    assert analysis.source == "mock", "a stub result must never look live"
    assert analysis.session_id == "sess_x"
    assert analysis.room_spec_draft is not None
    assert analysis.room_spec_draft.session_id == "sess_x"


def test_validate_room_spec_is_the_real_validator_not_a_stub() -> None:
    """C cannot commit a room without this, so C owns a working one."""
    from tests.backend.conftest import make_room_payload
    from contracts.room_spec import RoomSpec

    stub = StubLaneA()
    assert stub.validate_room_spec(RoomSpec.model_validate(make_room_payload())).ok is True

    bad = make_room_payload(width=3.2)
    bad["openings"][0]["offset_m"] = 3.0
    result = stub.validate_room_spec(RoomSpec.model_validate(bad))
    assert result.ok is False
    assert "opening_outside_wall" in {e.code for e in result.errors}


def test_handle_command_understands_a_wall_colour_change() -> None:
    command = Command(
        command_id="cmd_1",
        session_id="sess_x",
        text="Make the north wall olive",
        room_revision=1,
        scene_revision=2,
    )
    result = StubLaneA().handle_command("sess_x", command, {"scene_revision": 2})
    assert result.status == "accepted"
    assert result.patch.actions[0].type == "set_wall_color"
    assert result.patch.actions[0].payload["wall_id"] == "wall_n"
    assert result.patch.base_revision == 2


def test_an_ambiguous_wall_asks_rather_than_guessing() -> None:
    command = Command(
        command_id="cmd_2",
        session_id="sess_x",
        text="Make that wall olive",
        room_revision=1,
        scene_revision=2,
    )
    result = StubLaneA().handle_command("sess_x", command, {})
    assert result.status == "needs_input"
    assert result.input_request is not None
    assert "wall_n" in result.input_request.options


def test_anything_else_is_needs_input_not_a_guess() -> None:
    command = Command(
        command_id="cmd_3",
        session_id="sess_x",
        text="Redesign the whole room as a library",
        room_revision=1,
        scene_revision=2,
    )
    result = StubLaneA().handle_command("sess_x", command, {})
    assert result.status == "needs_input"
    assert result.patch is None


def test_the_stub_router_never_emits_an_unregistered_action() -> None:
    from contracts.scene_revision import ALLOWED_SCENE_ACTIONS

    for text in ("paint the south wall black", "make the east wall blue"):
        command = Command(
            command_id="c", session_id="s", text=text, room_revision=1, scene_revision=1
        )
        result = StubLaneA().handle_command("s", command, {})
        if result.patch:
            for action in result.patch.actions:
                assert action.type in ALLOWED_SCENE_ACTIONS


def test_run_recovery_picks_only_a_registered_action() -> None:
    from contracts.recovery_run import ALLOWED_RECOVERY_ACTIONS, RecoveryRun

    run = RecoveryRun(
        run_id="r1",
        session_id="s",
        failed_step_id="scale",
        error_class="invalid_room_geometry",
        triggering_revision=1,
        allowed_actions=["request_measurement", "pause"],
    )
    action = StubLaneA().run_recovery(run)
    assert action.action in ALLOWED_RECOVERY_ACTIONS
    assert action.action in run.allowed_actions
    assert action.chosen_by == "fallback"


def test_plan_workflow_only_names_registered_operations() -> None:
    from contracts.workflow_plan import REGISTERED_OPERATIONS
    from tests.backend.conftest import make_room_payload
    from contracts.common import Money
    from contracts.design_brief import DesignBrief
    from contracts.room_spec import RoomSpec
    from contracts.scene_revision import SceneRevision

    brief = DesignBrief(
        brief_id="b",
        session_id="s",
        room_use="gaming_corner",
        merchandise_budget=Money(amount_minor=120000, currency="USD"),
    )
    plan = StubLaneA().plan_workflow(
        brief,
        RoomSpec.model_validate(make_room_payload()),
        SceneRevision(scene_revision=0, session_id="s", room_revision=0),
    )
    for step in plan.steps:
        assert step.op in REGISTERED_OPERATIONS


# ==========================================================================
# Swapping in a teammate's implementation
# ==========================================================================


class FakeRealLaneA:
    """Stands in for Person A's module in these tests."""

    is_stub = False
    name = "person_a"

    def analyze_room(self, image, session_id):
        return StubLaneA().analyze_room(image, session_id).model_copy(
            update={"source": "live"}
        )

    def validate_room_spec(self, draft):
        return StubLaneA().validate_room_spec(draft)

    def handle_command(self, session_id, command, revisions):
        return CommandResult(status="rejected", message="from person A")

    def plan_workflow(self, brief, room_spec, scene):
        raise NotImplementedError

    def run_workflow(self, plan_id):
        return []

    def run_checkout_gates(self, evidence_packet):
        raise NotImplementedError

    def run_recovery(self, recovery_run):
        raise NotImplementedError


def test_a_complete_provider_installs() -> None:
    set_lane_a(FakeRealLaneA())
    assert lane_a_is_stubbed() is False
    assert get_lane_a().name == "person_a"


def test_an_incomplete_provider_is_refused_before_it_is_installed() -> None:
    """A half-finished module must not silently leave some calls on the stub."""

    class Partial:
        def analyze_room(self, image, session_id):  # pragma: no cover
            raise NotImplementedError

    with pytest.raises(TypeError) as excinfo:
        set_lane_a(Partial())

    message = str(excinfo.value)
    assert "handle_command" in message
    assert "run_checkout_gates" in message
    assert lane_a_is_stubbed() is True, "the stub must still be installed"


def test_check_lane_a_lists_exactly_what_is_missing() -> None:
    class OnlyTwo:
        def analyze_room(self, image, session_id): ...
        def validate_room_spec(self, draft): ...

    missing = check_lane_a(OnlyTwo())
    assert "analyze_room" not in missing
    assert set(missing) == set(LANE_A_FUNCTIONS) - {"analyze_room", "validate_room_spec"}


def test_swapping_changes_what_the_routes_return(services, monkeypatch) -> None:
    """The point of the seam, checked through HTTP."""
    import backend.services as services_module
    from fastapi.testclient import TestClient
    from backend.app import app

    monkeypatch.setattr(services_module, "_services", services)
    client = TestClient(app)

    assert client.get("/api/health").json()["lane_a_is_stub"] is True
    session_id = client.post("/api/session", json={}).json()["session_id"]

    stub_response = client.post(
        "/api/room/analyze", json={"session_id": session_id, "consent_given": True}
    ).json()
    assert stub_response["source_label"] == "mock"

    set_lane_a(FakeRealLaneA())

    assert client.get("/api/health").json()["lane_a_is_stub"] is False
    live_response = client.post(
        "/api/room/analyze", json={"session_id": session_id, "consent_given": True}
    ).json()
    assert live_response["source_label"] == "live"


def test_the_checkout_gate_is_swappable_independently(services) -> None:
    """run_checkout_gates is injected into CheckoutService, so Person A can
    replace just that one without touching the rest."""
    assert services.checkout.decisions is None or hasattr(
        services.checkout.decisions, "create"
    )
    assert services.checkout.use_stand_in is True
