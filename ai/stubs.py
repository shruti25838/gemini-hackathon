"""Thin, clearly labelled stand-ins for lane A, so lane C runs alone.

These exist for one reason: Person C's backend must boot, serve every route
and pass its tests without waiting for Person A's agents. They are not an
attempt at lane A's work.

What they are:
  - deliberately shallow. No model is called and no agent runs.
  - honest. Everything they return is labelled `mock` or `local_stand_in`, so
    a stub result can never be mistaken for a real one on screen.
  - swappable in one line. See backend/lane_a.py and
    docs/LANE_C_HANDOFF.md.

Two of the seven are not really stubs, and the handoff doc says so plainly:

  validate_room_spec  delegates to backend/validation.py, which is a real
                      validator. C cannot commit a room without it, so C owns
                      a working implementation rather than waiting.
  run_checkout_gates  delegates to backend/decisions.py, which calls the real
                      Decisions API when a key is configured and falls back to
                      a labelled local stand-in otherwise.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from contracts.activity_event import ActivityEvent
from contracts.common import Money, utc_now
from contracts.design_brief import DesignBrief
from contracts.interfaces import (
    AuditInputs,
    Command,
    CommandResult,
    GateAnswers,
    InputRequest,
    RecoveryAction,
    RoomAnalysis,
    ValidationResult,
)
from contracts.recovery_run import RecoveryRun
from contracts.room_spec import RoomSpec
from contracts.scene_revision import SceneAction, ScenePatch, SceneRevision
from contracts.workflow_plan import PlanConstraints, WorkflowPlan, WorkflowStep

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "fixtures"

STUB_LABEL = "mock"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class StubLaneA:
    """Everything lane A provides, faked just enough for lane C to run."""

    is_stub = True
    name = "stub"

    # -- room ------------------------------------------------------------

    def analyze_room(self, image: bytes, session_id: str) -> RoomAnalysis:
        """Serves fixtures/room_analysis.sample.json. No model is called."""
        payload = _fixture("room_analysis.sample.json")
        payload["session_id"] = session_id
        payload["source"] = STUB_LABEL
        if payload.get("room_spec_draft"):
            payload["room_spec_draft"]["session_id"] = session_id
        return RoomAnalysis.model_validate(payload)

    def validate_room_spec(self, draft: RoomSpec) -> ValidationResult:
        """Not a stub. Delegates to C's real validator.

        Imported lazily so ai/ stays importable without the backend package,
        which matters when a teammate copies this file into their own lane.
        """
        from backend.validation import validate_room_spec as real_validator

        return real_validator(draft)

    # -- commands --------------------------------------------------------

    def handle_command(
        self, session_id: str, command: Command, revisions: dict[str, int]
    ) -> CommandResult:
        """A keyword router, not a language model.

        It recognises a wall colour change and nothing else. Anything it does
        not understand comes back as needs_input rather than a guess, which is
        the same shape lane A's real router must return.
        """
        text = (command.text or "").lower()
        scene_revision = revisions.get("scene_revision", command.scene_revision)

        colours = {
            "olive": "#6b7a4f",
            "white": "#f2f0eb",
            "black": "#1c1c1c",
            "blue": "#3b5f87",
            "green": "#4f7a5f",
            "grey": "#8a8f94",
            "gray": "#8a8f94",
        }
        walls = {
            "north": "wall_n",
            "east": "wall_e",
            "south": "wall_s",
            "west": "wall_w",
        }

        found_colour = next((hexcode for word, hexcode in colours.items() if word in text), None)
        if found_colour and ("wall" in text or "paint" in text):
            wall_id = next((wid for word, wid in walls.items() if word in text), None)
            if wall_id is None:
                return CommandResult(
                    status="needs_input",
                    input_request=InputRequest(
                        interaction_id=f"ask_{uuid.uuid4().hex[:8]}",
                        prompt="Which wall should I paint?",
                        expects="choice",
                        options=["wall_n", "wall_e", "wall_s", "wall_w"],
                    ),
                    message="Stub router: the wall is ambiguous.",
                )
            return CommandResult(
                status="accepted",
                patch=ScenePatch(
                    actions=[
                        SceneAction(
                            type="set_wall_color",
                            payload={"wall_id": wall_id, "hex": found_colour},
                        )
                    ],
                    base_revision=scene_revision,
                    action_id=command.command_id,
                    origin="user",
                ),
                message=f"Stub router: painting {wall_id}.",
            )

        return CommandResult(
            status="needs_input",
            input_request=InputRequest(
                interaction_id=f"ask_{uuid.uuid4().hex[:8]}",
                prompt=(
                    "This is lane A's stub router, which only understands wall "
                    "colour changes. Say which object and what change you want."
                ),
                expects="confirmation",
            ),
            message="Stub router: not understood.",
        )

    # -- workflow --------------------------------------------------------

    def plan_workflow(
        self, brief: DesignBrief, room_spec: RoomSpec, scene: SceneRevision
    ) -> WorkflowPlan:
        """A fixed plan over registered operations. Nothing is generated."""
        return WorkflowPlan(
            plan_id=f"plan_stub_{uuid.uuid4().hex[:8]}",
            session_id=brief.session_id,
            goal=brief.room_use,
            constraints=PlanConstraints(
                merchandise_budget=brief.merchandise_budget,
                locked_objects=list(brief.locked_object_ids),
                keep_door_clear=any(
                    c.kind == "keep_door_clear" for c in brief.hard_constraints
                ),
            ),
            steps=[
                WorkflowStep(id="shop", op="discover_shopify_variants"),
                WorkflowStep(id="fit", op="check_fit", after=["shop"]),
                WorkflowStep(id="review", op="build_cart_review", after=["fit"]),
            ],
            room_revision=room_spec.room_revision,
            scene_revision=scene.scene_revision,
            state="validated",
        )

    def run_workflow(self, plan_id: str) -> Iterable[ActivityEvent]:
        """Yields nothing. Lane C drives its own steps directly."""
        return []

    # -- gates and recovery ----------------------------------------------

    def run_checkout_gates(self, evidence_packet: AuditInputs) -> GateAnswers:
        """Not a stub. Delegates to C's Decisions client.

        With a key it calls the real Decisions API at v1beta and the answers
        are labelled `live`. Without one, a labelled `local_stand_in` answers.
        """
        from backend.decisions import run_checkout_gates as real_gates

        return real_gates(evidence_packet)

    def run_recovery(self, recovery_run: RecoveryRun) -> RecoveryAction:
        """Picks the first allowed action. No agent is consulted."""
        return RecoveryAction(
            action=recovery_run.allowed_actions[0],
            rationale=(
                "Lane A stub: no Managed Agent ran. The first registered action "
                "for this error class was chosen."
            ),
            chosen_by="fallback",
        )


__all__ = ["StubLaneA", "STUB_LABEL"]
