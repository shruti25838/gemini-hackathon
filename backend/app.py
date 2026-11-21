"""Ethyneon API. Run: uvicorn backend.app:app --reload --port 8000"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from contracts.schemas import DoorMotion, SceneRevision  # noqa: E402

from . import agents, checkout, gates, gemini, intake, shop, solver, state, vision, workflow  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
app = FastAPI(title="Ethyneon")
FRONT = Path(__file__).resolve().parent.parent / "frontend"
MAX_UPLOAD = 40 * 1024 * 1024


def S(sid: str) -> state.Session:
    try:
        return state.get(sid)
    except KeyError:
        raise HTTPException(404, "unknown session")


def _bg(s: state.Session, fn, *a):
    def run():
        try:
            fn(*a)
        except Exception as e:  # never leak a stack trace to the user
            s.emit("workflow", "error", f"{type(e).__name__}: {str(e)[:300]}")
    threading.Thread(target=run, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(FRONT / "index.html")


app.mount("/static", StaticFiles(directory=FRONT), name="static")


@app.get("/api/health")
def health():
    return {"gemini": gemini.configured(), "shopify_live": shop.live(), "fixture_catalog": shop.fixture_mode()}


@app.post("/api/session")
def create_session():
    s = state.new_session()
    s.emit("session", "info", "Session started")
    return {"id": s.id}


@app.get("/api/session/{sid}/state")
def get_state(sid: str):
    s = S(sid)
    sel = []
    if s.scene:
        for vid in [p.variant_id for p in s.scene.placements if p.variant_id] + s.extra_variant_ids:
            if vid in s.offers:
                sel.append(s.offers[vid].model_dump())
    return {
        "room": s.room.model_dump() if s.room else None,
        "brief": s.brief.model_dump(),
        "scene": s.scene.model_dump() if s.scene else None,
        "fit": s.fit.model_dump() if s.fit else None,
        "selected": sel,
        "subtotal_minor": sum(o["price_minor"] for o in sel),
        "pending": s.pending_input["request"] if s.pending_input else None,
        "approval": {k: s.approval[k] for k in ("scene_rev", "subtotal_minor")} if s.approval else None,
        "cart": {k: v for k, v in (s.cart or {}).items() if k != "checkout_url"} or None,
        "audit": s.audit,
        "alternative": s.last_alternative,
        "plan": s.plan.model_dump() if s.plan else None,
        "door_arcs": _door_arcs(s),
    }


def _door_arcs(s: state.Session) -> list[dict]:
    """Swept-area outlines for the 3D view, computed by the same solver code."""
    import math
    if not s.room:
        return []
    room, out = s.room.model_dump(), []
    for o in room["openings"]:
        if o["type"] != "door" or not o.get("motion") or o["motion"]["swing"] != "in":
            continue
        (hx, hz), n, a, r = solver.door_geometry(room, o)
        amax = math.radians(o["motion"].get("max_angle_deg", 90))
        pts = [[hx, hz]] + [[hx + r * (a[0] * math.cos(t) + n[0] * math.sin(t)), hz + r * (a[1] * math.cos(t) + n[1] * math.sin(t))]
                            for t in [amax * i / 24 for i in range(25)]]
        out.append({"id": o["id"], "points": pts, "source": o["motion"]["source"]})
    return out


@app.get("/api/session/{sid}/events")
def events(sid: str, since: int = 0):
    s = S(sid)
    return [e.model_dump() for e in s.events if e.seq > since]


# ---------- room ----------
async def _read(f: UploadFile) -> bytes:
    data = await f.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file too large")
    return data


@app.post("/api/session/{sid}/photo")
async def upload_photo(sid: str, file: UploadFile = File(...)):
    s = S(sid)
    img, mime = await _read(file), file.content_type or "image/jpeg"
    g = gates.room_photo(img, mime)
    s.gate_log.append(g.model_dump())
    s.emit("gate.room_photo", "ok" if g.passed else "warn", g.message or "Photo accepted", answers=g.answers,
           latency_ms=g.latency_ms)
    if not g.passed and not g.degraded:
        return {"accepted": False, "gate": g.model_dump()}
    s.emit("roomspec", "started", "Gemini 4 Neon is reading the room")
    try:
        spec, meta = vision.photo_to_roomspec(img, mime)
    except (gemini.GeminiError, vision.RoomSpecError) as e:
        s.emit("roomspec", "error", f"Could not build a room from this photo: {e}")
        raise HTTPException(422, "Could not build a room from this photo. Try another angle or enter dimensions.")
    s.set_room(spec)
    if not s.scene:
        s.commit_scene(SceneRevision(rev=0, room_revision=spec.revision,
                                     wall_colors={w.id: w.color_hex for w in spec.walls}))
    s.emit("roomspec", "ok", f"Room {spec.width_m.value_m:.2f} x {spec.depth_m.value_m:.2f} m, "
           f"{len(spec.openings)} openings, {len(spec.existing_items)} items", latency_ms=meta["latency_ms"],
           notes=spec.notes)
    return {"accepted": True, "gate": g.model_dump(), "room": spec.model_dump()}


@app.post("/api/session/{sid}/door")
async def upload_door(sid: str, file: UploadFile = File(...), opening_id: Optional[str] = Form(None)):
    s = S(sid)
    if not s.room:
        raise HTTPException(400, "Add a room photo first")
    data = await _read(file)
    part = gemini.video_part(data, file.content_type or "video/mp4", agentic=True)
    g = gates.door_clip(part)
    s.emit("gate.door_clip", "ok" if g.passed else "warn", g.message or "Clip shows the door moving",
           answers=g.answers, latency_ms=g.latency_ms)
    if not g.passed and not g.degraded:
        return {"accepted": False, "gate": g.model_dump()}
    s.emit("door_motion", "started", "Neon is scrubbing the clip for hinge and swing")
    try:
        door, meta = vision.clip_to_door_motion(part, workflow.room_summary(s))
    except gemini.GeminiError as e:
        s.emit("door_motion", "error", str(e)[:200])
        raise HTTPException(422, "Could not analyse the clip. Tap the hinge side instead.")
    spec = s.room.model_copy(deep=True)
    spec, problem = vision.apply_door(spec, door, opening_id)
    if problem:
        s.emit("door_motion", "needs_input", problem, draft=door.model_dump())
        return {"accepted": False, "problem": problem, "draft": door.model_dump()}
    s.set_room(spec)
    _recheck(s, "door motion added")
    s.emit("door_motion", "ok", f"Hinge {door.hinge_side}, swings {door.swing}, up to {door.max_angle_deg:.0f} deg "
           f"(clearest at {door.evidence_ts})", draft=door.model_dump(), latency_ms=meta["latency_ms"])
    return {"accepted": True, "door": door.model_dump(), "room": spec.model_dump()}


class DoorManual(BaseModel):
    opening_id: str
    hinge_side: Literal["left", "right"]
    swing: Literal["in", "out"]


@app.post("/api/session/{sid}/door/manual")
def door_manual(sid: str, body: DoorManual):
    s = S(sid)
    spec = s.room.model_copy(deep=True)
    o = next((o for o in spec.openings if o.id == body.opening_id), None)
    if not o:
        raise HTTPException(404, "no such opening")
    o.motion = DoorMotion(hinge_side=body.hinge_side, swing=body.swing, source="confirmed", confidence=1.0)
    spec.revision += 1
    s.set_room(spec)
    _recheck(s, "door corrected by shopper")
    return {"room": spec.model_dump()}


class Measure(BaseModel):
    field: Literal["width_m", "depth_m", "height_m"] = "width_m"
    value_m: float


@app.post("/api/session/{sid}/measure")
def measure(sid: str, body: Measure):
    s = S(sid)
    if not s.room:
        raise HTTPException(400, "no room yet")
    try:
        spec = vision.anchor_measurement(s.room, body.field, body.value_m)
    except vision.RoomSpecError as e:
        raise HTTPException(422, str(e))
    s.set_room(spec)
    s.emit("measurement", "ok", f"{body.field} confirmed at {body.value_m} m; estimated lengths rescaled")
    _recheck(s, "measurement anchored")
    if s.pending_input and s.pending_input["request"]["kind"] == "measurement":
        _bg(s, workflow.resume, s)
    return {"room": spec.model_dump()}


def _recheck(s: state.Session, why: str):
    if s.scene and s.scene.placements and s.room:
        rep = solver.check(s.room.model_dump(), [p.model_dump() for p in s.scene.placements], s.scene.rev)
        from contracts.schemas import FitReport
        s.fit = FitReport(**rep)
        s.emit("server.check", "ok" if rep["status"] in ("fits", "tight") else "warn",
               f"Re-checked after {why}: {rep['status']}", report=rep)


# ---------- commands ----------
@app.post("/api/session/{sid}/command")
async def command(sid: str, text: Optional[str] = Form(None), audio: Optional[UploadFile] = File(None)):
    s = S(sid)
    if not text and not audio:
        raise HTTPException(400, "say or type something")
    audio_bytes = await _read(audio) if audio else None
    ctx = workflow.room_summary(s)
    try:
        parsed = intake.parse(text=text, audio=audio_bytes, audio_mime=(audio.content_type if audio else "audio/webm"),
                              context=ctx)
    except gemini.GeminiError:
        s.emit("intake", "error", "Taking longer than usual. Please try again or type it.")
        raise HTTPException(503, "model unavailable")
    cmd = intake.Command(**parsed["command"])
    s.emit("intake", "ok", f"“{cmd.transcript}” -> {cmd.intent}", command=parsed["command"],
           route=parsed["route"]["answers"], agree=parsed["agree"], latency_ms=parsed["latency_ms"])
    if parsed["clarify"]:
        s.emit("intake", "needs_input", parsed["clarify"])
        return {"status": "clarify", "message": parsed["clarify"], "parsed": parsed}

    if any(v is not None for v in cmd.brief_patch.model_dump().values()):
        s.brief, changed = intake.apply_brief_patch(s.brief, cmd.brief_patch, s.room.existing_items if s.room else [])
        if changed:
            s.emit("brief", "ok", f"Updated {', '.join(changed)}", brief=s.brief.model_dump())
            if s.approval:
                s.approval = None
                s.emit("approval", "warn", "Brief changed; previous cart approval voided")

    if cmd.intent == "measurement_correction" and cmd.measurement_m:
        return measure(sid, Measure(field=cmd.measurement_field or "width_m", value_m=cmd.measurement_m))
    if cmd.intent == "cart_review":
        return {"status": "review", "review": _review(s)}
    if cmd.intent == "move_furniture":
        return _voice_move(s, cmd)
    if not s.room:
        raise HTTPException(400, "Add a room photo first")

    # fast paths: one validated op, no planner round-trip
    if cmd.intent == "style_edit":
        steps = [{"id": "style", "op": "apply_style_edit",
                  "args": {k: v for k, v in dict(wall_id=cmd.wall_id, color_hex=cmd.color_hex, lighting=cmd.lighting).items() if v}}]
        plan = workflow.validate_plan(s, workflow.WorkflowPlan(goal="style_edit", steps=[workflow.PlanStep(**x) for x in steps]))
        _bg(s, workflow.execute, s, plan)
        return {"status": "running", "plan": plan.model_dump(), "planner": "fast-path"}
    if cmd.intent == "product_search" and cmd.swap_category and cmd.finish and s.scene and s.scene.placements:
        plan = workflow.validate_plan(s, workflow.WorkflowPlan(goal="swap_finish", steps=[
            workflow.PlanStep(id="swap", op="swap_finish", args={"category": cmd.swap_category, "finish": cmd.finish})]))
        _bg(s, workflow.execute, s, plan)
        return {"status": "running", "plan": plan.model_dump(), "planner": "fast-path"}

    try:
        plan, meta = workflow.propose_plan(s, f"{cmd.transcript}\nParsed: {cmd.model_dump_json()}", cmd.intent)
    except workflow.PlanError as e:
        s.emit("planner", "error", f"Plan rejected by validator: {e}")
        plan, meta = workflow.validate_plan(s, workflow.template_plan(cmd.intent)), {"source": "template"}
    s.emit("planner", "ok" if meta.get("source") == "neon" else "warn",
           f"{'Neon' if meta.get('source') == 'neon' else 'Template (Neon unavailable)'} plan: "
           + " -> ".join(st.op for st in plan.steps), plan=plan.model_dump(), rationale=meta.get("rationale"))
    _bg(s, workflow.execute, s, plan)
    return {"status": "running", "plan": plan.model_dump(), "planner": meta.get("source")}


def _voice_move(s: state.Session, cmd: intake.Command):
    if not (s.scene and s.scene.placements and s.room):
        return {"status": "clarify", "message": "There is nothing to move yet."}
    target = (cmd.move_target or "").lower()
    cands = [p for p in s.scene.placements if not p.locked and (p.category in target or target in p.category)]
    if len(cands) != 1:
        msg = "Which item should I move? Tap it in the room." if cands or not target else f"I can't find '{target}' in the design."
        s.emit("move", "needs_input", msg, candidates=[p.key for p in cands])
        return {"status": "clarify", "message": msg, "candidates": [p.key for p in cands]}
    p = cands[0]
    dest = (cmd.move_destination or "").lower()
    pose = _resolve_destination(s, p, dest)
    if not pose:
        s.emit("move", "needs_input", f"Where exactly? Drag the {p.category} or name a wall, window or door.")
        return {"status": "clarify", "message": "Where should it go? Drag it, or name a wall or the window."}
    return move(s.id, MoveBody(key=p.key, x=pose[0], z=pose[1], rot=pose[2]))


def _resolve_destination(s, p, dest: str):
    room = s.room.model_dump()
    target_open = None
    for kind in ("window", "door"):
        if kind in dest:
            target_open = next((o for o in s.room.openings if o.type == kind), None)
    wall = next((w for w in ("back", "right", "front", "left") if w in dest), None)
    if target_open:
        wall = target_open.wall
        along = target_open.offset_m + target_open.width_m / 2
    elif wall:
        along = (s.room.width_m.value_m if wall in ("back", "front") else s.room.depth_m.value_m) / 2
    else:
        return None
    x, z, rot = solver._against_wall(room, wall, along, p.w, p.d)
    return x, z, rot


class MoveBody(BaseModel):
    key: str
    x: float
    z: float
    rot: Literal[0, 90, 180, 270] = 0


@app.post("/api/session/{sid}/move")
def move(sid: str, body: MoveBody):
    s = S(sid)
    if not (s.scene and s.room):
        raise HTTPException(400, "nothing to move")
    if not any(p.key == body.key for p in s.scene.placements):
        raise HTTPException(404, "unknown item")
    if next(p for p in s.scene.placements if p.key == body.key).locked:
        raise HTTPException(409, "That item is locked (you're keeping it)")
    base = s.scene.rev
    placements = [p.model_dump() for p in s.scene.placements]

    def run():
        res = agents.review_move(s.room.model_dump(), placements, body.key, body.x, body.z, body.rot,
                                 s.brief.model_dump(), s.emit)
        if res["accepted"]:
            _commit(s, res["placements"], base, "move accepted")
        else:
            s.last_alternative = {"base_rev": base, "requested": res["placements"], "conflicts": res["conflicts"],
                                  "alternative": res["alternative"]["placements"], "report": res["alternative"]["report"],
                                  "style": res["alternative"].get("style_assessment")}
            s.emit("move", "needs_input", f"That spot conflicts: {res['conflicts'][0]['detail']}. "
                   f"A checked alternative is ready ({res['alternative']['report']['status']}).")
    _bg(s, run)
    return {"status": "running"}


@app.post("/api/session/{sid}/alternative/{decision}")
def alternative(sid: str, decision: Literal["accept", "reject"]):
    s = S(sid)
    alt = s.last_alternative
    if not alt:
        raise HTTPException(404, "no alternative pending")
    s.last_alternative = None
    if decision == "accept":
        _commit(s, alt["alternative"], alt["base_rev"], "alternative accepted")
    else:
        s.emit("move", "info", "Kept the previous layout")
    return get_state(sid)


def _commit(s: state.Session, placements: list[dict], base: int, why: str):
    from contracts.schemas import FitReport, Placement
    sc = s.scene.model_copy(deep=True)
    sc.placements = [Placement(**{k: v for k, v in p.items() if k in Placement.model_fields and v is not None})
                     for p in placements if p.get("w") is not None]
    try:
        c = s.commit_scene(sc, base_rev=base)
    except state.StaleRevision as e:
        s.emit("scene", "warn", f"Discarded a late result: {e}")
        return
    rep = solver.check(s.room.model_dump(), [p.model_dump() for p in c.placements], c.rev)
    s.fit = FitReport(**rep)
    s.emit("scene", "ok", f"Revision {c.rev} committed ({why}); fit: {rep['status']}", rev=c.rev, fit=rep)


@app.post("/api/session/{sid}/undo")
def undo(sid: str):
    s = S(sid)
    if len(s.scenes) < 2:
        raise HTTPException(400, "nothing to undo")
    prev = s.scenes[-2].model_copy(deep=True)
    _commit(s, [p.model_dump() for p in prev.placements], s.scene.rev, f"undo to rev {s.scenes[-2].rev}")
    s.scene.wall_colors, s.scene.lighting = prev.wall_colors, prev.lighting
    return get_state(sid)


# ---------- commerce ----------
def _review(s):
    try:
        s.last_review = checkout.build_review(s, s.extra_variant_ids)
    except (checkout.CheckoutError, shop.ShopError) as e:
        raise HTTPException(409, str(e))
    return s.last_review


@app.post("/api/session/{sid}/review")
def review(sid: str):
    return _review(S(sid))


@app.post("/api/session/{sid}/approve")
def approve(sid: str):
    """Only the 'Confirm and add to cart' button calls this."""
    s = S(sid)
    if not s.last_review:
        raise HTTPException(400, "review first")
    try:
        tok = checkout.approve(s, s.last_review)
        return checkout.create_cart(s, tok["approval_token"])
    except (checkout.CheckoutError, shop.ShopError) as e:
        s.emit("cart", "error", str(e))
        raise HTTPException(409, str(e))


@app.post("/api/session/{sid}/checkout")
def do_checkout(sid: str):
    s = S(sid)
    try:
        return checkout.audit_and_release(s)
    except checkout.CheckoutError as e:
        raise HTTPException(409, str(e))
