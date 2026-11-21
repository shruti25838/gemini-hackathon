"""analyze_room / validate_room_spec (team contract, Person A)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ValidationError

from contracts.schemas import RoomSpec

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
        why.append(f"hinge: video model says {d.hinge_side}, independent check says {dec_hinge or 'unclear'} "
                   f"({dec_conf:.2f})")
    if not swing_ok:
        why.append(f"swing: video model says {d.swing}, independent check says {dec_swing or 'unclear'}")
    out.swing = d.swing if swing_ok else None
    out.message = "I'm not sure about the door (" + "; ".join(why) + "). Tap which side the hinge is on."
    return out
