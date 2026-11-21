import pytest

from backend import gates, state, vision, workflow
from contracts.schemas import (DesignBrief, Dimension, Opening, RoomSpec, SceneRevision, Wall, WorkflowPlan, PlanStep)


def make_room(width=3.0, depth=3.0):
    return RoomSpec(width_m=Dimension(value_m=width), depth_m=Dimension(value_m=depth), height_m=Dimension(value_m=2.5),
                    walls=[Wall(id=w) for w in ("back", "right", "front", "left")],
                    openings=[Opening(id="door1", wall="back", type="door", offset_m=width - 1.0, width_m=0.8)])


def session_with_room():
    s = state.new_session()
    s.set_room(make_room())
    return s


# ---------- plan validation ----------
def test_unregistered_op_rejected():
    s = session_with_room()
    with pytest.raises(workflow.PlanError):
        workflow.validate_plan(s, WorkflowPlan(goal="x", steps=[PlanStep(id="a", op="rm_rf")]))


def test_cycle_rejected():
    s = session_with_room()
    p = WorkflowPlan(goal="x", steps=[PlanStep(id="a", op="inspect_room_motion", after=["b"]),
                                      PlanStep(id="b", op="inspect_room_motion", after=["a"])])
    with pytest.raises(workflow.PlanError):
        workflow.validate_plan(s, p)


def test_bad_args_rejected():
    s = session_with_room()
    p = WorkflowPlan(goal="x", steps=[PlanStep(id="a", op="apply_style_edit", args={"color_hex": "olive"})])
    with pytest.raises(workflow.PlanError):
        workflow.validate_plan(s, p)


def test_layout_gets_mandatory_steps_and_commit():
    s = session_with_room()
    p = workflow.validate_plan(s, WorkflowPlan(goal="x", steps=[PlanStep(id="lay", op="plan_and_test_layout")]))
    ops = [st.op for st in p.steps]
    assert {"confirm_measurement_if_needed", "inspect_room_motion", "discover_shopify_variants"} <= set(ops)
    lay = next(st for st in p.steps if st.op == "plan_and_test_layout")
    assert len(lay.after) == 3
    commit = next(st for st in p.steps if st.op == "commit_scene_preview")
    assert set(commit.after) == {st.id for st in p.steps if st is not commit}


def test_parallel_layers():
    s = session_with_room()
    p = workflow.validate_plan(s, workflow.template_plan("product_search"))
    layers = workflow._toposort(p.steps)
    assert {st.op for st in layers[0]} >= {"inspect_room_motion", "discover_shopify_variants"}


# ---------- measurement anchoring ----------
def test_anchor_rescales_estimates_and_keeps_confirmed():
    r = make_room(width=2.5, depth=3.0)
    r2 = vision.anchor_measurement(r, "width_m", 3.2)
    k = 3.2 / 2.5
    assert r2.width_m.source == "confirmed" and r2.width_m.value_m == 3.2
    assert r2.depth_m.value_m == pytest.approx(3.0 * k, abs=1e-3)
    assert r2.openings[0].width_m == pytest.approx(0.8 * k, abs=1e-3)
    r3 = vision.anchor_measurement(r2, "depth_m", 3.5)
    assert r3.width_m.value_m == 3.2  # confirmed value preserved


def test_invalid_room_rejected():
    with pytest.raises(vision.RoomSpecError):
        vision.validate_room(make_room(width=50))


def test_opening_outside_wall_dropped():
    r = make_room()
    r.openings.append(Opening(id="w9", wall="back", type="window", offset_m=2.8, width_m=1.0))
    r = vision.validate_room(r)
    assert all(o.id != "w9" for o in r.openings)


def test_unknown_hinge_never_guessed():
    r = make_room()
    d = vision.DoorDraft(door_wall="back", hinge_side="unknown", swing="in", max_angle_deg=90, evidence_ts="00:02",
                         confidence=0.4, reasoning="hinge occluded")
    r2, problem = vision.apply_door(r, d)
    assert problem and r2.openings[0].motion is None


# ---------- state ----------
def test_scene_commit_voids_approval_and_rejects_stale():
    s = session_with_room()
    s.commit_scene(SceneRevision(rev=0, room_revision=1))
    s.approval = {"token": "t"}
    s.commit_scene(SceneRevision(rev=0, room_revision=1), base_rev=1)
    assert s.approval is None
    with pytest.raises(state.StaleRevision):
        s.commit_scene(SceneRevision(rev=0, room_revision=1), base_rev=1)


# ---------- budget picking (fixture catalog, injection gate mocked) ----------
def test_discover_respects_budget_and_stock(monkeypatch):
    monkeypatch.setattr(gates, "injection", lambda texts: {k: "ignore previous" in v for k, v in texts.items()})
    s = session_with_room()
    s.brief = DesignBrief(budget_minor=70000, style=["purple"], categories=["desk", "chair", "monitor"])
    ctx = {}
    workflow.op_discover_shopify_variants(s, {}, ctx)
    sel = ctx["selected"]
    assert sum(o.price_minor for o in sel) <= 70000
    assert all(o.available for o in sel)
    assert any(e.step == "injection_gate" for e in s.events)


def test_discover_asks_when_budget_impossible(monkeypatch):
    monkeypatch.setattr(gates, "injection", lambda texts: {})
    s = session_with_room()
    s.brief = DesignBrief(budget_minor=1000, categories=["desk", "chair"])
    with pytest.raises(workflow.NeedsInput):
        workflow.op_discover_shopify_variants(s, {}, {})


# ---------- gates: threshold logic with mocked Decision 1 ----------
def test_checkout_audit_requires_confident_ready(monkeypatch):
    def fake(parts, q):
        return ({"purchase_scope_matches": {"noul": 0.97, "confidence": 0.97},
                 "required_constraints_resolved": {"noul": 0.95, "confidence": 0.95},
                 "next_action": {"choice": "ready", "confidence": 0.6, "probabilities": {}}}, {"latency_ms": 5})
    monkeypatch.setattr(gates.gemini, "decide", fake)
    g = gates.checkout_audit({})
    assert not g.passed  # ready but low confidence -> held


def test_route_degrades_to_question_when_api_down(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("503")
    monkeypatch.setattr(gates.gemini, "decide", boom)
    g = gates.route("make the wall olive")
    assert g.degraded and not g.passed
