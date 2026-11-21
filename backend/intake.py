"""Voice / text intake with Gemini 4 Hydrogen, cross-checked by Decision 1.

Hydrogen turns the utterance (raw audio or text) into a typed Command. Decision 1
independently routes the transcript. If they disagree, or either is unsure, we ask
a short clarifying question instead of acting.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from contracts.schemas import DesignBrief

from . import gates, gemini
from .models import HYDROGEN

Intent = Literal["product_search", "style_edit", "move_furniture", "measurement_correction", "cart_review", "other"]


class BriefPatch(BaseModel):
    budget_usd: Optional[float] = None
    style: Optional[list[str]] = None
    categories: Optional[list[Literal["desk", "chair", "monitor", "lighting", "storage"]]] = None
    keep_door_clear: Optional[bool] = None
    preferred_wall: Optional[Literal["back", "right", "front", "left"]] = None
    keep_existing_labels: Optional[list[str]] = Field(None, description="labels of existing items the user wants to keep")


class Command(BaseModel):
    transcript: str
    intent: Intent
    brief_patch: BriefPatch
    wall_id: Optional[Literal["back", "right", "front", "left", "all"]] = None
    color_hex: Optional[str] = Field(None, description="#rrggbb for a named color, e.g. olive -> #708238")
    lighting: Optional[Literal["daylight", "evening", "rgb"]] = None
    swap_category: Optional[Literal["desk", "chair", "monitor", "lighting", "storage"]] = None
    finish: Optional[str] = Field(None, description="requested finish/color of a product, e.g. walnut")
    move_target: Optional[str] = Field(None, description="which item to move, as the user named it")
    move_destination: Optional[str] = Field(None, description="where to, as the user said it")
    measurement_field: Optional[Literal["width_m", "depth_m", "height_m"]] = None
    measurement_m: Optional[float] = None
    ambiguities: list[str] = Field(description="anything you had to guess; empty if none")


SYSTEM = """You turn a shopper's spoken or typed request about designing their room into one typed command.
Transcribe audio verbatim into `transcript`. Only fill fields the shopper actually said. Convert spoken
units to metres and named colors to hex. Never invent a budget, product or measurement.
'Make that wall olive' -> style_edit, color_hex. 'Show me the walnut desk' -> product_search with swap_category=desk,
finish=walnut. 'The desk wall is 3.2 metres' -> measurement_correction, width_m=3.2 (the desk wall is the back wall).
If the request mixes intents, pick the one that changes products/money and list the rest in ambiguities."""


def parse(text: Optional[str] = None, audio: Optional[bytes] = None, audio_mime: str = "audio/webm",
          context: str = "") -> dict:
    parts = []
    if audio:
        parts.append(gemini.audio_part(audio, audio_mime))
    parts.append(gemini.text_part(f"Context: {context}\n" + (f"Shopper typed: {text}" if text else "Shopper spoke (audio above).")))
    cmd, meta = gemini.generate_structured(HYDROGEN, parts, Command, system=SYSTEM, thinking="low")
    route = gates.route(cmd.transcript, context)
    agree = route.degraded or route.action == cmd.intent
    clarify = None
    if not route.passed and not route.degraded:
        clarify = route.message
    elif not agree:
        clarify = (f"Just to check: did you want to {cmd.intent.replace('_', ' ')} "
                   f"or {route.action.replace('_', ' ')}?")
    return {"command": cmd.model_dump(), "route": route.model_dump(), "agree": agree,
            "clarify": clarify, "latency_ms": meta["latency_ms"] + (route.latency_ms or 0)}


def apply_brief_patch(brief: DesignBrief, patch: BriefPatch, existing_items) -> tuple[DesignBrief, list[str]]:
    """Preserve unchanged fields; return the changed field names."""
    b = brief.model_copy(deep=True)
    changed = []
    if patch.budget_usd is not None and patch.budget_usd > 0:
        b.budget_minor = int(round(patch.budget_usd * 100)); changed.append("budget")
    if patch.style:
        b.style = patch.style; changed.append("style")
    if patch.categories:
        b.categories = list(dict.fromkeys(patch.categories)); changed.append("categories")
    if patch.keep_door_clear is not None:
        b.keep_door_clear = patch.keep_door_clear; changed.append("keep_door_clear")
    if patch.preferred_wall:
        b.preferred_wall = patch.preferred_wall; changed.append("preferred_wall")
    if patch.keep_existing_labels:
        want = [l.lower() for l in patch.keep_existing_labels]
        b.locked_item_ids = [i.id for i in existing_items if any(w in i.label.lower() for w in want)]
        changed.append("locked_items")
    return b, changed
