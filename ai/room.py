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
