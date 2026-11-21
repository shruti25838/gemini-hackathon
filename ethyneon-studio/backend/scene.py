"""Render source for the central session file, geometricconfig.json.

Another agent edits geometricconfig.json while chatting with the shopper. This module reads it, applies
guarded shopper edits from the Design view (see backend/scene_edit.py), and
it converts the file into the room shape the Three.js viewer draws, resolves each placed product to a
cached 3D model keyed by its exact Shopify variant, and finds similar Shopify products for the side panel.
The frame matches backend/room.py: metres, Y-up, origin at the wall_n / wall_w floor corner.
"""

import asyncio
import base64
from dataclasses import dataclass, field
import hashlib
import json
import math
from os import environ
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from backend.connections import SHOPIFY_VARIANT_ID, ShopifyConnection
from backend.errors import ConnectionError
from backend.product_models import ProductModelConnection
from backend.scene_edit import SceneEdit, add_product, apply_edit, placement_problems, replacement_problem
from backend.studio import catalog_offer


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(environ.get("GEOMETRIC_CONFIG_PATH", ROOT / "geometricconfig.json"))
MODEL_CACHE = ROOT / ".cache" / "product-models"
SAMPLES = ROOT / "samples"
CATALOG = SAMPLES / "catalog" / "products.json"
# Models built outside the app (for example by another agent) go here as <numeric variant id>.glb, with an
# optional <numeric variant id>.json of product facts. A file here means the product's 3D model is cached.
MODEL_DROP = SAMPLES / "models"
MODEL_CACHE_VERSION = "3"
CONTEXT = {"address_country": "US", "currency": "USD"}
WALLS = ("wall_n", "wall_e", "wall_s", "wall_w")
DEFAULT_WALL = "#e9e5dd"
DEFAULT_FLOOR = {"color": "#b48c63", "material": "wood"}
# Fallback sizes (width, depth, height in metres) for fixtures the file lists without a size.
KIND_SIZES = {
    "wardrobe": (1.0, 0.6, 2.0), "cupboard": (0.9, 0.45, 0.9), "heater": (1.0, 0.12, 0.6), "air_conditioner": (0.9, 0.25, 0.3), "screen": (1.2, 0.08, 0.7),
    "pillar": (0.4, 0.4, None), "furniture": (0.8, 0.8, 0.8), "other": (0.5, 0.5, 0.5),
}


def _number(value: Any, fallback: float) -> float:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else fallback


def _vector(value: Any) -> dict[str, float]:
    value = value if isinstance(value, dict) else {}
    return {axis: _number(value.get(axis), 0.0) for axis in ("x", "y", "z")}


def _pose(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    rotation = value.get("rotation") if isinstance(value.get("rotation"), dict) else {}
    return {
        "position": _vector(value.get("position")),
        "rotation": {axis: _number(rotation.get(axis), 0.0) for axis in ("yaw", "pitch", "roll")},
    }


def _size(value: Any, kind: str, room_height: float) -> dict[str, float]:
    width, depth, height = KIND_SIZES.get(kind, KIND_SIZES["other"])
    value = value if isinstance(value, dict) else {}
    return {
        "width": max(_number(value.get("width"), width), 0.02),
        "depth": max(_number(value.get("depth"), depth), 0.02),
        "height": max(_number(value.get("height"), height if height is not None else room_height), 0.02),
    }


def scene_room(config: dict[str, Any]) -> dict[str, Any]:
    """Convert geometricconfig.json into the room shape RoomViewer renders. Missing optional fields get defaults."""
    dimensions = config.get("dimensions")
    if not isinstance(dimensions, dict):
        raise ValueError("dimensions are missing")
    dims = {}
    for axis in ("width", "depth", "height"):
        entry = dimensions.get(axis)
        value = entry.get("value_m") if isinstance(entry, dict) else entry
        if not isinstance(value, (int, float)) or not 0.5 <= value <= 30:
            raise ValueError(f"dimensions.{axis} must be a length in metres")
        dims[axis] = {"value_m": float(value), "status": entry.get("status", "estimated") if isinstance(entry, dict) else "estimated"}
    width, depth, height = (dims[axis]["value_m"] for axis in ("width", "depth", "height"))

    given_walls = {wall.get("id"): wall for wall in config.get("walls") or [] if isinstance(wall, dict)}
    walls = [{
        "id": wall_id,
        "length_m": width if wall_id in {"wall_n", "wall_s"} else depth,
        "color": given_walls.get(wall_id, {}).get("color", DEFAULT_WALL),
        "color_source": given_walls.get(wall_id, {}).get("color_source", "estimated"),
        "material": given_walls.get(wall_id, {}).get("material", "paint"),
        "observed": True,
    } for wall_id in WALLS]
    floor = {**DEFAULT_FLOOR, "color_source": "estimated", **(config.get("floor") or {})}

    openings, windows = [], []
    for index, opening in enumerate(config.get("openings") or [], 1):
        if not isinstance(opening, dict) or opening.get("wall") not in WALLS:
            continue
        kind = opening.get("type") if opening.get("type") in {"door", "window", "opening"} else "opening"
        wall = opening["wall"]
        length = width if wall in {"wall_n", "wall_s"} else depth
        span = min(max(_number(opening.get("width_m"), 0.9), 0.3), length)
        tall = min(max(_number(opening.get("height_m"), 2.0 if kind == "door" else 1.2), 0.3), height)
        position = _vector(opening.get("position"))
        # Positions in the file are the bottom centre of the opening on its wall.
        along = position["x"] if wall in {"wall_n", "wall_s"} else position["z"]
        offset = _number(opening.get("offset_m"), along - span / 2)
        sill = 0.0 if kind == "door" else _number(opening.get("sill_m"), position["y"])
        entry = {
            "id": str(opening.get("id") or f"{kind}_{index}"), "type": kind, "wall": wall,
            "offset_m": min(max(offset, 0.0), length - span), "width_m": span, "height_m": min(tall, height - sill),
            "sill_m": sill, "hinge": opening.get("hinge", "none"), "swing": opening.get("swing", "none"),
        }
        openings.append(entry)
        if kind == "window":
            start = {"wall_n": (0, 0), "wall_s": (0, depth), "wall_w": (0, 0), "wall_e": (width, 0)}[wall]
            run = entry["offset_m"] + span / 2
            center = {"x": start[0] + (run if wall in {"wall_n", "wall_s"} else 0), "y": sill + entry["height_m"] / 2,
                      "z": start[1] + (0 if wall in {"wall_n", "wall_s"} else run)}
            windows.append({"id": entry["id"], "wall": wall, "center": center, "area_m2": span * entry["height_m"]})

    existing = []
    for index, item in enumerate(config.get("existing_items") or [], 1):
        if not isinstance(item, dict):
            continue
        kind = item.get("kind", "other")
        existing.append({
            "id": str(item.get("id") or f"existing_{index}"), "label": item.get("label") or kind.replace("_", " "),
            "kind": kind, "fixed": bool(item.get("fixed", kind in {"pillar", "heater", "wardrobe"})),
            "size": _size(item.get("size"), kind, height), "pose": _pose(item.get("pose")),
        })

    products = []
    for index, item in enumerate(config.get("placed_products") or [], 1):
        if not isinstance(item, dict):
            continue
        variant = item.get("variant_id")
        products.append({
            "id": str(item.get("id") or f"product_{index}"),
            "label": item.get("label") or item.get("title") or "Product",
            "variant_id": variant if isinstance(variant, str) and re.fullmatch(SHOPIFY_VARIANT_ID, variant) else None,
            "price": item.get("price") if isinstance(item.get("price"), dict) else None,
            "listing_url": item.get("listing_url"),
            "size": _size(item.get("size"), "other", height),
            "pose": _pose(item.get("pose")),
            "model": item.get("model") if isinstance(item.get("model"), dict) else None,
        })

    lighting = config.get("lighting") if isinstance(config.get("lighting"), dict) else {}
    return {
        "room_id": "geometricconfig",
        "revision": config.get("revision", 0),
        "category": config.get("category", "other"),
        "design_brief": config.get("design_brief"),
        "dimensions": dims, "walls": walls, "floor": floor, "openings": openings,
        "existing_items": existing, "placed_products": products,
        "lighting": {
            **lighting, "windows": windows,
            "window_area_m2": round(sum(window["area_m2"] for window in windows), 3),
        },
    }


@dataclass
class SceneStudio:
    shopify: ShopifyConnection
    models: ProductModelConnection
    path: Path = CONFIG_PATH
    cache_dir: Path = MODEL_CACHE
    _last_good: tuple[str, dict[str, Any]] | None = None
    _memory: dict[str, dict[str, Any]] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _edit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _edits: dict[str, dict[str, Any]] = field(default_factory=dict)

    def read(self) -> tuple[str, dict[str, Any]]:
        """Return (etag, scene). A half-written or invalid file keeps serving the last valid scene."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError as exc:
            raise ConnectionError(404, "scene_config_missing", f"Waiting for {self.path.name}.", retryable=True) from exc
        etag = hashlib.sha1(raw).hexdigest()[:16]
        if self._last_good and self._last_good[0] == etag:
            return self._last_good
        try:
            scene = scene_room(json.loads(raw))
        except (ValueError, TypeError, AttributeError) as exc:
            if self._last_good:
                return self._last_good
            raise ConnectionError(422, "scene_config_invalid", f"{self.path.name} is not a valid room: {exc}.", retryable=True) from exc
        self._last_good = (etag, {"scene": scene, "etag": etag, "source": self.path.name})
        return self._last_good

    @staticmethod
    def _variant_number(variant_id: str) -> str:
        return variant_id.rsplit("/", 1)[-1]

    def _drop_file(self, variant_id: str) -> Path:
        return MODEL_DROP / f"{self._variant_number(variant_id)}.glb"

    @staticmethod
    def _catalog_facts(variant_id: str) -> dict[str, Any]:
        for path in (CATALOG.with_name("demo_30.json"), CATALOG):
            try:
                for entry in json.loads(path.read_text()):
                    if entry.get("variant_id") == variant_id:
                        return {key: entry.get(key) for key in ("title", "price", "merchant", "listing_url", "image_url")} | {
                            "dimensions_m": entry.get("size_m")}
            except (FileNotFoundError, ValueError):
                continue
        return {}

    def _dropped(self, variant_id: str) -> dict[str, Any] | None:
        glb = self._drop_file(variant_id)
        if not glb.exists():
            return None
        sidecar = glb.with_suffix(".json")
        facts = json.loads(sidecar.read_text()) if sidecar.exists() else {}
        return {
            "variant_id": variant_id, "title": "Shopify product", **self._catalog_facts(variant_id), **facts,
            "source": "model_drop",
            "model": {"format": "glb", "scope": "variant", "url": f"/studio/scene/models/{glb.name}",
                      "label": "Prepared 3D model for this exact Shopify variant"},
        }

    def model_state(self, variant_id: str) -> str:
        """cached: a 3D model is ready; building: one is being made now; missing: none yet."""
        if (variant_id in self._memory or self._drop_file(variant_id).exists() or self._cache_file(variant_id).exists()
                or self._sample(variant_id)):
            return "cached"
        lock = self._locks.get(variant_id)
        return "building" if lock and lock.locked() else "missing"

    def model_bytes(self, variant_number: str) -> bytes | str | None:
        """GLB bytes for a variant from any store (drop folder, generated cache, sample), or a merchant model URL."""
        variant_id = f"gid://shopify/ProductVariant/{variant_number}"
        drop = self._drop_file(variant_id)
        if drop.exists():
            return drop.read_bytes()
        record = self._memory.get(variant_id)
        if record is None and self._cache_file(variant_id).exists():
            record = json.loads(self._cache_file(variant_id).read_text())
        record = record or self._sample(variant_id)
        model = (record or {}).get("model") or {}
        if model.get("data_base64"):
            return base64.b64decode(model["data_base64"])
        return model.get("url")

    def model_states(self, variant_ids: list[str]) -> dict[str, str]:
        return {variant_id: self.model_state(variant_id) for variant_id in dict.fromkeys(variant_ids)}

    @staticmethod
    def _catalog_size(variant_id: str) -> dict[str, float] | None:
        try:
            for entry in json.loads(CATALOG.read_text()):
                if entry.get("variant_id") == variant_id:
                    listed, typical = entry.get("size_m"), entry.get("typical_size_m")
                    # A listed size far from the type's typical size is usually a part (a seat height), not the item.
                    if listed and typical and all(0.5 <= listed[axis] / typical[axis] <= 2 for axis in typical if typical[axis] > 0.05):
                        return listed
                    return typical or listed
        except (FileNotFoundError, ValueError):
            pass
        return None

    async def edit(self, request: SceneEdit) -> dict[str, Any]:
        """Apply one guarded edit to geometricconfig.json and return the new scene."""
        async with self._edit_lock:
            if request.action_id in self._edits:
                return self._edits[request.action_id]
            raw = self.path.read_bytes()
            if hashlib.sha1(raw).hexdigest()[:16] != request.base_etag:
                raise ConnectionError(409, "scene_changed", "The room changed since you started this edit. It has been reloaded.")
            config = json.loads(raw)
            size = None
            if request.op == "replace":
                size = self._catalog_size(request.replacement.variant_id)
                if size is None:
                    product = self._memory.get(request.replacement.variant_id) or {}
                    size = product.get("dimensions_m")
            before = {item["id"]: item for item in scene_room(config)["placed_products"]}.get(request.product_id)
            if request.op == "add":
                new = request.new_product
                product = self._memory.get(new.variant_id) or {}
                fit = new.size or self._catalog_size(new.variant_id) or product.get("dimensions_m") or \
                    {"width": 0.5, "depth": 0.5, "height": 0.8}
                try:
                    add_product(config, request, fit, scene_room)
                except ValueError as exc:
                    raise ConnectionError(422, "placement_blocked", str(exc)) from exc
                changed = []
            else:
                changed = None
            try:
                changed = apply_edit(config, request, size) if changed is None else changed
            except KeyError as exc:
                raise ConnectionError(404, "scene_product_missing", "That product is no longer in the room.") from exc
            problems = []
            if request.op == "replace" and before and size:
                after = next(item for item in scene_room(config)["placed_products"] if item["id"] == request.product_id)
                problem = replacement_problem(before["label"], after["label"], before["size"], after["size"])
                problems += [problem] if problem else []
            problems = problems or placement_problems(scene_room(config), changed)
            if problems:
                raise ConnectionError(422, "placement_blocked", " ".join(problems), details={"problems": problems})
            config["revision"] = int(config.get("revision") or 0) + 1
            config["last_edit"] = {"op": request.op, "product_id": request.product_id, "source": "design_view"}
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
            temporary.replace(self.path)
            self._last_good = None
            etag, body = self.read()
            self._edits[request.action_id] = body
            return body

    def _cache_file(self, variant_id: str) -> Path:
        key = hashlib.sha1(f"{MODEL_CACHE_VERSION}:{variant_id}".encode()).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _sample(self, variant_id: str) -> dict[str, Any] | None:
        for meta in SAMPLES.glob("*.json"):
            record = json.loads(meta.read_text())
            if record.get("variant_id") == variant_id and (SAMPLES / record["model"]["file"]).exists():
                data = base64.b64encode((SAMPLES / record["model"]["file"]).read_bytes()).decode("ascii")
                return {**record, "model": {**record["model"], "data_base64": data}}
        return None

    async def product(self, variant_id: str) -> dict[str, Any]:
        """Product facts and 3D model for an exact variant, cached in memory and on disk."""
        if variant_id in self._memory:
            return self._memory[variant_id]
        async with self._locks.setdefault(variant_id, asyncio.Lock()):
            if variant_id in self._memory:
                return self._memory[variant_id]
            cached = self._cache_file(variant_id)
            dropped = self._dropped(variant_id)
            if dropped:
                self._memory[variant_id] = dropped
                return dropped
            if cached.exists():
                record = json.loads(cached.read_text())
            else:
                record = self._sample(variant_id) or await self._convert(variant_id)
                cached.parent.mkdir(parents=True, exist_ok=True)
                temporary = cached.with_suffix(".tmp")
                temporary.write_text(json.dumps(record))
                temporary.replace(cached)
            self._memory[variant_id] = record
            return record

    @staticmethod
    def _snapshot(variant_id: str) -> dict[str, Any] | None:
        """The saved Shopify listing for a variant from the demo catalogs, used when live lookup fails."""
        for name in ("demo_250_alternatives.json", "demo_120_alternatives.json", "demo_30.json", "products.json"):
            try:
                data = json.loads(CATALOG.with_name(name).read_text())
            except (FileNotFoundError, ValueError):
                continue
            entries = [item for group in data for item in group.get("alternatives", [])] if data and "alternatives" in data[0] else data
            for entry in entries:
                if entry.get("variant_id") == variant_id and entry.get("image_url"):
                    return entry
        return None

    async def _convert(self, variant_id: str, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        """Shopify listing to 3D: live variant first (merchant 3D media or photo recipe), else the saved listing."""
        try:
            return await self.models.generate(variant_id, CONTEXT)
        except ConnectionError as error:
            snapshot = snapshot or self._snapshot(variant_id)
            if snapshot is None:
                raise
            record = await self.models.generate_snapshot({**snapshot, "variant_id": variant_id})
            return {**record, "conversion_note": f"Built from the saved Shopify listing ({error.payload['code']})."}

    async def product_from_link(self, url: str) -> dict[str, Any]:
        """Convert a pasted Shopify product link into a cached 3D model and product facts."""
        parsed = urlsplit(url.strip())
        if parsed.scheme != "https" or not parsed.hostname or re.fullmatch(r"[\d.:\[\]]+", parsed.hostname):
            raise ConnectionError(422, "shopify_link_invalid", "Paste an https link to a Shopify product page.")
        handle = re.search(r"/products/([A-Za-z0-9_-]+)", parsed.path)
        number = (parse_qs(parsed.query).get("variant") or [None])[0]
        listing = None
        if handle:
            try:
                response = await self.models.http.get(f"https://{parsed.hostname}/products/{handle[1]}.js",
                                                      timeout=10.0, follow_redirects=True)
                listing = response.json() if response.is_success else None
            except (ValueError, OSError, httpx.HTTPError):
                listing = None
        if not number and listing and listing.get("variants"):
            number = str(listing["variants"][0]["id"])
        if not number or not re.fullmatch(r"\d{5,20}", str(number)):
            raise ConnectionError(422, "shopify_link_unrecognised",
                                  "That link isn't a Shopify product page. Open the product and copy its URL.")
        variant_id = f"gid://shopify/ProductVariant/{number}"
        snapshot = None
        if listing:
            variant = next((v for v in listing.get("variants", []) if str(v.get("id")) == str(number)), {})
            image = (variant.get("featured_image") or {}).get("src") or (listing.get("images") or [None])[0]
            snapshot = {"variant_id": variant_id, "title": listing.get("title"), "description": listing.get("description"),
                        "image_url": ("https:" + image) if image and image.startswith("//") else image,
                        "options": [{"name": name, "label": value} for name, value in
                                    zip(listing.get("options") or [], [variant.get(f"option{i}") for i in (1, 2, 3)]) if value],
                        "price": {"amount": int(variant.get("price") or listing.get("price") or 0), "currency": "USD"},
                        "listing_url": url.strip()}
        if variant_id not in self._memory and not self._dropped(variant_id) and not self._cache_file(variant_id).exists():
            async with self._locks.setdefault(variant_id, asyncio.Lock()):
                record = await self._convert(variant_id, snapshot)
                record = {"listing_url": url.strip(), **record}
                cached = self._cache_file(variant_id)
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.with_suffix(".tmp").write_text(json.dumps(record))
                cached.with_suffix(".tmp").replace(cached)
                self._memory[variant_id] = record
        return await self.product(variant_id)

    def catalog_alternatives(self, variant_id: str, limit: int) -> list[dict[str, Any]]:
        """Use the demo product's curated alternatives when available."""
        try:
            groups = json.loads(CATALOG.with_name("demo_250_alternatives.json").read_text())
        except (FileNotFoundError, ValueError):
            groups = []
        for group in groups:
            alternatives = group["alternatives"]
            if group["seed_variant_id"] == variant_id:
                return alternatives[:limit]
            if any(item["variant_id"] == variant_id for item in alternatives):
                return [item for item in alternatives if item["variant_id"] != variant_id][:limit]
        try:
            catalog = json.loads(CATALOG.read_text())
        except (FileNotFoundError, ValueError):
            return []
        kind = next((entry["object_type"] for entry in catalog if entry["variant_id"] == variant_id), None)
        return [
            {key: entry.get(key) for key in ("variant_id", "title", "merchant", "image_url", "options", "price", "listing_url", "option")}
            for entry in catalog if entry["object_type"] == kind and entry["variant_id"] != variant_id
        ][:limit] if kind else []

    async def similar(self, variant_id: str, query: str, limit: int = 6) -> dict[str, Any]:
        query = " ".join(query.split())[:120]
        if not query:
            raise ConnectionError(422, "similar_query_missing", "Provide a product name to compare.")
        # Demo mix: half cached alternatives with ready 3D models, half live Shopify results built on click.
        cached = self.catalog_alternatives(variant_id, (limit + 1) // 2)
        document = await self.shopify.search_catalog({"query": query, "context": CONTEXT, "pagination": {"limit": limit + 6}})
        offers, seen = [], {offer["title"] for offer in cached} | {offer["variant_id"] for offer in cached}
        for product in document["products"]:
            offer = catalog_offer(product, product["variants"][0])
            if (offer["variant_id"] == variant_id or offer["title"] in seen or offer["variant_id"] in seen
                    or self.model_state(offer["variant_id"]) == "cached"):
                continue
            seen.add(offer["title"])
            offer["listing_url"] = product["variants"][0].get("url")
            offers.append(offer)
        live = offers[:limit - len(cached)]
        return {"query": query, "source": "cached_and_live", "cached": len(cached), "live": len(live),
                "results": [*cached, *live]}
