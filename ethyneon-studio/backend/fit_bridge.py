"""Ask the deterministic fit engine about one product in geometricconfig.json.

The Studio has its own guardrails in `scene_edit.placement_problems`, which
are fast, run on every drag, and answer one question: does this box overlap
something it must not. They are not a fit verdict. They cannot say tentative,
they cannot say unknown, and they do not know that a size read out of listing
text is weaker evidence than a size from a metafield.

Browse-first makes that distinction the whole point. Almost every product now
comes from another merchant with no structured dimension field, so its size
was parsed out of prose and must never support a firm "it fits". This module
converts a scene into the contract models and calls `fit.check_fit`, which is
the only thing in the repository allowed to return a verdict a shopper sees.

Two things make this more than a data copy.

**The wall frames disagree, and silently.** The Studio walks wall_s and
wall_w in the opposite direction from `fit/geometry.py`, so the same door
offset means two different places. This is the exact mistake that put the
vendored lane A solver at 7 of 15 on the golden cases. `fit_offset` converts
it, and `tests/backend/test_fit_bridge.py` pins the conversion against the
Studio's own `opening_zone`.

**Evidence has to survive the trip.** The Studio records where a size came
from; the contract records how much that is worth. `DIMENSION_SOURCE` maps
one to the other, and a listing-text size arrives at the engine as
`listing_text_inferred`, which caps the result at tentative by construction.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


class FitEngineUnavailable(RuntimeError):
    """The fit engine could not be imported. Raised with somewhere to go."""


# --------------------------------------------------------------------------
# The one place the repository root is put on sys.path
# --------------------------------------------------------------------------
#
# ethyneon-studio/ ships as its own package with its own virtual environment,
# so `fit` and `contracts` at the repository root are not importable from it.
# They are pure model and geometry code and depend on nothing but pydantic,
# which this environment already has, so a path entry is enough and no
# dependency is added to requirements.txt.
#
# This is the only sys.path manipulation in the Studio. If it ever needs to
# happen anywhere else, the right answer is to make the repository root a real
# installed package, not a second copy of these six lines.


def repo_root() -> Path:
    """The repository root, or a refusal that says what is missing."""
    root = Path(__file__).resolve().parents[2]
    missing = [
        str(part) for part in ("fit/engine.py", "contracts/__init__.py")
        if not (root / part).is_file()
    ]
    if missing:
        raise FitEngineUnavailable(
            f"The fit engine is not where the Studio expects it. Looked for "
            f"{', '.join(missing)} under {root}. ethyneon-studio/ must sit "
            f"inside the ethyneon repository for placement verdicts to work."
        )
    return root


def _install() -> None:
    root = str(repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


_install()

from contracts.catalog_offer import (  # noqa: E402
    Availability,
    CatalogOffer,
    ProductDimensions,
    StoreRef,
)
from contracts.common import ColorSpec, Measurement, Money, Pose, Rect2D  # noqa: E402
from contracts.room_spec import (  # noqa: E402
    ExistingItem,
    Floor,
    HingeSpec,
    Opening,
    RoomDimensions,
    RoomSpec,
    Wall,
)
from contracts.scene_revision import ObjectPlacement, SceneRevision  # noqa: E402
from fit.analysis import ProposedPlacement, analyze_fit  # noqa: E402


# --------------------------------------------------------------------------
# Frames and vocabularies
# --------------------------------------------------------------------------

# fit/geometry.py walks the walls clockwise: wall_s runs (W, D) -> (0, D) and
# wall_w runs (0, D) -> (0, 0). The Studio's room-viewer runs both the other
# way. An offset on those two walls therefore has to be measured from the
# other end. wall_n and wall_e already agree.
MIRRORED_WALLS = frozenset({"wall_s", "wall_w"})

# Hinge sides do NOT flip. The Studio's "left" is the left end seen from
# inside the room, which is the low-offset end on wall_n and wall_e and the
# high-offset end on wall_s and wall_w. The fit engine's "left" is the end
# nearer its own wall start. Once the offset is mirrored those are the same
# physical end on all four walls. Pinned by a test, because it does not look
# like it should be true.

# The Studio says where it read a size. The contract says what that is worth.
DIMENSION_SOURCE = {
    "shopify_selected_size": "listing_text_inferred",
    "shopify_listing_description": "listing_text_inferred",
    "catalog_fixture": "metafield",
    "unknown": "unknown",
}

OPENING_TYPE = {"door": "door", "window": "window", "opening": "archway"}
HINGE_SIDE = {"left": "left", "right": "right"}
SWING = {"inward": "inward", "outward": "outward", "sliding": "sliding"}

GREY = ColorSpec(hex="#cccccc", evidence_status="estimated", source="generated")


def fit_offset(wall_id: str, offset_m: float, opening_width_m: float, wall_length_m: float) -> float:
    """A Studio wall offset, measured in the fit engine's direction."""
    if wall_id not in MIRRORED_WALLS:
        return offset_m
    return max(0.0, wall_length_m - (offset_m + opening_width_m))


# --------------------------------------------------------------------------
# Scene to contracts
# --------------------------------------------------------------------------


def _measurement(entry: dict[str, Any]) -> Measurement:
    """A room dimension keeps its own evidence. An estimated room caps the
    result at tentative all by itself, which is correct: a verdict is only as
    good as the tape measure behind it."""
    status = entry.get("status") or "estimated"
    if status not in {"estimated", "confirmed", "unknown", "sampled_from_photo"}:
        status = "estimated"
    return Measurement(value_m=float(entry["value_m"]), evidence_status=status, source="live")


def room_spec(scene: dict[str, Any]) -> RoomSpec:
    width = scene["dimensions"]["width"]["value_m"]
    depth = scene["dimensions"]["depth"]["value_m"]
    lengths = {"wall_n": width, "wall_s": width, "wall_e": depth, "wall_w": depth}

    openings = []
    for opening in scene.get("openings") or []:
        wall_id = opening["wall"]
        kind = OPENING_TYPE.get(opening["type"], "archway")
        hinge = None
        if kind == "door":
            # "none" and anything unrecognised become unknown rather than a
            # guess, and the engine then answers unknown and says which datum
            # it wanted. That is the behaviour, not a gap in it.
            hinge = HingeSpec(
                hinge_side=HINGE_SIDE.get(opening.get("hinge"), "unknown"),
                swing_direction=SWING.get(opening.get("swing"), "unknown"),
                evidence_status="estimated",
            )
        openings.append(Opening(
            opening_id=opening["id"],
            wall_id=wall_id,
            type=kind,
            offset_m=fit_offset(wall_id, opening["offset_m"], opening["width_m"], lengths[wall_id]),
            width_m=opening["width_m"],
            height_m=opening["height_m"],
            sill_m=opening.get("sill_m") or None,
            hinge=hinge,
            evidence_status="estimated",
        ))

    existing = [
        ExistingItem(
            item_id=item["id"],
            label=item["label"],
            footprint=Rect2D(
                x_m=item["pose"]["position"]["x"], y_m=item["pose"]["position"]["z"],
                width_m=item["size"]["width"], depth_m=item["size"]["depth"],
                rotation_deg=item["pose"]["rotation"]["yaw"],
            ),
            height_m=item["size"]["height"],
            locked=bool(item.get("fixed")),
        )
        for item in scene.get("existing_items") or []
    ]

    return RoomSpec(
        room_revision=max(1, int(scene.get("revision") or 1)),
        session_id="studio",
        dimensions_m=RoomDimensions(
            width_m=_measurement(scene["dimensions"]["width"]),
            depth_m=_measurement(scene["dimensions"]["depth"]),
            height_m=_measurement(scene["dimensions"]["height"]),
        ),
        walls=[Wall(wall_id=wall_id, length_m=lengths[wall_id], color=GREY)
               for wall_id in ("wall_n", "wall_e", "wall_s", "wall_w")],
        floor=Floor(color=GREY),
        openings=openings,
        existing_items=existing,
        evidence_status="estimated",
    )


def _placement_of(item: dict[str, Any]) -> ObjectPlacement:
    position = item["pose"]["position"]
    return ObjectPlacement(
        object_id=item["id"],
        label=item["label"],
        pose=Pose(x_m=position["x"], y_m=position["z"], rotation_deg=item["pose"]["rotation"]["yaw"]),
        footprint=Rect2D(
            x_m=position["x"], y_m=position["z"],
            width_m=item["size"]["width"], depth_m=item["size"]["depth"],
            rotation_deg=item["pose"]["rotation"]["yaw"],
        ),
        height_m=item["size"]["height"],
    )


def scene_revision(scene: dict[str, Any], *, without: str) -> SceneRevision:
    """Everything already in the room except the item being asked about."""
    return SceneRevision(
        scene_revision=max(1, int(scene.get("revision") or 1)),
        session_id="studio",
        room_revision=max(1, int(scene.get("revision") or 1)),
        placements=[_placement_of(item) for item in scene["placed_products"]
                    if item["id"] != without],
    )


def offer_for(item: dict[str, Any]) -> CatalogOffer | None:
    """Carry the product's size and the provenance of that size.

    Returns None when the item has no variant, which is the shopper's own
    furniture rather than something from Shopify.
    """
    if not item.get("variant_id"):
        return None
    source = DIMENSION_SOURCE.get(item.get("dimension_source") or "unknown", "unknown")
    size = item.get("size") or {}
    dimensions = None
    if source != "unknown" and size.get("width") and size.get("depth"):
        dimensions = ProductDimensions(
            width_m=size["width"], depth_m=size["depth"], height_m=size.get("height"),
            evidence_status="estimated",
        )
    price = item.get("price") or {}
    return CatalogOffer(
        offer_id=f"scene:{item['id']}",
        store=StoreRef(
            store_domain=item.get("merchant") or "unknown.myshopify.com",
            api_surface="shop_mcp" if item.get("preview") else "storefront_api",
        ),
        # The scene file does not record the product ID, only the variant, and
        # nothing on the fit path reads this field. It is filled from the
        # variant so the model validates rather than so it can be trusted.
        product_id=item["variant_id"],
        variant_id=item["variant_id"],
        title=item["label"],
        price=Money(amount_minor=int(price.get("amount") or 0), currency=price.get("currency") or "USD"),
        availability=Availability(available=True),
        listing_url=item.get("listing_url") or "https://shopify.com/",
        dimensions=dimensions,
        dimension_source=source,
        shoppable=False,
    )


# --------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------

TENTATIVE_SENTENCE = (
    "This size was read from the listing text, not measured, so treat the result "
    "as a guide rather than a guarantee."
)


def check_placement(scene: dict[str, Any], product_id: str) -> dict[str, Any]:
    """Run one placed product through the fit engine and return the verdict."""
    item = next((entry for entry in scene["placed_products"] if entry["id"] == product_id), None)
    if item is None:
        raise KeyError(product_id)

    offer = offer_for(item)
    size = item.get("size") or {}
    position = item["pose"]["position"]
    # A footprint is supplied only when the size came from somewhere real. With
    # an unknown source the engine is asked with no footprint, so it answers
    # unknown and names the missing datum rather than measuring a default box.
    known_size = offer is not None and offer.dimensions is not None
    placement = ProposedPlacement(
        object_id=item["id"],
        pose=Pose(x_m=position["x"], y_m=position["z"], rotation_deg=item["pose"]["rotation"]["yaw"]),
        footprint=Rect2D(
            x_m=position["x"], y_m=position["z"],
            width_m=size["width"], depth_m=size["depth"],
            rotation_deg=item["pose"]["rotation"]["yaw"],
        ) if known_size else None,
        height_m=size.get("height") if known_size else None,
        offer_ref=offer.offer_id if offer else None,
    )

    analysis = analyze_fit(
        room_spec(scene),
        scene_revision(scene, without=product_id),
        placement,
        [offer] if offer else None,
    )
    report = analysis.report
    failing = next((check.name for check in report.checks_run if check.result == "fail"), None)
    # The engine writes its sentence about object_id, which is an internal
    # id like product_8f2c. The shopper is looking at a gaming chair.
    cause = analysis.cause.replace(item["id"], item["label"])
    return {
        "product_id": product_id,
        "result": report.result,
        "tentative": bool(report.tentative),
        "cause": cause,
        "failing_check": failing,
        "unknown_reasons": list(report.unknown_reasons),
        "minimum_clearance_m": report.minimum_clearance_m,
        "dimension_source": offer.dimension_source if offer else "unknown",
        "tentative_note": TENTATIVE_SENTENCE if report.tentative else None,
        "solver_version": report.solver_version,
    }
