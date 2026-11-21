"""Regenerate Lane A fixtures (fixtures/*.sample.json) by running A's real functions with MOCK_AI=1.
Run: MOCK_AI=1 ETHYNEON_FIXTURE_CATALOG=1 python scripts/gen_ai_fixtures.py"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["MOCK_AI"] = "1"
os.environ["ETHYNEON_FIXTURE_CATALOG"] = "1"

import ai  # noqa: E402
from ai import agents, gates, vision  # noqa: E402
from backend import state  # noqa: E402
from backend.shop import ShopError  # noqa: E402
from contracts.schemas import PlacementReview, Pose, SceneRevision  # noqa: E402
from fit import solver  # noqa: E402

OUT = ROOT / "fixtures"


def dump(name, obj):
    data = obj.model_dump() if hasattr(obj, "model_dump") else obj
    (OUT / f"{name}.sample.json").write_text(json.dumps(data, indent=1, default=str) + "\n")
    print("wrote", name)


analysis = ai.analyze_room(b"\xff\xd8mock", "fixture")
dump("room_analysis", analysis)
room = vision.anchor_measurement(analysis.room_spec, "width_m", 3.2)
room.openings[0].motion = vision.DoorMotion(hinge_side="left", swing="in", source="inferred", confidence=0.8, evidence_ts="00:02")
dump("room_spec", room)

s = state.new_session()
s.set_room(room)
s.commit_scene(SceneRevision(rev=0, room_revision=room.room_revision))
cmd = ai.handle_command(s, text="build a purple and blue dual-monitor setup under $1,200")
dump("command_result", cmd)
dump("design_brief", cmd.brief)
dump("workflow_plan", cmd.plan)
dump("gate_answers", {"room_photo": analysis.gate.model_dump(),
                      "route": gates.route("make the wall olive").model_dump(),
                      "checkout_audit": ai.run_checkout_gates({"fixture": True}).model_dump()})

# placement review: desk dragged into the door swing, with the server-checked alternative
s.brief = cmd.brief
ai.run_workflow(s, cmd.plan)
desk = next(p for p in s.scene.placements if p.category == "desk")
door = room.openings[0]
x = door.offset_m + door.width_m / 2
res = agents.review_move(room.model_dump(), [p.model_dump() for p in s.scene.placements], desk.key, x, 0.4, 0,
                         s.brief.model_dump(), lambda *a, **k: None)
dump("placement_review", PlacementReview(
    base_scene_rev=s.scene.rev, selected_key=desk.key, requested_pose=Pose(x=x, z=0.4, rot=0),
    requested_fit=res["report"], conflicts=res["conflicts"],
    style_assessment=res["alternative"].get("style_assessment"), alternatives_tested=1,
    alternative_placements=res["alternative"]["placements"], alternative_fit=res["alternative"]["report"],
    tester_findings=res["alternative"]["findings"], checked_by=res["alternative"]["report"]["checked_by"]))

from ai import recovery  # noqa: E402
dump("recovery_run", ai.run_recovery(recovery.new_run("discover_shopify_variants", ShopError("Shopify rate limited"),
                                                      s.scene.rev, {"scene_rev": s.scene.rev})))
