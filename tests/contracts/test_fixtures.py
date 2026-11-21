"""Every fixture validates against its contract model (team contract: required check on every PR)."""
import json
from pathlib import Path

import pytest

from ai import CommandResult, RoomAnalysis
from contracts.schemas import DesignBrief, PlacementReview, RecoveryRun, RoomSpec, WorkflowPlan

FIX = Path(__file__).resolve().parents[2] / "fixtures"
MODELS = {"room_spec": RoomSpec, "design_brief": DesignBrief, "workflow_plan": WorkflowPlan,
          "placement_review": PlacementReview, "recovery_run": RecoveryRun, "room_analysis": RoomAnalysis,
          "command_result": CommandResult}


@pytest.mark.parametrize("name", sorted(MODELS))
def test_fixture_validates(name):
    MODELS[name].model_validate_json((FIX / f"{name}.sample.json").read_text())


def test_wall_ids_and_fit_values_follow_contract():
    spec = RoomSpec.model_validate_json((FIX / "room_spec.sample.json").read_text())
    assert {w.id for w in spec.walls} == {"wall_n", "wall_e", "wall_s", "wall_w"}
    pr = PlacementReview.model_validate_json((FIX / "placement_review.sample.json").read_text())
    assert pr.requested_fit.status == "does_not_fit" and any(c.kind == "door_swing" for c in pr.conflicts)
    assert pr.alternative_fit.status in ("fits", "tight")


def test_mock_ai_never_claims_sandbox():
    pr = json.loads((FIX / "placement_review.sample.json").read_text())
    assert not any(c.endswith("sandbox") for c in pr["checked_by"])


def test_mock_outputs_are_labeled_mock():
    assert json.loads((FIX / "workflow_plan.sample.json").read_text())["source"] == "mock"
    assert json.loads((FIX / "recovery_run.sample.json").read_text())["chosen_by"] == "mock"
