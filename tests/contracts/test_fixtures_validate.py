"""Every fixture validates against its model, and every contract has a fixture.

This is the gate the integration doc requires on every pull request:
`pytest tests/contracts` must pass before anything merges.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts import CONTRACT_MODELS, CONTRACT_OWNERS

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "fixtures"
SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"

pytestmark = pytest.mark.contracts


def snake(name: str) -> str:
    import re

    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# One valid fixture per contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize("contract_name", sorted(CONTRACT_MODELS))
def test_every_contract_has_a_sample_fixture(contract_name: str) -> None:
    path = FIXTURES / f"{snake(contract_name)}.sample.json"
    assert path.exists(), (
        f"{contract_name} (owner {CONTRACT_OWNERS[contract_name]}) has no fixture at "
        f"{path.relative_to(REPO_ROOT)}. Run: python scripts/make_fixtures.py"
    )


@pytest.mark.parametrize("contract_name", sorted(CONTRACT_MODELS))
def test_sample_fixture_validates(contract_name: str) -> None:
    model = CONTRACT_MODELS[contract_name]
    path = FIXTURES / f"{snake(contract_name)}.sample.json"
    model.model_validate(load(path))


@pytest.mark.parametrize("contract_name", sorted(CONTRACT_MODELS))
def test_sample_fixture_round_trips(contract_name: str) -> None:
    """Serialising a parsed fixture reproduces the same data, so a fixture can
    be fed straight back into a mock without drifting."""
    model = CONTRACT_MODELS[contract_name]
    path = FIXTURES / f"{snake(contract_name)}.sample.json"
    raw = load(path)
    once = model.model_validate(raw).model_dump(mode="json")
    twice = model.model_validate(once).model_dump(mode="json")
    assert once == twice


# --------------------------------------------------------------------------
# The mock-mode fixtures named in the integration doc
# --------------------------------------------------------------------------


def test_catalog_offers_fixture_has_enough_offers() -> None:
    """MOCK_COMMERCE serves 15 to 30 offers with missing-field cases."""
    offers = load(FIXTURES / "catalog_offers.sample.json")
    assert 15 <= len(offers) <= 30, f"expected 15 to 30 offers, found {len(offers)}"


def test_catalog_offers_fixture_validates() -> None:
    from contracts.catalog_offer import CatalogOffer

    for raw in load(FIXTURES / "catalog_offers.sample.json"):
        CatalogOffer.model_validate(raw)


def test_catalog_offers_include_the_missing_field_cases() -> None:
    """The mock catalog must exercise the awkward cases, not just happy ones."""
    from contracts.catalog_offer import CatalogOffer

    offers = [CatalogOffer.model_validate(r) for r in load(FIXTURES / "catalog_offers.sample.json")]

    assert any(o.dimensions is None for o in offers), "no offer with missing dimensions"
    assert any(not o.availability.available for o in offers), "no unavailable offer"
    assert any(
        o.dimension_source == "listing_text_inferred" for o in offers
    ), "no offer with dimensions inferred from listing text"
    assert any(o.dimension_source == "metafield" for o in offers), "no offer with metafield dimensions"
    assert any(
        o.dimensions is not None and not o.dimensions.has_footprint for o in offers
    ), "no offer with a partial footprint"


def test_no_shop_mcp_offer_is_shoppable() -> None:
    """The rule that keeps other merchants out of the cart, checked on the data."""
    from contracts.catalog_offer import CatalogOffer

    offers = [CatalogOffer.model_validate(r) for r in load(FIXTURES / "catalog_offers.sample.json")]
    for offer in offers:
        if offer.store.api_surface == "shop_mcp":
            assert not offer.shoppable, f"{offer.offer_id} is a discovery result but marked shoppable"


@pytest.mark.parametrize(
    "filename,module_path,class_name",
    [
        ("room_analysis.sample.json", "contracts.interfaces", "RoomAnalysis"),
        ("command_result.sample.json", "contracts.interfaces", "CommandResult"),
        ("gate_answers.sample.json", "contracts.interfaces", "GateAnswers"),
        ("command.sample.json", "contracts.interfaces", "Command"),
        ("fit_report_does_not_fit.sample.json", "contracts.fit_report", "FitReport"),
    ],
)
def test_supporting_fixtures_validate(filename: str, module_path: str, class_name: str) -> None:
    import importlib

    model = getattr(importlib.import_module(module_path), class_name)
    model.model_validate(load(FIXTURES / filename))


def test_budget_lines_fixture_validates() -> None:
    from contracts.interfaces import BudgetLine

    for raw in load(FIXTURES / "budget_lines.sample.json"):
        BudgetLine.model_validate(raw)


# --------------------------------------------------------------------------
# Schemas stay in step with the models
# --------------------------------------------------------------------------


def test_generated_schemas_match_the_models() -> None:
    """Runs the same --check CI uses. A model change without regeneration fails here."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "gen_schemas.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        "contracts/schemas/ or web/src/types/ is out of date.\n"
        f"{result.stdout}\n{result.stderr}"
    )


@pytest.mark.parametrize("contract_name", sorted(CONTRACT_MODELS))
def test_every_contract_has_a_schema(contract_name: str) -> None:
    path = SCHEMA_DIR / f"{snake(contract_name)}.schema.json"
    assert path.exists(), f"missing schema for {contract_name}"
    schema = load(path)
    assert schema["title"] == contract_name


def test_typescript_types_exist_for_every_contract() -> None:
    ts = (REPO_ROOT / "web" / "src" / "types" / "contracts.ts").read_text(encoding="utf-8")
    for contract_name in CONTRACT_MODELS:
        assert f"export interface {contract_name} " in ts, f"no TypeScript type for {contract_name}"


# --------------------------------------------------------------------------
# Contract hygiene
# --------------------------------------------------------------------------


def test_every_contract_has_a_declared_owner() -> None:
    assert set(CONTRACT_MODELS) == set(CONTRACT_OWNERS)
    assert set(CONTRACT_OWNERS.values()) <= {"A", "B", "C"}


def test_contract_count_matches_the_integration_doc() -> None:
    """Twelve contracts, exactly as the team integration doc lists them."""
    assert len(CONTRACT_MODELS) == 12


def test_interfaces_module_exposes_every_named_function() -> None:
    """The Protocol stubs the integration doc names, by lane."""
    import contracts.interfaces as interfaces

    lane_a = [
        "AnalyzeRoom",
        "ValidateRoomSpec",
        "HandleCommand",
        "PlanWorkflow",
        "RunWorkflow",
        "RunCheckoutGates",
        "RunRecovery",
    ]
    lane_c = [
        "SearchCatalog",
        "GetOffer",
        "CheckFit",
        "ComputeBudget",
        "CommitRevision",
        "EmitEvent",
        "BuildAuditInputs",
        "BuildCartReview",
        "ConfirmCart",
        "ContinueCheckout",
    ]
    for name in lane_a + lane_c:
        assert hasattr(interfaces, name), f"interfaces.py is missing the {name} Protocol"
        assert getattr(getattr(interfaces, name), "_is_protocol", False), (
            f"{name} is not a Protocol"
        )
