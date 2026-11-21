"""Ethyneon API. Run: uvicorn backend.app:app --reload --port 8000"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from contracts.schemas import DoorMotion, SceneRevision  # noqa: E402

from ai import agents, commands, gates, gemini, vision, workflow
from ai import room as ai_room
from fit import solver

from . import checkout, shop, state  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
app = FastAPI(title="Ethyneon")
FRONT = Path(__file__).resolve().parent.parent / "frontend"
MAX_UPLOAD = 40 * 1024 * 1024
DOOR_AGENTIC = os.getenv("DOOR_VIDEO_PROCESSING", "agentic") == "agentic"


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
    return {"gemini": gemini.configured(), "mock_ai": gemini.mock_ai(), "shopify_live": shop.live(),
            "voice_mode": os.getenv("ETHYNEON_VOICE_MODE", "hydrogen"),
            "fixture_catalog": shop.fixture_mode()}


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


PRESET_ROOM = {
    "room_revision": 1, "width_m": {"value_m": 3.2, "source": "default", "confidence": 1.0},
    "depth_m": {"value_m": 3.0, "source": "default", "confidence": 1.0},
    "height_m": {"value_m": 2.5, "source": "default", "confidence": 1.0},
    "walls": [{"id": w, "color_hex": "#d9d4cc"} for w in ("wall_n", "wall_e", "wall_s", "wall_w")],
    "openings": [{"id": "door1", "wall": "wall_n", "type": "door", "offset_m": 2.2, "width_m": 0.8, "confidence": 1.0},
                 {"id": "window1", "wall": "wall_w", "type": "window", "offset_m": 0.9, "width_m": 1.2, "height_m": 1.2,
                  "sill_m": 0.9, "confidence": 1.0}],
    "existing_items": [], "status": "preset", "notes": ["PRESET ROOM: not from your photo. Edit the dimensions to match your room."],
}


@app.post("/api/session/{sid}/preset")
def use_preset(sid: str):
    """Labeled fallback when the photo path fails. Never presented as the shopper's room."""
    from contracts.schemas import RoomSpec
    s = S(sid)
    spec = RoomSpec(**PRESET_ROOM)
    s.set_room(spec)
    if not s.scene:
        s.commit_scene(SceneRevision(rev=0, room_revision=spec.room_revision))
    s.emit("roomspec", "warn", "Using the labeled PRESET room (not from a photo). Enter your real wall length.")
    return {"room": spec.model_dump()}


@app.post("/api/session/{sid}/photo")
async def upload_photo(sid: str, file: UploadFile = File(...)):
    s = S(sid)
    img, mime = await _read(file), file.content_type or "image/jpeg"
    s.emit("roomspec", "started", "Decision 1 photo gate, then Gemini 4 Neon reads the room")
    a = await run_in_threadpool(ai_room.analyze_room, img, sid, mime)
    s.gate_log.append(a.gate.model_dump())
    s.emit("gate.room_photo", "ok" if a.gate.passed else "warn", a.gate.message or "Photo accepted",
           answers=a.gate.answers, latency_ms=a.gate.latency_ms)
    if not a.accepted:
        s.emit("roomspec", "warn" if a.room_spec is None and not a.gate.passed else "error", a.message)
        if a.gate.passed or a.gate.degraded:
            raise HTTPException(422, a.message)
        return {"accepted": False, "gate": a.gate.model_dump()}
    v = ai_room.validate_room_spec(a.room_spec)  # C validates before commit
    if not v.ok:
        s.emit("roomspec", "error", "Room draft failed validation: " + "; ".join(v.errors))
        raise HTTPException(422, "The room draft was invalid. Try another photo or use the preset room.")
    spec = v.room_spec
    s.set_room(spec)
    if not s.scene:
        s.commit_scene(SceneRevision(rev=0, room_revision=spec.room_revision,
                                     wall_colors={w.id: w.color_hex for w in spec.walls}))
    s.emit("roomspec", "ok", f"Room {spec.width_m.value_m:.2f} x {spec.depth_m.value_m:.2f} m, "
           f"{len(spec.openings)} openings, {len(spec.existing_items)} items", latency_ms=a.latency_ms,
           notes=spec.notes)
    return {"accepted": True, "gate": a.gate.model_dump(), "room": spec.model_dump()}


@app.post("/api/session/{sid}/door")
async def upload_door(sid: str, file: UploadFile = File(...), opening_id: Optional[str] = Form(None)):
    s = S(sid)
    if not s.room:
        raise HTTPException(400, "Add a room photo first")
    if not any(o.type == "door" for o in s.room.openings):
        msg = ("Your room model has no door, so there is nothing to attach the clip to. "
               "Upload a room photo that shows the door, then add the clip.")
        s.emit("door_motion", "needs_input", msg)
        return {"accepted": False, "problem": msg}
    data = await _read(file)
    part = await run_in_threadpool(gemini.video_part, data, file.content_type or "video/mp4", DOOR_AGENTIC)
    s.emit("door_motion", "started", "Decision 1 checks the clip; Neon scrubs it for hinge and swing")
    d = await run_in_threadpool(ai_room.analyze_door, part, workflow.room_summary(s))
    g = d.gate
    s.emit("gate.door_clip", "ok" if g.passed else "warn", g.message or "Clip shows the door moving",
           answers=g.answers, latency_ms=g.latency_ms)
    if not d.accepted:
        s.emit("door_motion", "needs_input" if d.needs_confirm else "warn", d.message)
        return {"accepted": False, "gate": g.model_dump(), "problem": d.message}
    if d.needs_confirm:  # the two readings disagree: record nothing, ask for one tap
        s.emit("door_motion", "needs_input", d.message, draft=d.draft, latency_ms=d.latency_ms)
        return {"accepted": False, "needs_confirm": True, "problem": d.message, "draft": d.draft}
    draft = vision.DoorDraft(**d.draft)
    spec, problem = vision.apply_door(s.room.model_copy(deep=True), draft, opening_id)
    if problem:
        s.emit("door_motion", "needs_input", problem, draft=d.draft)
        return {"accepted": False, "problem": problem, "draft": d.draft}
    s.set_room(spec)
    _recheck(s, "door motion added")
    s.emit("door_motion", "ok", f"Hinge {draft.hinge_side}, swings {draft.swing}, up to {draft.max_angle_deg:.0f} deg "
           f"(clearest at {draft.evidence_ts}); video model and independent check agree", draft=d.draft,
           latency_ms=d.latency_ms)
    return {"accepted": True, "door": d.draft, "room": spec.model_dump()}


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
    spec.room_revision += 1
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
async def command(sid: str, text: Optional[str] = Form(None), audio: Optional[UploadFile] = File(None),
                  selected_key: Optional[str] = Form(None), frame: Optional[UploadFile] = File(None)):
    """B -> C route. A's handle_command interprets; this server applies the result."""
    s = S(sid)
    if not text and not audio:
        raise HTTPException(400, "say or type something")
    audio_bytes = await _read(audio) if audio else None
    frame_t = ((await _read(frame)), frame.content_type or "image/jpeg") if frame else None
    try:
        res = await run_in_threadpool(lambda: commands.handle_command(
            s, text=text, audio=audio_bytes, audio_mime=(audio.content_type if audio else "audio/webm"),
            selected_key=selected_key or None, frame=frame_t))
    except gemini.GeminiError:
        s.emit("intake", "error", "Taking longer than usual. Please try again or type it.")
        raise HTTPException(503, "model unavailable")
    if res.brief is not None and res.brief_changed:
        s.brief = res.brief
        s.emit("brief", "ok", f"Updated {', '.join(res.brief_changed)}", brief=s.brief.model_dump())
        if s.approval:
            s.approval = None
            s.emit("approval", "warn", "Brief changed; previous cart approval voided")
    out = {"status": res.status, "action": res.action, "message": res.message, "intent": res.intent,
           "transcript": res.transcript, "say": res.message if res.status == "needs_input" else res.spoken_reply}
    if res.status == "needs_input":
        s.emit("command", "needs_input", res.message, candidates=res.payload.get("candidates", []))
        return {**out, "status": "clarify", **res.payload}
    if res.action == "measurement":
        return measure(sid, Measure(**res.payload))
    if res.action == "cart_review":
        return {**out, "status": "review", "review": _review(s)}
    if res.action == "move":
        return move(sid, MoveBody(**res.payload))
    if res.action == "run_plan":
        _bg(s, workflow.execute, s, res.plan)
        return {**out, "status": "running", "plan": res.plan.model_dump(), "planner": res.plan.source}
    if res.action == "plan_and_run":
        brief = s.brief
        _bg(s, lambda: workflow.execute(s, commands.plan_workflow(s, res.payload["request"], res.payload["route"], brief)))
        return {**out, "status": "running", "planner": "neon (planning in background)"}
    return out


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
            s.last_alternative = {"base_rev": base, "title": "That spot conflicts", "requested": res["placements"], "conflicts": res["conflicts"],
                                  "alternative": res["alternative"]["placements"], "report": res["alternative"]["report"],
                                  "style": res["alternative"].get("style_assessment"),
                                  "findings": res["alternative"].get("findings", [])}
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
