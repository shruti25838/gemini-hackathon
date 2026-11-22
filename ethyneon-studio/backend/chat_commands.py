"""Quick room-chat commands answered without the planner: budget, suggestions, and painting lighting.

Moves and additions ("move the desk under the window", "add a bookcase") still go to the Argon planner; this
module only adds the shopper's budget to those requests. Painting advice uses the same window geometry as
backend/room_lighting.py: lower relative exposure means less direct sun, glare, and fading on the artwork.
"""

from math import dist
import re
from typing import Any

from backend.room_lighting import scene_geometry


BUDGET = re.compile(
    r"\b(?:budget|under|below|max(?:imum)?|spend|up to|within)\b[^\d$]{0,20}\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|thousand)?", re.I)
SUGGEST = re.compile(r"\b(?:suggest(?:ions?)?|ideas?|recommend(?:ations?)?|what (?:should|do|would) (?:i|you))\b", re.I)
PAINTING = re.compile(r"\b(?:paint(?:ing)?s?|artwork|wall art|art ?print|canvas|poster|sunlight|glare|lighting)\b", re.I)
ART = re.compile(r"\b(?:art|painting|print|canvas|poster|frame)\b", re.I)
# Inward normal of each wall in the room frame (origin at the wall_n / wall_w corner).
NORMALS = {"wall_n": (0.0, 1.0), "wall_s": (0.0, -1.0), "wall_w": (1.0, 0.0), "wall_e": (-1.0, 0.0)}
WALL_NAMES = {"wall_n": "north", "wall_e": "east", "wall_s": "south", "wall_w": "west"}
MOVE_FACTOR = 2.0
ROOM_IDEAS = {
    "bedroom": "a bed, two nightstands, a dresser, and a reading lamp",
    "living_room": "a sofa, a coffee table, a reading chair, and a media console",
    "office": "a desk, a task chair, a bookcase, and a desk lamp",
    "gaming_room": "a gaming desk, an adjustable chair, a monitor, storage for controllers, and soft lighting",
    "kitchen": "a movable island cart, counter stools, and a freestanding pantry",
    "dining_room": "a dining table, chairs, and a sideboard",
    "library": "a bookcase, a reading chair, a side table, and a floor lamp",
}


def money(minor: int) -> str:
    return f"${minor / 100:,.0f}" if minor % 100 == 0 else f"${minor / 100:,.2f}"


def parse_budget(text: str) -> int | None:
    match = BUDGET.search(text)
    if not match:
        return None
    amount = float(match[1].replace(",", "")) * (1000 if match[2] else 1)
    return int(round(amount * 100)) if 20 <= amount <= 100_000 else None


def _products(session) -> list[dict[str, Any]]:
    document = session.draft or session.published or {}
    return [*(document.get("placed_products") or []), *(document.get("proposed_products") or [])]


def _total(products: list[dict[str, Any]]) -> int:
    return sum((item.get("price") or {}).get("amount", 0) for item in products)


def with_budget(session, text: str) -> str:
    """The planner instruction, carrying the remembered budget.
    When the message itself sets a budget and the current total is over it, turn the
    instruction into an explicit replan so Argon swaps items rather than just noting the limit.
    """
    budget = getattr(session, "budget_minor", None)
    if not budget:
        return text[:700]
    total = _total(_products(session))
    # If the user's message contained a budget trigger and the plan is over, make it
    # an explicit replan instruction so Argon acts immediately without a second prompt.
    if parse_budget(text) is not None and total and total > budget:
        instruction = (
            f"The shopper wants the full room plan to cost under {money(budget)} total. "
            f"The current plan totals {money(total)}, which is {money(total - budget)} over budget. "
            "Replace or remove items to bring the total under the budget. "
            "Keep as many items as possible but swap expensive ones for cheaper alternatives. "
            "Return a complete revised layout within budget."
        )
        note = f"\nBudget: keep the total of all suggested Shopify items under {money(budget)}."
        return instruction[:700 - len(note)] + note
    note = f"\nBudget: keep the total of all suggested Shopify items under {money(budget)}."
    return text[:700 - len(note)] + note


def _lighting(session) -> dict[str, Any] | None:
    if session.room is None:
        return None
    room = session.room.model_dump(mode="json")
    scene_geometry(session.room, room)
    return room.get("lighting")


def _exposure(lighting: dict[str, Any], wall: str, point: dict[str, float]) -> float:
    """Direct-window light reaching a point on a wall, matching backend/room_lighting.py."""
    nx, nz = NORMALS[wall]
    strength = 0.0
    for window in lighting.get("windows", []):
        center = window["center"]
        dx, dz = point["x"] - center["x"], point["z"] - center["z"]
        distance = max(dist((point["x"], point["y"], point["z"]), (center["x"], center["y"], center["z"])), 0.1)
        wx, wz = NORMALS[window["wall"]]
        emit = max(0.0, (wx * dx + wz * dz) / distance)
        receive = max(0.0, (-nx * dx - nz * dz) / distance)
        strength += window["area_m2"] * emit * receive / distance ** 2
    return strength


def _nearest_wall(session, point: dict[str, float]) -> str:
    dims = session.room.dimensions
    width, depth = dims["width"].value_m, dims["depth"].value_m
    gaps = {"wall_n": point["z"], "wall_s": depth - point["z"], "wall_w": point["x"], "wall_e": width - point["x"]}
    return min(gaps, key=gaps.get)


def painting_advice(session) -> str:
    lighting = _lighting(session)
    if lighting is None:
        return "Share the room's measurements first, then I can check the light for paintings."
    if not lighting.get("windows"):
        return "I don't know where the windows are, so I can't judge the light on a painting yet. Add a photo that shows them."
    spots = lighting.get("painting_spots") or []
    if not spots:
        return "There is no clear wall space for a 60 cm painting at eye level right now."
    best = spots[0]
    where = f"the {WALL_NAMES[best['wall']]} wall at about {best['position']['x']:.1f}, {best['position']['z']:.1f} m"
    art = [item for item in _products(session) if ART.search(item.get("label", ""))]
    if not art:
        return (f"The best spot for a painting is {where}, at eye level: it gets the least direct window light, "
                "so less glare and fading. Want me to add wall art there?")
    lines = []
    for item in art:
        position = item["pose"]["position"]
        wall = _nearest_wall(session, position)
        here = _exposure(lighting, wall, position)
        if here > max(best["relative_exposure"] or 0, 0.001) * MOVE_FACTOR:
            lines.append(f"{item['label']} gets strong direct window light on the {WALL_NAMES[wall]} wall, which causes glare "
                         f"and fading. Move it to {where}, say \"move the painting to the {WALL_NAMES[best['wall']]} wall\".")
        else:
            lines.append(f"{item['label']} is in good light on the {WALL_NAMES[wall]} wall; no need to move it.")
    return " ".join(lines)


def suggestions(session) -> str:
    brief = session.room.design_brief if session.room is not None else None
    category = session.room.category if session.room is not None else "other"
    ideas = ROOM_IDEAS.get(category, "a comfortable seat, a side table, good lighting, and storage")
    room = "room" if category == "other" else category.replace("_", " ")
    parts = [f"For this {room}, I'd consider {ideas}."]
    if brief and (brief.styles or brief.colors):
        parts.append("I'll keep to " + ", ".join([*brief.styles, *brief.colors]) + ".")
    budget = getattr(session, "budget_minor", None)
    total = _total(_products(session))
    if budget:
        parts.append(f"Your budget is {money(budget)}; the current plan totals {money(total)}." if total
                     else f"I'll keep everything under {money(budget)}.")
    lighting = _lighting(session)
    if lighting and lighting.get("painting_spots"):
        best = lighting["painting_spots"][0]
        parts.append(f"For art, the {WALL_NAMES[best['wall']]} wall gets the least direct sun.")
    parts.append("Say \"add a ...\", \"move the ... to ...\", or \"make it under $...\" to change the plan.")
    return " ".join(parts)


def handle(session, text: str) -> str | None:
    """Reply directly for budget, suggestion, and painting-light requests; None sends the text to the planner."""
    budget = parse_budget(text)
    if budget is not None:
        session.budget_minor = budget
        total = _total(_products(session))
        if total and total > budget:
            # Plan is over budget — return None so the message goes straight to Argon.
            # with_budget() will inject a full replan instruction for Argon to act on immediately.
            return None
        return f"Budget set to {money(budget)}. I'll keep every suggestion within it."
    question = text.rstrip().endswith("?") or re.match(r"\s*(?:where|which|is|are|should|how|does|do|can)\b", text, re.I)
    action = re.search(r"\b(?:add|buy|place|put|move|hang|swap|remove|replace)\b", text, re.I)
    if PAINTING.search(text) and (question or not action):
        return painting_advice(session)
    if SUGGEST.search(text) and (question or not action):
        return suggestions(session)
    return None
