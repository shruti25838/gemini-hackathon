"""Pick the 30 products the demo needs from samples/catalog/products.json.

Products that already have a cached 3D model are kept first, then the rest come from the catalog, preferring
listings that state their size, main picks over alternatives, and common styles. Every object type in the
list has at least two products so the alternatives panel can swap within the type.

    .venv/bin/python scripts/pick_demo_products.py
"""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "samples" / "catalog"
MODEL_CACHE = ROOT / ".cache" / "product-models"
STYLE_ORDER = ["modern", "mid_century", "minimalist", "scandinavian", "industrial", "farmhouse", "boho"]
# room, object type, how many, typical size (W, D, H metres) for cached models without a catalog entry
SLOTS = [
    ("gaming_room", "gaming_chair", 3, (0.66, 0.66, 1.25)),
    ("gaming_room", "gaming_desk", 3, (1.4, 0.7, 0.75)),
    ("gaming_room", "monitor", 2, (0.62, 0.2, 0.5)),
    ("gaming_room", "side_table", 2, (0.45, 0.45, 0.6)),
    ("gaming_room", "gaming_storage", 1, (0.8, 0.35, 0.9)),
    ("gaming_room", "floor_lamp", 1, (0.35, 0.35, 1.65)),
    ("gaming_room", "wall_art", 1, (0.6, 0.03, 0.8)),
    ("office", "desk", 2, (1.2, 0.6, 0.75)),
    ("office", "task_chair", 2, (0.6, 0.6, 1.1)),
    ("office", "bookcase", 1, (0.8, 0.3, 1.8)),
    ("office", "desk_lamp", 1, (0.15, 0.15, 0.45)),
    ("living_room", "sofa", 2, (2.0, 0.9, 0.8)),
    ("living_room", "coffee_table", 2, (1.1, 0.6, 0.42)),
    ("living_room", "armchair", 1, (0.75, 0.8, 0.85)),
    ("living_room", "tv_stand", 1, (1.6, 0.4, 0.5)),
    ("bedroom", "bed_frame", 2, (1.6, 2.1, 1.0)),
    ("bedroom", "nightstand", 1, (0.45, 0.4, 0.55)),
    ("kitchen", "kitchen_island", 1, (0.9, 0.5, 0.9)),
    ("kitchen", "bar_stool", 1, (0.4, 0.4, 0.7)),
]
# Already-modelled products and the object type each fills.
CACHED_TYPES = {
    "gid://shopify/ProductVariant/42611714130009": "gaming_chair",
    "gid://shopify/ProductVariant/50876631908516": "side_table",
    "gid://shopify/ProductVariant/46917740036316": "side_table",
    "gid://shopify/ProductVariant/51604078494017": "sofa",
}
ROOM_TITLES = {
    "gaming_room": "GAMING ROOM", "office": "WORKING ROOM / STUDY", "living_room": "LIVING ROOM",
    "bedroom": "BEDROOM", "kitchen": "KITCHEN",
}


def cached_models() -> dict[str, dict]:
    records = {}
    for path in MODEL_CACHE.glob("*.json"):
        record = json.loads(path.read_text())
        if record.get("variant_id") in CACHED_TYPES:
            records[record["variant_id"]] = record
    return records


def main() -> None:
    catalog = json.loads((CATALOG / "products.json").read_text())
    cached = cached_models()
    chosen, used = [], set()
    for room, kind, count, typical in SLOTS:
        picks = []
        for variant_id, record in cached.items():
            if CACHED_TYPES[variant_id] == kind and len(picks) < count:
                picks.append({
                    "room": room, "object_type": kind, "variant_id": variant_id, "title": record["title"],
                    "price": record.get("price"), "merchant": record.get("merchant"), "listing_url": record.get("listing_url"),
                    "image_url": record.get("image_url"), "size_m": record.get("dimensions_m"),
                    "typical_size_m": dict(zip(("width", "depth", "height"), typical)), "style": None,
                    "model_ready": True,
                })
        used.update(pick["variant_id"] for pick in picks)
        candidates = sorted(
            (entry for entry in catalog if entry["object_type"] == kind and entry["variant_id"] not in used),
            key=lambda entry: (
                entry["size_m"] is None, entry.get("role") != "main",
                STYLE_ORDER.index(entry["style"]) if entry.get("style") in STYLE_ORDER else 0,
            ),
        )
        styles = {pick.get("style") for pick in picks}
        for entry in candidates:
            if len(picks) == count:
                break
            if entry.get("style") and entry["style"] in styles:
                continue  # one product per style, so the type's alternatives differ in taste
            styles.add(entry.get("style"))
            picks.append({**{key: entry.get(key) for key in (
                "object_type", "variant_id", "title", "price", "merchant", "listing_url", "image_url", "size_m",
                "typical_size_m", "style", "mount")}, "room": room, "model_ready": False})
        used.update(pick["variant_id"] for pick in picks)
        chosen += picks

    (CATALOG / "demo_30.json").write_text(json.dumps(chosen, indent=2) + "\n")
    lines = [f"ETHYNEON DEMO: {len(chosen)} PRODUCTS", ""]
    for room, title in ROOM_TITLES.items():
        lines.append(title)
        lines += [f"  {pick['title']}" for pick in chosen if pick["room"] == room]
        lines.append("")
    (CATALOG / "demo_30.txt").write_text("\n".join(lines))
    print(f"{len(chosen)} products, {sum(pick['model_ready'] for pick in chosen)} already modelled, "
          f"{sum(pick['size_m'] is not None for pick in chosen)} with listed size")


if __name__ == "__main__":
    main()
