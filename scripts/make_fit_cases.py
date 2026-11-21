"""Write the golden fit cases in fixtures/fit_cases/.

    python scripts/make_fit_cases.py

Each file holds one `input` document, exactly as `python -m fit check` takes
it, and one `expected` block.

The expectations are written out by hand here rather than captured from the
engine. A golden recorded from the thing it is testing proves only that the
code still does what it did, including the parts that were wrong. These say
what the right answer is: a desk inside a door's arc must be does_not_fit by
door_swing, whatever the engine currently returns.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

CASES_DIR = REPO_ROOT / "fixtures" / "fit_cases"

GREY = {"hex": "#cccccc", "evidence_status": "confirmed", "source": "generated"}


def measurement(value: float, status: str = "confirmed") -> dict[str, Any]:
    return {"value_m": value, "evidence_status": status, "source": "live"}


def room(
    *,
    width: float = 4.0,
    depth: float = 4.0,
    openings: list[dict] | None = None,
    free_zones: list[dict] | None = None,
    width_status: str = "confirmed",
    depth_status: str = "confirmed",
) -> dict[str, Any]:
    return {
        "room_revision": 1,
        "session_id": "sandbox",
        "dimensions_m": {
            "width_m": measurement(width, width_status),
            "depth_m": measurement(depth, depth_status),
            "height_m": measurement(2.5),
        },
        "walls": [
            {"wall_id": "wall_n", "length_m": width, "color": GREY},
            {"wall_id": "wall_e", "length_m": depth, "color": GREY},
            {"wall_id": "wall_s", "length_m": width, "color": GREY},
            {"wall_id": "wall_w", "length_m": depth, "color": GREY},
        ],
        "floor": {"floor_id": "floor", "color": GREY},
        "openings": openings or [],
        "existing_items": [],
        "free_zones": free_zones or [],
        "evidence_status": "confirmed",
        "source": "generated",
    }


def door(
    *,
    hinge: dict | None = None,
    offset_m: float = 0.2,
    width_m: float = 0.9,
) -> dict[str, Any]:
    return {
        "opening_id": "door_main",
        "wall_id": "wall_s",
        "type": "door",
        "offset_m": offset_m,
        "width_m": width_m,
        "height_m": 2.03,
        "hinge": hinge,
        "evidence_status": "confirmed",
    }


INWARD_HINGE = {
    "hinge_side": "left",
    "swing_direction": "inward",
    "swing_angle_max_deg": 90.0,
    "evidence_status": "confirmed",
    "motion_evidence_ref": "clip_door_001",
}


def placed(object_id: str, label: str, x: float, y: float, w: float, d: float, locked=False):
    return {
        "object_id": object_id,
        "label": label,
        "pose": {"x_m": x, "y_m": y, "rotation_deg": 0.0},
        "footprint": {"x_m": x, "y_m": y, "width_m": w, "depth_m": d, "rotation_deg": 0.0},
        "locked": locked,
    }


def placement(object_id: str, x: float, y: float, w: float, d: float, **extra):
    body = {
        "object_id": object_id,
        "pose": {"x_m": x, "y_m": y, "rotation_deg": extra.pop("rotation_deg", 0.0)},
        "footprint": {
            "x_m": x,
            "y_m": y,
            "width_m": w,
            "depth_m": d,
            "rotation_deg": 0.0,
        },
    }
    body.update(extra)
    return body


def route_zone() -> dict[str, Any]:
    return {
        "zone_id": "route_door_to_desk",
        "label": "Door to desk",
        "area": {
            "points": [
                {"x_m": 0.2, "y_m": 3.9},
                {"x_m": 1.1, "y_m": 3.9},
                {"x_m": 1.1, "y_m": 0.8},
                {"x_m": 0.2, "y_m": 0.8},
            ]
        },
        "purpose": "walking_route",
    }


OFFER_NO_DIMENSIONS = {
    "offer_id": "offer_no_dims",
    "store": {"store_domain": "ethyneon.myshopify.com", "api_surface": "storefront_api"},
    "product_id": "gid://shopify/Product/1",
    "variant_id": "gid://shopify/ProductVariant/1",
    "title": "Mystery Desk (no size listed)",
    "price": {"amount_minor": 19900, "currency": "USD"},
    "availability": {"available": True},
    "listing_url": "https://ethyneon.myshopify.com/products/mystery-desk",
    "dimensions": None,
    "dimension_source": "unknown",
    "shoppable": True,
}

OFFER_INFERRED = {
    "offer_id": "offer_inferred",
    "store": {"store_domain": "mygarvee.myshopify.com", "api_surface": "shop_mcp"},
    "product_id": "gid://shopify/Product/2",
    "variant_id": "gid://shopify/ProductVariant/2",
    "title": "Folding desk",
    "price": {"amount_minor": 7299, "currency": "USD"},
    "availability": {"available": True},
    "listing_url": "https://example.com/p",
    "dimensions": {
        "width_m": 0.8,
        "depth_m": 0.4,
        "height_m": 0.72,
        "evidence_status": "estimated",
        "raw_text": "80 x 40 x 72 cm",
    },
    "dimension_source": "listing_text_inferred",
    "shoppable": False,
}


CASES: list[dict[str, Any]] = [
    {
        "name": "fits_empty_room",
        "description": "A measured room, an empty floor, a desk against the far wall.",
        "input": {
            "room_spec": room(openings=[door(hinge=INWARD_HINGE)]),
            "placements": [],
            "placement": placement("obj_desk", 2.5, 0.5, 1.2, 0.6),
        },
        "expected": {
            "result": "fits",
            "tentative": False,
            "failing_check": None,
            "cause_contains": "fits",
            "minimum_clearance_present": True,
        },
    },
    {
        "name": "tight_near_an_existing_item",
        "description": "A 0.02 m gap to a shelf, under the 0.05 m margin.",
        "input": {
            "room_spec": room(),
            "placements": [placed("obj_shelf", "Shelf", 2.0, 1.0, 1.0, 0.4)],
            "placement": placement("obj_desk", 2.0, 1.52, 1.2, 0.6),
        },
        "expected": {
            "result": "tight",
            "tentative": False,
            "failing_check": None,
            "cause_contains": "barely",
            "minimum_clearance_present": True,
        },
    },
    {
        "name": "does_not_fit_door_swing",
        "description": "The desk sits inside the arc of an inward-opening door.",
        "input": {
            "room_spec": room(openings=[door(hinge=INWARD_HINGE)]),
            "placements": [],
            "placement": placement("obj_desk", 3.4, 3.4, 1.0, 0.6),
        },
        "expected": {
            "result": "does_not_fit",
            "tentative": False,
            "failing_check": "door_swing",
            "cause_contains": "open door",
            "geometry": {"door_swings": 1, "overlaps_at_least": 1},
        },
    },
    {
        "name": "does_not_fit_overlaps_kept_item",
        "description": "The shopper is keeping their monitor; the desk lands on it.",
        "input": {
            "room_spec": room(),
            "placements": [placed("obj_monitor", "Kept monitor", 2.0, 1.0, 0.6, 0.25, locked=True)],
            "placement": placement("obj_desk", 2.0, 1.0, 1.2, 0.6),
        },
        "expected": {
            "result": "does_not_fit",
            "tentative": False,
            "failing_check": "locked_object_overlap",
            "cause_contains": "overlap",
            "geometry": {"overlaps_at_least": 1},
        },
    },
    {
        "name": "unknown_missing_product_dimensions",
        "description": "A real, buyable variant with no documented size.",
        "input": {
            "room_spec": room(),
            "placements": [],
            "placement": {
                "object_id": "obj_mystery",
                "pose": {"x_m": 2.0, "y_m": 2.0, "rotation_deg": 0.0},
                "offer_ref": "offer_no_dims",
            },
            "offers": [OFFER_NO_DIMENSIONS],
        },
        "expected": {
            "result": "unknown",
            "failing_check": None,
            "unknown_reason_contains": ["no documented footprint"],
            "cause_contains": "Cannot tell",
            "minimum_clearance_present": False,
        },
    },
    {
        "name": "unknown_missing_hinge",
        "description": "A door with no hinge recorded. The engine must not guess.",
        "input": {
            "room_spec": room(openings=[door(hinge=None)]),
            "placements": [],
            "placement": placement("obj_desk", 2.5, 0.5, 1.2, 0.6),
        },
        "expected": {
            "result": "unknown",
            "failing_check": None,
            "unknown_reason_contains": ["hinge"],
            "cause_contains": "Cannot tell",
        },
    },
    {
        "name": "unknown_missing_swing_direction",
        "description": "A hinge is known but which way it opens is not.",
        "input": {
            "room_spec": room(
                openings=[
                    door(hinge={"hinge_side": "left", "swing_direction": "unknown"})
                ]
            ),
            "placements": [],
            "placement": placement("obj_desk", 2.5, 0.5, 1.2, 0.6),
        },
        "expected": {
            "result": "unknown",
            "unknown_reason_contains": ["swing direction"],
            "cause_contains": "Cannot tell",
        },
    },
    {
        "name": "tentative_estimated_room_scale",
        "description": "It fits, but the room depth is still an estimate.",
        "input": {
            "room_spec": room(depth_status="estimated"),
            "placements": [],
            "placement": placement("obj_desk", 2.0, 0.5, 1.2, 0.6),
        },
        "expected": {
            "result": "fits",
            "tentative": True,
            "failing_check": None,
            "assumption_contains": "estimated",
        },
    },
    {
        "name": "tentative_inferred_product_dimensions",
        "description": "Dimensions parsed out of listing text cap the result at tentative.",
        "input": {
            "room_spec": room(),
            "placements": [],
            "placement": {
                "object_id": "obj_folding",
                "pose": {"x_m": 2.0, "y_m": 0.5, "rotation_deg": 0.0},
                "offer_ref": "offer_inferred",
            },
            "offers": [OFFER_INFERRED],
        },
        "expected": {
            "result": "fits",
            "tentative": True,
            "assumption_contains": "listing text",
        },
    },
    {
        "name": "does_not_fit_blocks_walking_route",
        "description": "The desk lands across the route from the door.",
        "input": {
            "room_spec": room(free_zones=[route_zone()]),
            "placements": [],
            "placement": placement("obj_desk", 0.7, 2.0, 1.2, 0.6),
        },
        "expected": {
            "result": "does_not_fit",
            "failing_check": "walking_route",
            "cause_contains": "walking route",
            "geometry": {"corridors": 1},
        },
    },
    {
        "name": "chair_pullback_clear",
        "description": "A chair with 0.6 m of pull-back room, well clear of the door.",
        "input": {
            "room_spec": room(openings=[door(hinge=INWARD_HINGE)]),
            "placements": [],
            "placement": placement(
                "obj_chair", 2.5, 1.0, 0.6, 0.6, category="chair", pullback_m=0.6
            ),
        },
        "expected": {
            "result": "fits",
            "extra_checks": [{"name": "chair_pullback", "result": "pass"}],
        },
    },
    {
        "name": "chair_pullback_hits_door_swing",
        "description": "The chair itself is clear, but pulling it back fouls the door.",
        "input": {
            "room_spec": room(openings=[door(hinge=INWARD_HINGE)]),
            "placements": [],
            "placement": placement(
                "obj_chair", 3.5, 2.55, 0.6, 0.6, category="chair", pullback_m=0.6
            ),
        },
        "expected": {
            "result": "does_not_fit",
            "extra_checks": [{"name": "chair_pullback", "result": "fail"}],
            "cause_contains": "door",
        },
    },
    {
        "name": "chair_pullback_unknown_without_the_distance",
        "description": "A chair with no pull-back distance supplied.",
        "input": {
            "room_spec": room(),
            "placements": [],
            "placement": placement("obj_chair", 2.0, 2.0, 0.6, 0.6, category="chair"),
        },
        "expected": {
            "result": "unknown",
            "extra_checks": [{"name": "chair_pullback", "result": "unknown"}],
            "unknown_reason_contains": ["pullback_m"],
        },
    },
    {
        "name": "drawer_extension_hits_a_wall",
        "description": "An open drawer would run past the wall.",
        "input": {
            "room_spec": room(),
            "placements": [],
            "placement": placement(
                "obj_drawers",
                2.0,
                3.75,
                0.4,
                0.5,
                category="drawer_unit",
                drawer_extension_m=0.5,
            ),
        },
        "expected": {
            "result": "does_not_fit",
            "extra_checks": [{"name": "drawer_extension", "result": "fail"}],
        },
    },
    {
        "name": "drawer_extension_unknown_without_the_depth",
        "description": "A drawer unit with no extension depth supplied.",
        "input": {
            "room_spec": room(),
            "placements": [],
            "placement": placement("obj_drawers", 2.0, 2.0, 0.4, 0.5, category="drawer_unit"),
        },
        "expected": {
            "result": "unknown",
            "extra_checks": [{"name": "drawer_extension", "result": "unknown"}],
            "unknown_reason_contains": ["drawer_extension_m"],
        },
    },
]


def main() -> int:
    CASES_DIR.mkdir(parents=True, exist_ok=True)

    written = []
    for case in CASES:
        path = CASES_DIR / f"{case['name']}.json"
        path.write_text(
            json.dumps(case, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written.append(path.name)

    # Remove goldens that no longer have a definition here.
    for existing in sorted(CASES_DIR.glob("*.json")):
        if existing.name not in written:
            existing.unlink()
            print(f"removed stale {existing.name}")

    for name in written:
        print(f"wrote fixtures/fit_cases/{name}")
    print(f"{len(written)} golden case(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
