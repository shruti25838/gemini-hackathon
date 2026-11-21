"""The deliberately bad fixtures behave exactly as they are labelled.

Two kinds of failure, and the distinction matters:

  schema_valid false   the contract model itself must refuse the payload
  schema_valid true    the payload is well formed and must still be refused
                       later, by a rule that Phase 1 implements

For the second kind this file asserts the shape is genuinely valid and that
the violating condition really is present in the data, so Phase 1 has a target
it cannot accidentally satisfy with a fixture that was never wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
FAILURES = REPO_ROOT / "fixtures" / "failures" / "failure_cases.json"

pytestmark = pytest.mark.contracts

CASES = json.loads(FAILURES.read_text(encoding="utf-8"))["cases"]
CASES_BY_ID = {case["id"]: case for case in CASES}


def case(case_id: str) -> dict:
    return CASES_BY_ID[case_id]


def model_for(contract: str):
    """Resolve a contract name to its model, including the non-contract shapes."""
    import importlib

    from contracts import CONTRACT_MODELS

    if contract in CONTRACT_MODELS:
        return CONTRACT_MODELS[contract]
    for module_path in ("contracts.common", "contracts.scene_revision", "contracts.interfaces"):
        module = importlib.import_module(module_path)
        if hasattr(module, contract):
            return getattr(module, contract)
    return None


# --------------------------------------------------------------------------
# The brief names six failure samples. All six are present.
# --------------------------------------------------------------------------


def test_the_six_required_failure_cases_are_present() -> None:
    required = {
        "opening_outside_wall",
        "variant_with_no_dimensions",
        "unavailable_variant",
        "stale_base_revision",
        "wrong_variant_in_cart",
        "declined_test_payment",
    }
    missing = required - set(CASES_BY_ID)
    assert not missing, f"missing required failure fixtures: {sorted(missing)}"


def test_every_case_declares_where_it_is_caught() -> None:
    allowed = {
        "model",
        "validate_room_spec",
        "commit_revision",
        "cart_build",
        "checkout_reconcile",
        "fit_engine",
        "order_lookup",
        "prompt_sanitiser",
    }
    for entry in CASES:
        assert entry["rejected_by"] in allowed, (
            f"{entry['id']} declares an unknown rejected_by: {entry['rejected_by']}"
        )
        assert entry["assert"], f"{entry['id']} does not say what must happen"


# --------------------------------------------------------------------------
# Shape failures: the model must bite
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_id",
    sorted(c["id"] for c in CASES if not c["schema_valid"] and c["rejected_by"] == "model"),
)
def test_schema_invalid_cases_are_rejected_by_the_model(case_id: str) -> None:
    entry = case(case_id)
    model = model_for(entry["contract"])
    assert model is not None, f"no model found for {entry['contract']}"
    with pytest.raises(ValidationError):
        model.model_validate(entry["payload"])


def test_money_rejects_a_fractional_minor_unit() -> None:
    from contracts.common import Money

    with pytest.raises(ValidationError):
        Money.model_validate(case("money_as_float")["payload"])


def test_cart_review_rejects_a_subtotal_that_is_not_the_sum_of_lines() -> None:
    from contracts.cart_review import CartReview

    with pytest.raises(ValidationError) as excinfo:
        CartReview.model_validate(case("cart_subtotal_mismatch")["payload"])
    assert "sum of the lines" in str(excinfo.value)


def test_a_shop_mcp_offer_cannot_be_marked_shoppable() -> None:
    from contracts.catalog_offer import CatalogOffer

    with pytest.raises(ValidationError) as excinfo:
        CatalogOffer.model_validate(case("shop_mcp_offer_marked_shoppable")["payload"])
    assert "never be shoppable" in str(excinfo.value)


# --------------------------------------------------------------------------
# Rule failures: well formed, and still wrong
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_id", sorted(c["id"] for c in CASES if c["schema_valid"])
)
def test_rule_failure_cases_are_well_formed(case_id: str) -> None:
    """These parse cleanly. That is the point: the shape cannot catch them."""
    entry = case(case_id)
    model = model_for(entry["contract"])
    if model is None:
        pytest.skip(f"{entry['contract']} is not a model; checked by its own test")
    model.model_validate(entry["payload"])


def test_opening_outside_wall_really_does_overrun_its_wall() -> None:
    """Phase 1's validate_room_spec must catch this. Prove the data is wrong."""
    from contracts.room_spec import RoomSpec

    spec = RoomSpec.model_validate(case("opening_outside_wall")["payload"])
    walls = {wall.wall_id: wall for wall in spec.walls}
    overruns = [
        opening
        for opening in spec.openings
        if opening.offset_m + opening.width_m > walls[opening.wall_id].length_m
    ]
    assert overruns, "the fixture no longer contains an opening outside its wall"
    assert overruns[0].opening_id == "door_main"


def test_variant_with_no_dimensions_really_has_none() -> None:
    from contracts.catalog_offer import CatalogOffer

    offer = CatalogOffer.model_validate(case("variant_with_no_dimensions")["payload"])
    assert offer.dimensions is None
    assert offer.dimension_source == "unknown"
    assert offer.shoppable, "the point of this case is that it sells but cannot be fit-checked"


def test_unavailable_variant_really_is_unavailable() -> None:
    from contracts.catalog_offer import CatalogOffer

    offer = CatalogOffer.model_validate(case("unavailable_variant")["payload"])
    assert not offer.availability.available


def test_stale_base_revision_really_is_behind() -> None:
    from contracts.scene_revision import ScenePatch

    entry = case("stale_base_revision")
    patch = ScenePatch.model_validate(entry["payload"])
    assert patch.base_revision < entry["current_revision"]


def test_wrong_variant_in_cart_really_differs_from_the_approved_one() -> None:
    from contracts.cart_review import CartReview

    entry = case("wrong_variant_in_cart")
    review = CartReview.model_validate(entry["payload"])
    cart_variants = {line.variant_id for line in review.lines}
    assert entry["approved_variant_id"] not in cart_variants
    assert entry["live_cart_variant_id"] in cart_variants
    # The amounts match, so only the variant check can catch this.
    assert review.merchandise_subtotal.amount_minor == 42900


def test_declined_payment_never_becomes_order_confirmed() -> None:
    payload = case("declined_test_payment")["payload"]
    assert payload["gateway_result"] == "declined"
    assert payload["order_id"] is None
    assert payload["expected_payment_state"] == "payment_unconfirmed"
    # Opening the hosted checkout is not payment. Only an order lookup is.
    assert payload["checkout_opened_at"] is not None
    assert payload["expected_payment_state"] != "order_confirmed"


def test_no_failure_fixture_contains_card_data() -> None:
    """Card numbers are never stored or logged, not even in a fixture."""
    import re

    raw = FAILURES.read_text(encoding="utf-8")
    pan_like = re.findall(r"\b\d{13,19}\b", raw)
    shopify_ids = re.findall(r"(?:Product|ProductVariant|Shop)/(\d{13,19})", raw)
    assert set(pan_like) <= set(shopify_ids), (
        f"possible card-like number in the failure fixtures: "
        f"{sorted(set(pan_like) - set(shopify_ids))}"
    )


# --------------------------------------------------------------------------
# Untrusted product text
# --------------------------------------------------------------------------


def test_injection_probe_fixture_carries_real_injection_text() -> None:
    """The bait lives in the raw Shop MCP fields, which is where it really arrives."""
    entry = case("prompt_injection_in_product_text")
    raw = json.dumps(entry["raw_shop_mcp_fields"]).lower()
    for marker in entry["injection_markers"]:
        assert marker.lower() in raw, f"injection marker missing from the fixture: {marker}"
    assert "<b>" in entry["raw_shop_mcp_fields"]["title"], "the raw fields should carry HTML too"


def test_normalised_offer_carries_none_of_the_injection_text() -> None:
    """CatalogOffer has no description field, and the title is already clean.

    This is the contract-level half of the defence: even before the sanitiser
    exists, there is nowhere on the offer for the raw text to ride along.
    """
    from contracts.catalog_offer import CatalogOffer

    entry = case("prompt_injection_in_product_text")
    offer = CatalogOffer.model_validate(entry["payload"])
    blob = json.dumps(offer.model_dump(mode="json")).lower()
    for marker in entry["injection_markers"]:
        assert marker.lower() not in blob, (
            f"the normalised offer still carries injection text: {marker}"
        )
    assert "<" not in offer.title, "the normalised title still carries HTML"
    assert "description" not in CatalogOffer.model_fields, (
        "CatalogOffer must not carry raw product prose; strip it at normalisation"
    )
