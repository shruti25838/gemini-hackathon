import asyncio
from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

from pydantic import Field

from backend.connections import ShopifyConnection
from backend.errors import ConnectionError
from backend.product_dimensions import listing_dimensions
from backend.product_models import ProductModelConnection
from backend.room import (
    ROOM_LIMITS, WALLS, Dimension, Measurement, PlacedProduct, RoomRecord, RoomSpec, RoomStudio,
    Size, Surface, Wall, apply_measurement, normalize,
)
from backend.room_lighting import scene_geometry
from backend import chat_commands
from backend.room_planner import PlanMessage, PlanRequest, PlacementPlanner, PlanSlot
from backend.scene import CONFIG_PATH, CONTEXT, SAMPLES
from backend.schemas import RequestBody


AXES = ("width", "depth", "height")
ROOM_IDEAS = {
    "bedroom": "a bed, a bedside table, and storage",
    "living_room": "a sofa, a side table, and a reading chair",
    "office": "a desk, a task chair, and a bookcase",
    "gaming_room": "a gaming desk, an adjustable chair, and storage",
    "kitchen": "a movable prep cart and freestanding storage",
    "dining_room": "a dining table, chairs, and a sideboard",
    "library": "a bookcase, a reading chair, and a side table",
    "studio_apartment": "a compact sofa, a drop-leaf table, and freestanding storage",
}
LENGTH = r"(\d+(?:\.\d+)?)\s*(metres?|meters?|cm|feet|ft|m)?(?=\s|[x×,.;!?]|$)"
LABELLED_LENGTH = re.compile(rf"\b(width|depth|length|height)\s*(?:to|is|=|:)?\s*{LENGTH}", re.I)
THREE_LENGTHS = re.compile(rf"\s*{LENGTH}\s*(?:x|×|,|by)\s*{LENGTH}\s*(?:x|×|,|by)\s*{LENGTH}\s*", re.I)
USER_CONFIRM = re.compile(r"^(?:confirm(?: placement)?|yes(?:,? place them)?|place them)[.!]?$", re.I)
SAMPLE_TABLE_PATH = SAMPLES / "shopify-walnut-side-table.json"


class StartRoomChat(RequestBody):
    room_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class RoomChatMessage(RequestBody):
    text: str = Field(min_length=1, max_length=700)
    review_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


@dataclass
class ChatSession:
    id: str
    phase: str = "measurements"
    measurements: dict[str, float] = field(default_factory=dict)
    messages: list[PlanMessage] = field(default_factory=list)
    seed: RoomSpec | None = None
    room: RoomSpec | None = None
    plan_id: str | None = None
    pending_plan_id: str | None = None
    review_id: str | None = None
    source_hash: str | None = None
    draft: dict[str, Any] | None = None
    published: dict[str, Any] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    budget_minor: int | None = None

    def say(self, text: str, role: str = "assistant") -> None:
        self.messages.append(PlanMessage(role=role, text=text))

    def response(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "phase": self.phase,
            "messages": [message.model_dump() for message in self.messages],
            "review_id": self.review_id if self.phase == "review" else None,
            "document": self.draft or self.published or {
                "units": "metres", "dimensions": {axis: {"value_m": value, "status": "confirmed"}
                                                for axis, value in self.measurements.items()},
            },
        }


def metres(value: str, unit: str | None) -> float:
    scale = {"cm": .01, "ft": .3048, "feet": .3048}.get((unit or "").lower(), 1.0)
    return round(float(value) * scale, 6)


def measurement_input(text: str, missing: list[str]) -> dict[str, float]:
    triple = THREE_LENGTHS.fullmatch(text.strip().rstrip("."))
    if triple:
        shared = triple[6] if not triple[2] and not triple[4] else None
        return {axis: metres(triple[index], triple[index + 1] or shared) for axis, index in zip(AXES, (1, 3, 5))}
    if re.match(r"^\s*(?:room\s+|change\s+(?:the\s+)?(?:room\s+)?)?(?:width|depth|length|height)\b", text, re.I):
        return {("depth" if axis.lower() == "length" else axis.lower()): metres(value, unit)
                for axis, value, unit in LABELLED_LENGTH.findall(text)}
    single = re.fullmatch(LENGTH, text.strip().rstrip("."))
    if single and len(missing) == 1:
        return {missing[0]: metres(single[1], single[2])}
    # Spoken or natural phrasing: "4 m wide, 3.5 m deep and 2.6 m high", "4 by 3.5 by 2.6 metres" inside a sentence.
    # Only while the room size is incomplete, so "add a 2 m long sofa" later is not read as a room size.
    if not missing:
        return {}
    adjectives = {"wide": "width", "deep": "depth", "long": "depth", "high": "height", "tall": "height"}
    found = {adjectives[word.lower()]: metres(value, unit) for value, unit, word in re.findall(
        r"(\d+(?:\.\d+)?)\s*(metres?|meters?|cm|feet|ft|m)?\s+(wide|deep|long|high|tall)\b", text, re.I)}
    if found:
        return found
    triple = THREE_LENGTHS.search(text)
    if triple:
        shared = triple[6] if not triple[2] and not triple[4] else None
        return {axis: metres(triple[index], triple[index + 1] or shared) for axis, index in zip(AXES, (1, 3, 5))}
    labelled = LABELLED_LENGTH.findall(text)
    if labelled:
        return {("depth" if axis.lower() == "length" else axis.lower()): metres(value, unit) for axis, value, unit in labelled}
    return {}


def measured_room(session: ChatSession) -> RoomSpec:
    if session.seed:
        spec = session.seed.model_copy(deep=True, update={"room_id": session.id})
        for axis, value in session.measurements.items():
            spec = apply_measurement(spec, Measurement(axis=axis, value_m=value))
        return spec
    return normalize(RoomSpec(
        room_id=session.id, revision=1, category="other", category_source="user",
        dimensions={axis: Dimension(value_m=session.measurements[axis], status="confirmed") for axis in AXES},
        walls=[Wall(id=wall, length_m=1.0, color="#e9e5dd", color_source="estimated",
                    material="paint", observed=False) for wall in WALLS],
        floor=Surface(color="#b48c63", color_source="estimated", material="wood"),
        openings=[], existing_items=[], views=[], photo_count=0,
    ))


@dataclass
class RoomChat:
    rooms: RoomStudio
    planner: PlacementPlanner
    shopify: ShopifyConnection
    path: Path = CONFIG_PATH
    sessions: dict[str, ChatSession] = field(default_factory=dict)

    def start(self, body: StartRoomChat) -> dict[str, Any]:
        session = ChatSession(id=uuid4().hex)
        if body.room_id:
            session.seed = self.rooms.current_spec(body.room_id)
            dims = ", ".join(f"{axis} {session.seed.dimensions[axis].value_m:g} m" for axis in AXES)
            session.say(f"The room has {dims}. Say “use these measurements” or enter all three measurements in metres.")
        else:
            session.say("What are the room's width, depth, and ceiling height? Enter them in that order, like 4 × 3.5 × 2.6 m.")
        self.sessions[session.id] = session
        return session.response()

    def _session(self, session_id: str) -> ChatSession:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise ConnectionError(404, "room_chat_missing", "This room chat has expired.") from exc

    def _config_bytes(self) -> bytes:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return b""
        except OSError as exc:
            raise ConnectionError(500, "room_chat_read_failed", "The shared room JSON could not be read.") from exc

    def _measure(self, session: ChatSession, values: dict[str, float]) -> None:
        for axis, value in values.items():
            low, high = ROOM_LIMITS[axis]
            if not low <= value <= high:
                session.say(f"Enter a {axis} between {low:g} and {high:g} metres.")
                return
        session.measurements.update(values)
        missing = [axis for axis in AXES if axis not in session.measurements]
        if missing:
            session.say("I still need " + " and ".join(missing) + " in metres.")
            return
        spec = measured_room(session)
        if session.room:
            spec.revision = session.room.revision + 1
        session.room = spec
        self.rooms.rooms[session.id] = RoomRecord(revisions=[spec])
        session.draft = spec.model_dump(mode="json")
        scene_geometry(spec, session.draft)
        session.plan_id = session.review_id = None
        session.phase = "planning_brief"
        width, depth, height = (spec.dimensions[axis].value_m for axis in AXES)
        overview = f"I read a {width:g} × {depth:g} × {height:g} m room."
        if spec.openings or spec.existing_items:
            overview += f" The room has {len(spec.openings)} recorded openings and {len(spec.existing_items)} existing items to work around."
        ideas = ROOM_IDEAS.get(spec.category)
        if ideas:
            category = spec.category.replace("_", " ")
            session.say(f"{overview}\nFor a {category}, I'd consider {ideas}. What style, colors, or must-have items do you prefer? Tell me what to keep or swap, or whether you want a different room type.")
        else:
            session.say(f"{overview}\nFor work or gaming, I'd consider a desk and chair. For relaxing, I'd consider a reading chair and side table. What type of room are you making, and what style, colors, or items do you prefer?")

    async def message(self, session_id: str, body: RoomChatMessage) -> dict[str, Any]:
        session = self._session(session_id)
        async with session.lock:
            text = body.text.strip()
            if not text:
                raise ConnectionError(422, "room_chat_text_required", "Enter a chat message.")
            if USER_CONFIRM.fullmatch(text):
                return self._confirm(session, body)
            if session.phase == "planning":
                raise ConnectionError(409, "room_chat_plan_pending", "Argon is still preparing the current plan.")
            session.say(text, "user")
            missing = [axis for axis in AXES if axis not in session.measurements]
            values = measurement_input(text, missing)
            if session.seed and session.phase == "measurements" and text.lower().rstrip(".") == "use these measurements":
                values = {axis: session.seed.dimensions[axis].value_m for axis in AXES}
            if values or session.phase == "measurements":
                if not values and (reply := chat_commands.handle(session, text)):
                    session.say(reply)
                    return session.response()
                self._measure(session, values)
                return session.response()
            if reply := chat_commands.handle(session, text):
                session.say(reply)
                return session.response()

            try:
                run = await self.planner.start(session.id, PlanRequest(
                    base_revision=session.room.revision, instruction=chat_commands.with_budget(session, text),
                    previous_plan_id=session.plan_id,
                ))
            except ConnectionError as exc:
                session.say(exc.payload["message"])
                return session.response()
            session.pending_plan_id = run.id
            session.review_id = None
            session.phase = "planning"
            return session.response()

    async def get(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        async with session.lock:
            if session.phase != "planning":
                return session.response()
            run = await self.planner.get(session.id, session.pending_plan_id)
            if run.status == "planning":
                return session.response()
            session.pending_plan_id = None
            session.phase = "planning_brief"
            if run.draft is None:
                session.say(" ".join(run.notes) or "Argon could not produce a layout. Try describing the items again.")
                return session.response()
            session.plan_id = run.id
            products = await asyncio.gather(*(self._product_for(slot) for slot in run.slots),
                                            return_exceptions=True)
            selected = [product for product in products if isinstance(product, PlacedProduct)]
            missing = [slot.label for slot, product in zip(run.slots, products) if not isinstance(product, PlacedProduct)]
            session.draft = {**run.draft, "proposed_products": [
                product.model_dump(mode="json", exclude={"model"}) for product in selected
            ]}
            if session.published:
                session.draft["placed_products"] = session.published["placed_products"]
            summary = run.messages[-1].text
            if run.notes:
                summary += "\n" + " ".join(run.notes)
            if missing:
                summary += " I could not verify a fitting Shopify variant for " + ", ".join(missing) + "."
            if selected:
                session.review_id = run.id
                session.source_hash = sha256(self._config_bytes()).hexdigest()
                session.phase = "review"
                summary += "\nReview these Shopify items. Tell me what to add, replace, remove, or move, or confirm once to finalize.\n" + "\n".join(
                    f"• {product.label} at ({product.pose.position.x:g}, {product.pose.position.y:g}, "
                    f"{product.pose.position.z:g}) m, yaw {product.pose.rotation.yaw:g}°"
                    for product in selected
                )
            session.say(summary)
            return session.response()

    async def _product_for(self, slot: PlanSlot) -> PlacedProduct | None:
        if re.search(r"\bwalnut\b", slot.shopify_query, re.I) and "side table" in slot.shopify_query.lower():
            sample = json.loads(SAMPLE_TABLE_PATH.read_text())
            try:
                result = await self.shopify.lookup_variant(sample["variant_id"], CONTEXT)
            except ConnectionError as exc:
                if exc.payload["code"] != "shop_variant_not_found":
                    raise
            else:
                product = self._choose(slot, result["products"])
                if product:
                    return product
        result = await self.shopify.search_catalog({
            "query": slot.shopify_query, "context": CONTEXT, "pagination": {"limit": 10},
        })
        # 1) a live result whose listed size fits, 2) a demo-catalog product (cached 3D) that fits,
        # 3) a matching live result without a listed size, placed in the planner's slot envelope.
        return (self._choose(slot, result["products"]) or self._from_catalog(slot)
                or self._choose_estimated(slot, result["products"]))

    @staticmethod
    def _slot_words(slot: PlanSlot) -> set[str]:
        """The slot's head noun (desk, chair, bookcase): the last word of its label, so a gaming desk never takes a chair."""
        words = re.findall(r"[a-z]{3,}", slot.label.lower()) or re.findall(r"[a-z]{3,}", slot.shopify_query.lower())
        noun = words[-1] if words else ""
        noun = noun[:-1] if noun.endswith("s") and not noun.endswith("ss") else noun
        synonyms = {"bookcase": {"bookcase", "bookshelf", "shelf"}, "shelf": {"shelf", "bookcase", "bookshelf", "shelving"},
                    "shelving": {"shelf", "shelving", "bookcase"}, "sofa": {"sofa", "couch", "loveseat"},
                    "lamp": {"lamp", "light"}, "nightstand": {"nightstand", "bedside"}, "rug": {"rug", "carpet"}}
        return synonyms.get(noun, {noun}) if noun else set()

    @staticmethod
    def _fits(size: dict[str, float] | None, slot: PlanSlot) -> bool:
        return bool(size) and all(size[axis] <= getattr(slot.size, axis) + 1e-6 for axis in AXES)

    def _from_catalog(self, slot: PlanSlot) -> PlacedProduct | None:
        words = self._slot_words(slot)
        catalog = SAMPLES / "catalog"
        entries = []
        for name in ("demo_250_alternatives.json", "demo_30.json", "products.json"):
            try:
                data = json.loads((catalog / name).read_text())
            except (FileNotFoundError, ValueError):
                continue
            entries += [item for group in data for item in group.get("alternatives", [])] if data and "alternatives" in data[0] else data
        for entry in entries:
            title = entry.get("title", "").lower()
            kind = entry.get("object_type", "").replace("_", " ")
            if not words or not any(word in title or word in kind for word in words):
                continue
            size = entry.get("size_m") or entry.get("typical_size_m")
            # Skip partial listing sizes (a seat height instead of the chair) that are far below the slot.
            if not self._fits(size, slot) or not entry.get("price") or size["height"] < slot.size.height * 0.5:
                continue
            return PlacedProduct(
                id=f"product_{uuid4().hex}", label=entry["title"], kind="furniture", pose_source="user",
                size=Size.model_validate(size), pose=slot.pose, variant_id=entry["variant_id"],
                merchant=entry.get("merchant") or "", listing_url=entry.get("listing_url"), price=entry["price"],
                dimension_source=entry.get("size_source") or "catalog", model={},
            )
        return None

    def _choose_estimated(self, slot: PlanSlot, products: list[dict[str, Any]]) -> PlacedProduct | None:
        words = self._slot_words(slot)
        for product in products:
            if not words or not any(word in product["title"].lower() for word in words):
                continue
            variant = product["variants"][0]
            return PlacedProduct(
                id=f"product_{uuid4().hex}", label=product["title"], kind="furniture", pose_source="user",
                size=slot.size, pose=slot.pose, variant_id=variant["id"], merchant=variant["seller"]["domain"],
                listing_url=variant.get("url"), price=variant.get("price"), dimension_source="planner_slot_estimate",
                model={},
            )
        return None

    @classmethod
    def _choose(cls, slot: PlanSlot, products: list[dict[str, Any]]) -> PlacedProduct | None:
        words = cls._slot_words(slot)
        for product in products:
            if words and not any(word in product["title"].lower() for word in words):
                continue
            for variant in product["variants"]:
                description = ProductModelConnection._description(variant.get("description") or product.get("description"))
                dimensions = listing_dimensions(variant.get("options", []), description)
                size = dimensions.dimensions_m
                if not size or any(size[axis] > getattr(slot.size, axis) + 1e-6 for axis in AXES):
                    continue
                return PlacedProduct(
                    id=f"product_{uuid4().hex}", label=product["title"], kind="furniture", pose_source="user",
                    size=Size.model_validate(size), pose=slot.pose,
                    variant_id=variant["id"], merchant=variant["seller"]["domain"], listing_url=variant.get("url"),
                    price=variant.get("price"), dimension_source=dimensions.source, model={},
                )
        return None

    def _confirm(self, session: ChatSession, body: RoomChatMessage) -> dict[str, Any]:
        if session.phase == "placed" and body.review_id and body.review_id == session.review_id:
            return session.response()
        if session.phase != "review" or body.review_id != session.review_id:
            raise ConnectionError(409, "room_chat_review_changed", "Review the current items before confirming placement.")
        raw = self._config_bytes()
        if sha256(raw).hexdigest() != session.source_hash:
            session.phase, session.review_id = "planning_brief", None
            session.say("The shared room JSON changed. Describe the layout again to prepare a current review.")
            return session.response()
        try:
            current = json.loads(raw) if raw else {}
            revision = current.get("revision", 0) if isinstance(current, dict) else None
            if type(revision) is not int or revision < 0:
                raise ValueError("Invalid room revision")
        except ValueError as exc:
            raise ConnectionError(409, "room_chat_invalid_config", "The shared room JSON has an invalid revision or format.") from exc
        placed = {**session.draft}
        proposed = placed.pop("proposed_products")
        placed["placed_products"] = [*session.room.model_dump(mode="json")["placed_products"], *proposed]
        placed["revision"] = max(revision, session.room.revision) + 1
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as output:
                temporary = Path(output.name)
                json.dump(placed, output, ensure_ascii=False, indent=2)
                output.write("\n")
            temporary.replace(self.path)
        except OSError as exc:
            if temporary:
                temporary.unlink(missing_ok=True)
            raise ConnectionError(500, "room_chat_write_failed", "The confirmed room JSON could not be saved.") from exc
        session.draft = None
        session.published = placed
        session.phase = "placed"
        session.say(body.text.strip(), "user")
        item_word = "item" if len(proposed) == 1 else "items"
        session.say(f"Placed {len(proposed)} approved {item_word} in the room JSON. Tell me what you want to change next.")
        return session.response()
