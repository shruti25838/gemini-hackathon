"""handle_command (team contract, Person A). Interprets a spoken/typed request and returns a
CommandResult. It never writes shared state: C's server applies the result (brief patch,
measurement, move, cart review) or starts the returned plan with run_workflow.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from contracts.schemas import DesignBrief, WorkflowPlan
from fit import solver

from . import gemini, intake, workflow


class CommandResult(BaseModel):
    status: Literal["accepted", "pending", "needs_input"]
    action: Literal["run_plan", "measurement", "move", "cart_review", "none"] = "none"
    message: str = ""
    plan: Optional[WorkflowPlan] = None
    brief: Optional[DesignBrief] = None  # proposed brief (C stores it)
    brief_changed: list[str] = []
    payload: dict = {}
    transcript: str = ""
    intent: str = ""
    route_answers: dict = {}
    latency_ms: Optional[int] = None


WALL_WORDS = {"wall_n": ("wall_n", "back", "far", "north"), "wall_e": ("wall_e", "right", "east"),
              "wall_s": ("wall_s", "front", "behind", "south"), "wall_w": ("wall_w", "left", "west")}


def resolve_destination(room, p, dest: str):
    """Natural-language destination ("under the window", "back wall") -> pose against that wall."""
    target_open = None
    for kind in ("window", "door"):
        if kind in dest:
            target_open = next((o for o in room.openings if o.type == kind), None)
    wall = next((w for w, keys in WALL_WORDS.items() if any(k in dest for k in keys)), None)
    if target_open:
        wall = target_open.wall
        along = target_open.offset_m + target_open.width_m / 2
    elif wall:
        along = (room.width_m.value_m if wall in ("wall_n", "wall_s") else room.depth_m.value_m) / 2
    else:
        return None
    return solver._against_wall(room.model_dump(), wall, along, p.w, p.d)


def handle_command(s, text: Optional[str] = None, audio: Optional[bytes] = None,
                   audio_mime: str = "audio/webm") -> CommandResult:
    ctx = workflow.room_summary(s)
    parsed = intake.parse(text=text, audio=audio, audio_mime=audio_mime, context=ctx)  # may raise GeminiError
    cmd = intake.Command(**parsed["command"])
    s.emit("intake", "ok", f"“{cmd.transcript}” -> {cmd.intent}", command=parsed["command"],
           route=parsed["route"]["answers"], agree=parsed["agree"], latency_ms=parsed["latency_ms"])
    base = dict(transcript=cmd.transcript, intent=cmd.intent, route_answers=parsed["route"]["answers"],
                latency_ms=parsed["latency_ms"])
    if parsed["clarify"]:
        s.emit("intake", "needs_input", parsed["clarify"])
        return CommandResult(status="needs_input", message=parsed["clarify"], **base)

    brief, changed = s.brief, []
    if any(v is not None for v in cmd.brief_patch.model_dump().values()):
        brief, changed = intake.apply_brief_patch(s.brief, cmd.brief_patch, s.room.existing_items if s.room else [])
    base.update(brief=brief if changed else None, brief_changed=changed)

    if cmd.intent == "measurement_correction":
        if not cmd.measurement_m:
            return CommandResult(status="needs_input", message="What is the measurement, in metres?", **base)
        return CommandResult(status="accepted", action="measurement",
                             payload={"field": cmd.measurement_field or "width_m", "value_m": cmd.measurement_m}, **base)
    if cmd.intent == "cart_review":
        return CommandResult(status="accepted", action="cart_review", **base)
    if cmd.intent == "move_furniture":
        if not (s.scene and s.scene.placements and s.room):
            return CommandResult(status="needs_input", message="There is nothing to move yet.", **base)
        target = (cmd.move_target or "").lower()
        cands = [p for p in s.scene.placements if not p.locked and (p.category in target or target in p.category)]
        if len(cands) != 1:
            msg = ("Which item should I move? Tap it in the room." if cands or not target
                   else f"I can't find '{target}' in the design.")
            s.emit("move", "needs_input", msg, candidates=[p.key for p in cands])
            return CommandResult(status="needs_input", message=msg, payload={"candidates": [p.key for p in cands]}, **base)
        p = cands[0]
        pose = resolve_destination(s.room, p, (cmd.move_destination or "").lower())
        if not pose:
            return CommandResult(status="needs_input", **base,
                                 message=f"Where should the {p.category} go? Drag it, or name a wall or the window.")
        return CommandResult(status="accepted", action="move",
                             payload={"key": p.key, "x": pose[0], "z": pose[1], "rot": pose[2]}, **base)
    if not s.room:
        return CommandResult(status="needs_input", message="Add a room photo (or use the preset room) first.", **base)

    # fast paths: one validated op, no planner round-trip
    if cmd.intent == "style_edit":
        args = {k: v for k, v in dict(wall_id=cmd.wall_id, color_hex=cmd.color_hex, lighting=cmd.lighting).items() if v}
        plan = workflow.validate_plan(s, WorkflowPlan(goal="style_edit", source="fast_path", steps=[
            workflow.PlanStep(id="style", op="apply_style_edit", args=args)]))
        return CommandResult(status="pending", action="run_plan", plan=plan, **base)
    if cmd.intent == "product_search" and cmd.swap_category and cmd.finish and s.scene and s.scene.placements:
        plan = workflow.validate_plan(s, WorkflowPlan(goal="swap_finish", source="fast_path", steps=[
            workflow.PlanStep(id="swap", op="swap_finish", args={"category": cmd.swap_category, "finish": cmd.finish})]))
        return CommandResult(status="pending", action="run_plan", plan=plan, **base)

    plan = plan_workflow(s, f"{cmd.transcript}\nParsed: {cmd.model_dump_json()}", cmd.intent, brief)
    return CommandResult(status="pending", action="run_plan", plan=plan, **base)


def plan_workflow(s, request: str, route: str, brief: Optional[DesignBrief] = None) -> WorkflowPlan:
    """Neon plan, validated; template plan if Neon is unavailable or its plan is rejected."""
    try:
        plan, meta = workflow.propose_plan(s, request, route, brief=brief)
    except workflow.PlanError as e:
        s.emit("planner", "error", f"Plan rejected by validator: {e}")
        plan, meta = workflow.validate_plan(s, workflow.template_plan(route)), {"source": "template"}
    label = {"neon": "Neon", "mock": "MOCK_AI saved"}.get(meta.get("source"), "Template (Neon unavailable)")
    s.emit("planner", "ok" if meta.get("source") == "neon" else "warn", f"{label} plan: "
           + " -> ".join(st.op for st in plan.steps), plan=plan.model_dump(), rationale=meta.get("rationale"))
    return plan


__all__ = ["CommandResult", "handle_command", "plan_workflow", "resolve_destination", "gemini"]
