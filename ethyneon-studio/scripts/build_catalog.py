"""Collect the real Shopify products a full demo needs: every room, every taste style, with alternatives.

For each object type and each style (or, for tech items, each type option) the script keeps one main product
and two alternatives. It writes:

    samples/catalog/products.json   data for agents and the Design view alternatives panel
    samples/catalog/products.txt    the same list for people, grouped by room, object type, and style

    .venv/bin/python scripts/build_catalog.py
"""

import asyncio
from collections import defaultdict
import json
from pathlib import Path
import re
import sys

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.config import Settings  # noqa: E402
from backend.connections import ShopifyConnection  # noqa: E402
from backend.errors import ConnectionError  # noqa: E402
from backend.product_dimensions import listing_dimensions  # noqa: E402
from backend.product_models import ProductModelConnection  # noqa: E402
from backend.studio import catalog_offer  # noqa: E402


OUT = Path(__file__).resolve().parents[1] / "samples" / "catalog"
CONTEXT = {"address_country": "US", "currency": "USD"}
PER_GROUP = 3  # one main product and two alternatives
STYLES = {
    "modern": "modern", "minimalist": "minimalist", "scandinavian": "scandinavian",
    "mid_century": "mid century modern", "industrial": "industrial", "farmhouse": "farmhouse rustic", "boho": "boho",
}
ROOMS = {
    "gaming_room": "Gaming room", "office": "Working room / study", "living_room": "Living room",
    "bedroom": "Bedroom / guest room", "kitchen": "Kitchen",
}
ALL_ROOMS = list(ROOMS)
# Highest believable price in USD per object type; listings above it are usually mislabelled currencies.
MAX_PRICE_USD = {
    "sofa": 2500, "bed_frame": 2000, "wardrobe": 2000, "reading_chair": 1500, "armchair": 1500, "gaming_desk": 1200,
    "desk": 1200, "kitchen_island": 1200, "dresser": 1200, "tv_stand": 1200, "monitor": 1000, "gaming_chair": 800,
    "task_chair": 800, "rug": 800, "pantry_cabinet": 800, "bistro_set": 800, "coffee_table": 800, "bookcase": 600,
}
DEFAULT_MAX_USD = 400
# A product title must name its object type; this drops search results of another kind (a lantern for a floor lamp).
TITLE_MATCH = {
    "side_table": r"side|end table|accent table|c.?table|table", "floor_lamp": r"floor lamp|standing lamp|arc lamp|tripod lamp",
    "wall_art": r"art|print|canvas|painting|poster|frame", "rug": r"rug|carpet", "bookcase": r"book|shel|etagere|ladder",
    "gaming_desk": r"desk", "gaming_chair": r"chair|respawn", "monitor": r"monitor|display|\d+hz|mount|arm",
    "gaming_storage": r"storage|cabinet|shel|stand|holder|organi[sz]er|cube|rack|drawer", "tv_stand": r"tv|media|console|entertainment|sideboard|credenza|sk[äa]nk",
    "desk": r"desk", "task_chair": r"chair", "drawer_unit": r"file|cabinet|drawer|cart", "desk_lamp": r"lamp|light",
    "reading_chair": r"chair|lounge|fauteuil", "sofa": r"sofa|couch|loveseat|sectional|settee", "coffee_table": r"coffee|cocktail|table",
    "armchair": r"chair|recliner|fauteuil", "bed_frame": r"bed", "nightstand": r"nightstand|bedside|night stand|end table|side table",
    "dresser": r"dresser|chest|drawer", "wardrobe": r"wardrobe|armoire|closet|garment|clothes|rack", "bedside_lamp": r"lamp|light|sconce",
    "kitchen_island": r"island|cart|kitchen", "bar_stool": r"stool", "pantry_cabinet": r"pantry|cabinet|cupboard|hutch|rack",
    "bistro_set": r"bistro|set|table",
}

# object type, rooms, mount, typical size (W, D, H metres), search noun, tech options or None to vary by style
OBJECTS = [
    ("side_table", ALL_ROOMS, "floor", (0.45, 0.45, 0.6), "side table", None),
    ("floor_lamp", ALL_ROOMS, "floor", (0.35, 0.35, 1.65), "floor lamp", None),
    ("wall_art", ALL_ROOMS, "wall", (0.6, 0.03, 0.8), "wall art", None),
    ("rug", ["gaming_room", "office", "living_room", "bedroom"], "floor", (1.6, 2.3, 0.01), "area rug", None),
    ("bookcase", ["gaming_room", "office", "living_room"], "floor", (0.8, 0.3, 1.8), "bookcase", None),
    ("gaming_desk", ["gaming_room"], "floor", (1.4, 0.7, 0.75), "gaming desk", None),
    ("gaming_chair", ["gaming_room"], "floor", (0.66, 0.66, 1.25), "gaming chair", [
        ("mesh ergonomic", "mesh ergonomic gaming chair"), ("racing", "racing gaming chair pu leather"),
        ("footrest", "gaming chair with footrest"), ("fabric", "fabric gaming chair")]),
    ("monitor", ["gaming_room", "office"], "desk", (0.62, 0.2, 0.5), "monitor", [
        ("27 inch", "27 inch gaming monitor"), ("32 inch", "32 inch monitor"), ("34 inch ultrawide", "34 inch ultrawide monitor"),
        ("dual arm", "dual monitor arm stand")]),
    ("gaming_storage", ["gaming_room"], "floor", (0.8, 0.35, 0.9), "storage", [
        ("cube shelf", "cube shelf storage"), ("drawer cabinet", "storage cabinet with drawers"),
        ("headset stand", "headset controller stand"), ("game console cabinet", "game console storage cabinet")]),
    ("tv_stand", ["gaming_room", "living_room"], "floor", (1.6, 0.4, 0.5), "tv stand", None),
    ("desk", ["office"], "floor", (1.2, 0.6, 0.75), "desk", None),
    ("task_chair", ["office"], "floor", (0.6, 0.6, 1.1), "office chair", None),
    ("drawer_unit", ["office"], "floor", (0.4, 0.5, 0.6), "file cabinet", None),
    ("desk_lamp", ["office", "gaming_room"], "desk", (0.15, 0.15, 0.45), "desk lamp", None),
    ("reading_chair", ["office", "living_room"], "floor", (0.75, 0.8, 0.9), "accent chair", None),
    ("sofa", ["living_room"], "floor", (2.0, 0.9, 0.8), "sofa", None),
    ("coffee_table", ["living_room"], "floor", (1.1, 0.6, 0.42), "coffee table", None),
    ("armchair", ["living_room"], "floor", (0.75, 0.8, 0.85), "armchair", None),
    ("bed_frame", ["bedroom"], "floor", (1.6, 2.1, 1.0), "queen bed frame", None),
    ("nightstand", ["bedroom"], "floor", (0.45, 0.4, 0.55), "nightstand", None),
    ("dresser", ["bedroom"], "floor", (1.2, 0.45, 0.8), "dresser", None),
    ("wardrobe", ["bedroom"], "floor", (1.0, 0.6, 1.9), "wardrobe armoire", None),
    ("bedside_lamp", ["bedroom"], "nightstand", (0.25, 0.25, 0.45), "table lamp", None),
    ("kitchen_island", ["kitchen"], "floor", (0.9, 0.5, 0.9), "kitchen island cart", None),
    ("bar_stool", ["kitchen"], "floor", (0.4, 0.4, 0.7), "counter stool", None),
    ("pantry_cabinet", ["kitchen"], "floor", (0.8, 0.4, 1.8), "kitchen pantry cabinet", None),
    ("bistro_set", ["kitchen"], "floor", (0.7, 0.7, 0.75), "bistro table set", None),
]


async def search(shopify: ShopifyConnection, query: str, max_usd: int) -> list[dict]:
    for attempt in range(3):
        try:
            document = await shopify.search_catalog({
                "query": query, "context": CONTEXT, "pagination": {"limit": 12},
                "filters": {"price": {"min": 500, "max": max_usd * 100}},
            })
            return document["products"]
        except ConnectionError as error:
            if not error.payload["retryable"] or attempt == 2:
                print(f"  search failed for {query!r}: {error.payload['message']}", file=sys.stderr)
                return []
            await asyncio.sleep(2 * (attempt + 1))
    return []


def product_record(product: dict, kind: str, group: str, grouping: str, rooms: list[str], mount: str, typical: tuple) -> dict | None:
    variant = product["variants"][0]
    offer = catalog_offer(product, variant)
    price = offer["price"]
    if not offer["image_url"] or not isinstance(price, dict) or price.get("currency") != "USD" or not price.get("amount"):
        return None
    if price["amount"] > MAX_PRICE_USD.get(kind, DEFAULT_MAX_USD) * 100:
        return None
    if not re.search(TITLE_MATCH[kind], offer["title"], re.I):
        return None
    description = ProductModelConnection._description(variant.get("description") or product.get("description"))
    dimensions = listing_dimensions(offer["options"], description)
    return {
        "object_type": kind, grouping: group, "option": group, "rooms": rooms, "mount": mount,
        "variant_id": offer["variant_id"], "product_id": product["id"], "title": offer["title"],
        "price": price, "merchant": offer["merchant"], "listing_url": variant.get("url"), "image_url": offer["image_url"],
        "options": offer["options"],
        "size_m": dimensions.dimensions_m, "size_source": dimensions.source,
        "typical_size_m": dict(zip(("width", "depth", "height"), typical)),
    }


def money(price: dict) -> str:
    return f"${price['amount'] / 100:,.2f}"


def size(value: dict | None) -> str:
    return " x ".join(f"{value[axis] * 100:.0f}" for axis in ("width", "depth", "height")) + " cm" if value else "-"


def write_text(catalog: list[dict], gaps: list[str]) -> None:
    by_type: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for record in catalog:
        by_type[record["object_type"]][record["option"]].append(record)
    total_by_room = {room: sum(1 for record in catalog if room in record["rooms"]) for room in ROOMS}
    rule = "=" * 100
    lines = [
        "ETHYNEON FULL DEMO PRODUCT LIST", rule,
        f"{len(catalog)} real Shopify products across {len(by_type)} object types, {len(ROOMS)} room types, and {len(STYLES)} taste styles.",
        "Each style (or tech option) has 1 MAIN product and up to 2 ALTERNATIVES of the same object type.",
        "Styles: " + ", ".join(STYLES) + ". Tech items (gaming chair, monitor, gaming storage) vary by type option instead.",
        "Prices are USD from Shopify at build time; recheck before checkout.",
        "Model rules: GLB in metres, Y-up, floor-aligned, front facing +z, keyed by variant_id.",
        "Size: use 'listed' when present, otherwise 'typical' for the object type.",
        "", "ROOMS AND THE OBJECT TYPES THEY NEED", rule,
    ]
    for room, title in ROOMS.items():
        kinds = [kind for kind, rooms, *_ in OBJECTS if room in rooms]
        lines.append(f"{title} ({total_by_room[room]} products): " + ", ".join(kinds))
    lines += ["", "PRODUCTS BY OBJECT TYPE", rule]
    for kind, rooms, mount, typical, _, options in OBJECTS:
        groups = by_type.get(kind, {})
        count = sum(len(items) for items in groups.values())
        lines += ["", f"[{kind}]  {count} products  | rooms: {', '.join(rooms)}  | mount: {mount}  | typical size: "
                  f"{size(dict(zip(('width', 'depth', 'height'), typical)))}", "-" * 100]
        order = [name for name, _ in options] if options else list(STYLES)
        for group in order:
            items = groups.get(group, [])
            label = "option" if options else "style"
            lines.append(f"  {label}: {group}" + ("  (no Shopify match)" if not items else ""))
            for index, record in enumerate(items):
                role = "MAIN" if index == 0 else f"ALT {index}"
                lines += [
                    f"    {role:<6} {record['title'][:90]}",
                    f"           {money(record['price'])} | {record['merchant']} | listed size {size(record['size_m'])}",
                    f"           variant: {record['variant_id']}",
                    f"           link:    {record['listing_url'] or '-'}",
                ]
    if gaps:
        lines += ["", "GAPS (fewer than 3 products found)", rule, *gaps]
    (OUT / "products.txt").write_text("\n".join(lines) + "\n")
    names = []
    for kind, *_, options in OBJECTS:
        groups = by_type.get(kind, {})
        names += ["", kind.replace("_", " ").upper()]
        for group in ([name for name, _ in options] if options else list(STYLES)):
            for record in groups.get(group, []):
                names.append(record["title"])
    (OUT / "product_names.txt").write_text("\n".join(names).strip() + "\n")


async def main() -> None:
    settings = Settings.load()
    jobs = []
    for kind, rooms, mount, typical, noun, options in OBJECTS:
        if options:
            jobs += [(kind, rooms, mount, typical, group, query, "tech_option") for group, query in options]
        else:
            jobs += [(kind, rooms, mount, typical, style, f"{words} {noun}", "style") for style, words in STYLES.items()]
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as http:
        shopify = ShopifyConnection(http, settings.shopify_shops)
        semaphore = asyncio.Semaphore(4)

        async def run(job):
            async with semaphore:
                return job, await search(shopify, job[5], MAX_PRICE_USD.get(job[0], DEFAULT_MAX_USD))

        results = await asyncio.gather(*(run(job) for job in jobs))

    catalog, seen, gaps = [], set(), []
    for (kind, rooms, mount, typical, group, query, grouping), products in results:
        taken = 0
        for product in products:
            record = product_record(product, kind, group, grouping, rooms, mount, typical)
            if not record or record["variant_id"] in seen or record["title"].lower() in seen:
                continue
            seen.update({record["variant_id"], record["title"].lower()})
            record["role"] = "main" if taken == 0 else "alternative"
            catalog.append(record)
            taken += 1
            if taken == PER_GROUP:
                break
        if taken < PER_GROUP:
            gaps.append(f"{kind} / {group}: {taken} found for '{query}'")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "products.json").write_text(json.dumps(catalog, indent=2) + "\n")
    write_text(catalog, gaps)
    stale = OUT / "products.md"
    if stale.exists():
        stale.unlink()
    print(f"{len(catalog)} products, {len({r['object_type'] for r in catalog})} object types, {len(jobs)} searches -> {OUT}")
    for gap in gaps:
        print("  gap:", gap)


if __name__ == "__main__":
    asyncio.run(main())
