"""handle_command (team contract, Person A). Interprets a spoken/typed request and returns a
CommandResult. It never writes shared state: C's server applies the result (brief patch,
measurement, move, cart review) or starts the returned plan with run_workflow.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from ._vendor.kschemas import DesignBrief, WorkflowPlan
from ._vendor import solver

from . import gates, gemini, intake, workflow


class CommandResult(BaseModel):
    status: Literal["accepted", "pending", "needs_input"]
    action: Literal["run_plan", "plan_and_run", "measurement", "move", "cart_review", "none"] = "none"
    message: str = ""
    plan: Optional[WorkflowPlan] = None
    brief: Optional[DesignBrief] = None  # proposed brief (C stores it)
    brief_changed: list[str] = []
    payload: dict = {}
    transcript: str = ""
    intent: str = ""
    route_answers: dict = {}
    latency_ms: Optional[int] = None
    spoken_reply: str = ""


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


VAGUE = ("that", "this", "it", "the one", "there", "these", "those")


def _label(s, p) -> str:
    o = s.offers.get(p.variant_id) if p.variant_id else None
    name = f"{o.title} ({o.variant_title})" if o else p.category
    return f"{p.category}: {name} at x={p.x:.1f} m, z={p.z:.1f} m" + (" [shopper's own, locked]" if p.locked else "")


def resolve_target(s, said: str, category_hint: str, selected_key: Optional[str],
                   frame: Optional[tuple[bytes, str]], allow_locked: bool = False):
    """Return (placement, gate_result|None). Unambiguous category match needs no model call;
    vague words, several matches or a camera frame go to the Decision 1 target gate."""
    pool = [p for p in s.scene.placements if allow_locked or not p.locked]
    hint = (category_hint or "").lower()
    by_cat = [p for p in pool if hint and (p.category in hint or hint in p.category)]
    vague = not hint or any(w in f" {said.lower()} " for w in (f" {v} " for v in VAGUE))
    if len(by_cat) == 1 and not vague and not frame:
        return by_cat[0], None
    if selected_key and not frame and not by_cat and vague:
        sel = next((p for p in pool if p.key == selected_key), None)
        if sel:
            return sel, None
    cands = by_cat if len(by_cat) > 1 else pool
    g = gates.target_selection(said, [{"key": p.key, "label": _label(s, p)} for p in cands], selected_key, frame)
    s.emit("gate.target", "ok" if g.passed else "needs_input",
           f"Target: {g.action}" if g.passed else g.message, answers=g.answers, latency_ms=g.latency_ms,
           candidates=[p.key for p in cands])
    hit = next((p for p in cands if p.key == g.action), None) if g.passed else None
    return hit, g


def handle_command(s, text: Optional[str] = None, audio: Optional[bytes] = None,
                   audio_mime: str = "audio/webm", selected_key: Optional[str] = None,
                   frame: Optional[tuple[bytes, str]] = None) -> CommandResult:
    ctx = workflow.room_summary(s)
    parsed = intake.parse(text=text, audio=audio, audio_mime=audio_mime, context=ctx)  # may raise GeminiError
    cmd = intake.Command(**parsed["command"])
    s.emit("intake", "ok", f"“{cmd.transcript}” -> {cmd.intent}", command=parsed["command"],
           route=parsed["route"]["answers"], agree=parsed["agree"], latency_ms=parsed["latency_ms"])
    reply = cmd.spoken_reply.strip()
    if any(w in reply.lower() for w in ("purchased", "bought", "paid", "added to your cart", "added it to the cart")):
        reply = ""  # never let a spoken reply claim a money step happened
    base = dict(transcript=cmd.transcript, intent=cmd.intent, route_answers=parsed["route"]["answers"],
                latency_ms=parsed["latency_ms"], spoken_reply=reply)
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
        p, g = resolve_target(s, cmd.transcript, cmd.move_target or "", selected_key, frame)
        if p is None:
            msg = (g.message if g else "") or "Which item should I move? Tap it in the room."
            return CommandResult(status="needs_input", message=msg,
                                 payload={"candidates": [x.key for x in s.scene.placements if not x.locked]}, **base)
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
    if (cmd.intent == "product_search" and cmd.finish and s.scene and s.scene.placements
            and (not cmd.swap_category or any(f" {v} " in f" {cmd.transcript.lower()} " for v in VAGUE))):
        p, g = resolve_target(s, cmd.transcript, cmd.swap_category or "", selected_key, frame)
        if p is None:
            return CommandResult(status="needs_input", message=(g.message if g else "Which item? Tap it in the room."),
                                 payload={"candidates": [x.key for x in s.scene.placements if not x.locked]}, **base)
        cmd.swap_category = p.category if p.category in ("desk", "chair", "monitor", "lighting", "storage") else cmd.swap_category
    if cmd.intent == "product_search" and cmd.swap_category and cmd.finish and s.scene and s.scene.placements:
        plan = workflow.validate_plan(s, WorkflowPlan(goal="swap_finish", source="fast_path", steps=[
            workflow.PlanStep(id="swap", op="swap_finish", args={"category": cmd.swap_category, "finish": cmd.finish})]))
        return CommandResult(status="pending", action="run_plan", plan=plan, **base)

    # Neon planning takes seconds: return now (so voice can answer) and let the server plan + run in background
    return CommandResult(status="pending", action="plan_and_run", **base,
                         payload={"request": f"{cmd.transcript}\nParsed: {cmd.model_dump_json()}", "route": cmd.intent})


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
