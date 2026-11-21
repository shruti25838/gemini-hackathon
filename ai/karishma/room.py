"""analyze_room / validate_room_spec (team contract, Person A)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ValidationError

from ._vendor.kschemas import RoomSpec

from . import gates, gemini, vision


class RoomAnalysis(BaseModel):
    accepted: bool
    gate: gates.GateResult
    room_spec: Optional[RoomSpec] = None
    message: str = ""
    latency_ms: Optional[int] = None


class ValidationResult(BaseModel):
    ok: bool
    errors: list[str] = []
    room_spec: Optional[RoomSpec] = None


def analyze_room(image: bytes, session_id: str = "", mime: str = "image/jpeg") -> RoomAnalysis:
    """Decision 1 photo gate, then Neon RoomSpec draft, validated. Never raises for bad input."""
    g = gates.room_photo(image, mime)
    if not g.passed and not g.degraded:
        return RoomAnalysis(accepted=False, gate=g, message=g.message)
    try:
        spec, meta = vision.photo_to_roomspec(image, mime)
    except (gemini.GeminiError, vision.RoomSpecError) as e:
        return RoomAnalysis(accepted=False, gate=g,
                            message=f"Could not build a room from this photo ({type(e).__name__}). "
                                    "Try another angle, enter dimensions, or use the preset room.")
    for gap in g.coverage_gaps:
        spec.notes.append(f"Uncertain {gap['affects']}: {gap['rescan']}")
    return RoomAnalysis(accepted=True, gate=g, room_spec=spec, message=g.message, latency_ms=meta["latency_ms"])


def validate_room_spec(draft: dict | RoomSpec) -> ValidationResult:
    """C calls this before every room commit. Field-level errors, never partial state."""
    try:
        spec = draft if isinstance(draft, RoomSpec) else RoomSpec.model_validate(draft)
    except ValidationError as e:
        return ValidationResult(ok=False, errors=[f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()])
    try:
        spec = vision.validate_room(spec.model_copy(deep=True))
    except vision.RoomSpecError as e:
        return ValidationResult(ok=False, errors=[str(e)])
    dropped = [n for n in spec.notes if n.startswith("Dropped")]
    return ValidationResult(ok=True, errors=dropped, room_spec=spec)


class DoorAnalysis(BaseModel):
    accepted: bool
    gate: gates.GateResult
    draft: Optional[dict] = None        # Neon's reading (vision.DoorDraft)
    hinge_side: Optional[str] = None    # only set when Neon and Decision 1 agree confidently
    swing: Optional[str] = None
    agree: bool = False
    needs_confirm: bool = False
    message: str = ""
    latency_ms: Optional[int] = None


HINGE_CONF = 0.70   # Decision 1 choice confidence needed to trust its hinge answer
NEON_CONF = 0.70


def analyze_door(video_part: dict, room_hint: str = "") -> DoorAnalysis:
    """Decision 1 coverage + independent hinge/swing check, Neon agentic video reading, then agreement.
    Filmed from inside facing the door, frame-left == hinge_side 'left' in the RoomSpec convention."""
    import time as _t
    t0 = _t.time()
    g = gates.door_clip(video_part)
    if not g.passed and not g.degraded:
        return DoorAnalysis(accepted=False, gate=g, message=g.message)
    try:
        d, _ = vision.clip_to_door_motion(video_part, room_hint)
    except gemini.GeminiError as e:
        return DoorAnalysis(accepted=False, gate=g, needs_confirm=True,
                            message=f"Couldn't read the clip ({type(e).__name__}). Tap which side the hinge is on.")
    a = g.answers or {}
    dec_hinge = (a.get("hinge_edge") or {}).get("choice")
    dec_conf = (a.get("hinge_edge") or {}).get("confidence", 0)
    toward = (a.get("opens_toward_camera") or {}).get("noul")
    dec_swing = None if toward is None else ("in" if toward >= 0.6 else "out" if toward <= 0.4 else None)
    hinge_ok = (d.hinge_side in ("left", "right") and dec_hinge == d.hinge_side
                and dec_conf >= HINGE_CONF and d.confidence >= NEON_CONF)
    swing_ok = d.swing in ("in", "out") and (dec_swing is None or dec_swing == d.swing)
    out = DoorAnalysis(accepted=True, gate=g, draft=d.model_dump(), agree=hinge_ok and swing_ok,
                       latency_ms=int((_t.time() - t0) * 1000))
    if out.agree:
        out.hinge_side, out.swing = d.hinge_side, d.swing
        return out
    out.needs_confirm = True
    why = []
    if not hinge_ok:
        if dec_hinge == d.hinge_side and d.hinge_side in ("left", "right"):
            why.append(f"both readings lean hinge-{d.hinge_side}, but not confidently "
                       f"(video model {d.confidence:.2f}, independent check {dec_conf:.2f})")
        else:
            why.append(f"the video model says hinge-{d.hinge_side}, the independent check says "
                       f"{dec_hinge if dec_hinge in ('left', 'right') else 'it cannot tell'}")
    if not swing_ok:
        why.append(f"swing: the video model says {d.swing}, the independent check "
                   + (f"says {dec_swing}" if dec_swing else "cannot tell"))
    out.swing = d.swing if swing_ok else None
    out.message = "I'm not sure about the door: " + "; ".join(why) + ". Tap which side the hinge is on."
    return out


# ---------- fast door path (live: Decision 1 = 1.6-1.9 s vs Neon agentic video = 150+ s) ----------
class FastDoor(BaseModel):
    accepted: bool
    gate: gates.GateResult
    hinge_side: Optional[str] = None   # from Decision 1 when confident
    swing: Optional[str] = None        # from Decision 1 when clear, else filled later by Neon or the shopper
    message: str = ""


FAST_HINGE_CONF = 0.70


def fast_door(video_part: dict) -> FastDoor:
    """Decision 1 only: coverage + hinge edge + swing, in ~2 s."""
    g = gates.door_clip(video_part)
    if not g.passed and not g.degraded:
        return FastDoor(accepted=False, gate=g, message=g.message)
    a = g.answers or {}
    he = a.get("hinge_edge") or {}
    hinge = he.get("choice") if he.get("choice") in ("left", "right") and he.get("confidence", 0) >= FAST_HINGE_CONF else None
    toward = (a.get("opens_toward_camera") or {}).get("noul")
    swing = None if toward is None else ("in" if toward >= 0.65 else "out" if toward <= 0.35 else None)
    if hinge and swing:
        msg = f"Hinge {hinge}, opens {swing} (fast check). Neon is double-checking in the background."
    elif hinge:
        msg = f"Hinge {hinge} (fast check). Checking the swing direction in the background, or tap opens in / out."
    else:
        msg = "Couldn't see the hinge clearly. Tap hinge L or R; Neon is also checking in the background."
    return FastDoor(accepted=True, gate=g, hinge_side=hinge, swing=swing, message=msg)


def verify_door(video_part: dict, room_hint: str = "") -> Optional[dict]:
    """Background Neon reading (frame sampling, low thinking). Returns DoorDraft dict or None."""
    try:
        d, _ = gemini.generate_structured(
            vision.NEON, [video_part, gemini.text_part(f"Room context: {room_hint}\nAnalyse the door.")],
            vision.DoorDraft, system=vision.DOOR_SYSTEM, thinking="low", retries=0)
        return d.model_dump()
    except Exception:
        return None
