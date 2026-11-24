"""A placement may arrive with no footprint, and the answer is unknown.

Browse-first makes this the ordinary case rather than a rare one. A product
found on Shop MCP carries no structured dimension field, so when nothing can
be parsed out of the listing text there is no width and no depth to test
with. The engine has always handled that: `fit.analysis.ProposedPlacement`
takes `footprint=None` and the golden `unknown_missing_product_dimensions`
pins the behaviour.

What could not express it was the published cross-lane signature,
`contracts.interfaces.ProposedPlacement`, where `footprint` was required. A
caller coding against the contract had to invent a footprint or fail
validation, and inventing one is exactly the guess the whole design refuses.
This file pins the two halves together:

  the contract accepts a placement with no footprint, and
  the engine answers unknown for it, names the missing datum, and never fits.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.catalog_offer import (
    Availability,
    CatalogOffer,
    ProductDimensions,
    StoreRef,
)
from contracts.common import Money, Pose, Rect2D
from contracts.interfaces import ProposedPlacement as ContractPlacement
from fit.engine import check_fit

from tests.fit.test_engine import empty_scene, make_room

pytestmark = pytest.mark.fit

REPO_ROOT = Path(__file__).resolve().parents[2]
VARIANT = "gid://shopify/ProductVariant/77000000000002"


def offer(
    *,
    width_m: float | None = None,
    depth_m: float | None = None,
    surface: str = "shop_mcp",
    dimension_source: str = "unknown",
) -> CatalogOffer:
    dimensions = None
    if width_m is not None or depth_m is not None:
        dimensions = ProductDimensions(
            width_m=width_m, depth_m=depth_m, height_m=1.3, evidence_status="estimated",
            raw_text="Dimensions: 68 x 70 x 130 cm",
        )
    return CatalogOffer(
        offer_id="offer_browse_1",
        store=StoreRef(store_domain="gtracingchair.myshopify.com", api_surface=surface),
        product_id="gid://shopify/Product/9000000000002",
        variant_id=VARIANT,
        title="GT905 Fabric Gaming Chair",
        price=Money(amount_minor=42900, currency="USD"),
        availability=Availability(available=True),
        listing_url="https://gtracingchair.myshopify.com/products/gt905",
        dimensions=dimensions,
        dimension_source=dimension_source,
        shoppable=False,
    )


# ==========================================================================
# The contract itself
# ==========================================================================


def test_the_contract_accepts_a_placement_with_no_footprint() -> None:
    placement = ContractPlacement(
        object_id="obj_chair", pose=Pose(x_m=2.0, y_m=2.0), offer_ref="offer_browse_1"
    )
    assert placement.footprint is None


def test_the_field_says_what_omitting_it_means() -> None:
    """Somebody reading the contract has to learn this without reading fit/."""
    description = ContractPlacement.model_fields["footprint"].description or ""
    assert "unknown" in description
    assert "CatalogOffer" in description


def test_a_footprint_that_is_supplied_is_still_validated() -> None:
    """Optional must not mean unchecked. A negative width is still refused."""
    with pytest.raises(ValidationError):
        ContractPlacement(
            object_id="obj_chair",
            pose=Pose(x_m=2.0, y_m=2.0),
            footprint=Rect2D(x_m=2.0, y_m=2.0, width_m=-1.0, depth_m=0.6),
        )


def test_every_payload_that_validated_before_still_validates() -> None:
    """Making a required field optional is only safe if nothing tightened."""
    placement = ContractPlacement(
        object_id="obj_desk",
        pose=Pose(x_m=2.0, y_m=0.5),
        footprint=Rect2D(x_m=2.0, y_m=0.5, width_m=1.2, depth_m=0.6),
        height_m=0.75,
        offer_ref="offer_1",
    )
    assert placement.footprint is not None
    assert placement.footprint.width_m == 1.2


def test_the_contract_type_is_not_published_to_schemas_or_typescript() -> None:
    """It is a Protocol input, not one of the twelve root contracts. If that
    ever changes, the generated files must be regenerated and this test is
    the reminder."""
    schemas = (REPO_ROOT / "contracts" / "schemas").glob("*.schema.json")
    for path in schemas:
        assert "ProposedPlacement" not in path.read_text(encoding="utf-8"), path.name
    typescript = (REPO_ROOT / "web" / "src" / "types" / "contracts.ts").read_text(encoding="utf-8")
    assert "ProposedPlacement" not in typescript


# ==========================================================================
# What the engine does with it
# ==========================================================================


def test_no_footprint_and_no_offer_dimensions_is_unknown() -> None:
    report = check_fit(
        make_room(),
        empty_scene(),
        ContractPlacement(
            object_id="obj_chair", pose=Pose(x_m=2.0, y_m=2.0), offer_ref="offer_browse_1"
        ),
        [offer()],
    )
    assert report.result == "unknown"
    assert report.result != "fits"


def test_the_missing_datum_is_named() -> None:
    report = check_fit(
        make_room(),
        empty_scene(),
        ContractPlacement(
            object_id="obj_chair", pose=Pose(x_m=2.0, y_m=2.0), offer_ref="offer_browse_1"
        ),
        [offer()],
    )
    joined = " ".join(report.unknown_reasons)
    assert VARIANT in joined
    assert "no documented footprint" in joined
    assert "no dimensions at all" in joined
    assert "dimension_source is unknown" in joined


def test_one_axis_alone_is_still_unknown() -> None:
    """A width with no depth is not a footprint. Half an answer is a guess."""
    report = check_fit(
        make_room(),
        empty_scene(),
        ContractPlacement(
            object_id="obj_chair", pose=Pose(x_m=2.0, y_m=2.0), offer_ref="offer_browse_1"
        ),
        [offer(width_m=0.68, dimension_source="listing_text_inferred")],
    )
    assert report.result == "unknown"
    assert "depth_m=None" in " ".join(report.unknown_reasons)


def test_no_footprint_with_no_offer_at_all_is_unknown_and_says_so() -> None:
    report = check_fit(
        make_room(),
        empty_scene(),
        ContractPlacement(object_id="obj_chair", pose=Pose(x_m=2.0, y_m=2.0)),
        [],
    )
    assert report.result == "unknown"
    assert any("no CatalogOffer" in reason for reason in report.unknown_reasons)


def test_the_offer_fills_the_footprint_in_when_it_has_one() -> None:
    """The change must not turn every browse placement into unknown. A listing
    that did state its size still gets a real verdict, capped at tentative."""
    report = check_fit(
        make_room(),
        empty_scene(),
        ContractPlacement(
            object_id="obj_chair", pose=Pose(x_m=2.0, y_m=2.0), offer_ref="offer_browse_1"
        ),
        [offer(width_m=0.68, depth_m=0.7, dimension_source="listing_text_inferred")],
    )
    assert report.result in {"fits", "tight"}
    assert report.tentative is True, "listing text never supports a firm claim"


def test_listing_text_dimensions_can_never_return_a_firm_fit() -> None:
    report = check_fit(
        make_room(),
        empty_scene(),
        ContractPlacement(
            object_id="obj_chair", pose=Pose(x_m=2.0, y_m=2.0), offer_ref="offer_browse_1"
        ),
        [offer(width_m=0.68, depth_m=0.7, dimension_source="listing_text_inferred")],
    )
    assert report.tentative is True


def test_the_cause_sentence_tells_the_shopper_what_is_missing() -> None:
    from fit.analysis import analyze_fit

    analysis = analyze_fit(
        make_room(),
        empty_scene(),
        ContractPlacement(
            object_id="obj_chair", pose=Pose(x_m=2.0, y_m=2.0), offer_ref="offer_browse_1"
        ),
        [offer()],
    )
    assert analysis.cause.startswith("Cannot tell whether")
    assert "no documented footprint" in analysis.cause


# ==========================================================================
# Nothing that passed before changed
# ==========================================================================


def test_every_golden_case_still_passes() -> None:
    """Run all fifteen through the CLI entry point the goldens use."""
    from fit.cli import run_case

    cases = sorted((REPO_ROOT / "fixtures" / "fit_cases").glob("*.json"))
    assert len(cases) >= 15, f"expected the full golden set, found {len(cases)}"

    for path in cases:
        case = json.loads(path.read_text(encoding="utf-8"))
        result = run_case(case["input"])["fit_report"]
        expected = case["expected"]
        assert result["result"] == expected["result"], f"{path.name}: {result['result']}"
        for fragment in expected.get("unknown_reason_contains", []):
            assert any(fragment in reason for reason in result["unknown_reasons"]), (
                f"{path.name}: {fragment!r} not in {result['unknown_reasons']}"
            )
