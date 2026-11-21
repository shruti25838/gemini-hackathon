"""The fit command line, and the golden cases lane A will build against.

Two things are being protected here. One, that the documented CLI contract
holds: exit 0 for any verdict, exit 2 and a JSON error for bad input, never a
traceback. Two, that the CLI and the in-process engine agree, so a sandbox
result and a server result cannot quietly diverge.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fit import SOLVER_VERSION
from fit.cli import InputError, dump, load_case, run_case

REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = REPO_ROOT / "fixtures" / "fit_cases"

pytestmark = pytest.mark.fit

GOLDEN_FILES = sorted(CASES_DIR.glob("*.json"))
GOLDEN_IDS = [p.stem for p in GOLDEN_FILES]


def load_golden(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_cli(args: list[str], stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "fit", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        input=stdin,
    )


# ==========================================================================
# There are goldens, and they cover the required verdicts
# ==========================================================================


def test_the_golden_cases_exist() -> None:
    assert GOLDEN_FILES, "no golden cases in fixtures/fit_cases/"


def test_the_goldens_cover_every_required_outcome() -> None:
    """The set the brief names, plus the two extra checks."""
    goldens = [load_golden(p) for p in GOLDEN_FILES]
    results = {g["expected"]["result"] for g in goldens}
    assert {"fits", "tight", "does_not_fit", "unknown"} <= results

    names = {g["name"] for g in goldens}
    for required in (
        "does_not_fit_door_swing",
        "does_not_fit_overlaps_kept_item",
        "unknown_missing_product_dimensions",
        "unknown_missing_hinge",
        "unknown_missing_swing_direction",
        "tentative_estimated_room_scale",
    ):
        assert required in names, f"missing golden: {required}"

    assert any(g["expected"].get("tentative") for g in goldens)


def test_every_golden_says_what_it_is_for() -> None:
    for path in GOLDEN_FILES:
        golden = load_golden(path)
        assert golden.get("description"), f"{path.name} has no description"
        assert golden.get("input"), f"{path.name} has no input"
        assert golden.get("expected"), f"{path.name} has no expected block"


# ==========================================================================
# Each golden, through the real CLI
# ==========================================================================


@pytest.mark.parametrize("path", GOLDEN_FILES, ids=GOLDEN_IDS)
def test_the_cli_produces_the_expected_result(path: Path, tmp_path: Path) -> None:
    golden = load_golden(path)
    case_file = tmp_path / "case.json"
    case_file.write_text(json.dumps(golden["input"]), encoding="utf-8")

    result = run_cli(["check", "--input", str(case_file)])

    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    document = json.loads(result.stdout)
    report = document["fit_report"]
    expected = golden["expected"]

    assert report["result"] == expected["result"]

    if "tentative" in expected:
        assert report["tentative"] is expected["tentative"]

    if expected.get("failing_check") is None and "failing_check" in expected:
        assert report["rejected_placement"] is None
    elif expected.get("failing_check"):
        assert report["rejected_placement"] is not None
        assert report["rejected_placement"]["failing_check"] == expected["failing_check"]

    for needle in expected.get("unknown_reason_contains", []):
        joined = " ".join(report["unknown_reasons"])
        assert needle in joined, f"{needle!r} not in {joined!r}"

    if "cause_contains" in expected:
        assert expected["cause_contains"].lower() in document["extra"]["cause"].lower()

    if "assumption_contains" in expected:
        joined = " ".join(report["assumptions"])
        assert expected["assumption_contains"] in joined

    if expected.get("minimum_clearance_present") is True:
        assert report["minimum_clearance_m"] is not None
    elif expected.get("minimum_clearance_present") is False:
        assert report["minimum_clearance_m"] is None

    for wanted in expected.get("extra_checks", []):
        matching = [
            c for c in document["extra"]["extra_checks"] if c["name"] == wanted["name"]
        ]
        assert matching, f"no extra check named {wanted['name']}"
        assert matching[0]["result"] == wanted["result"]

    geometry_expected = expected.get("geometry", {})
    geometry = document["extra"]["geometry"]
    if "door_swings" in geometry_expected:
        assert len(geometry["door_swings"]) == geometry_expected["door_swings"]
    if "corridors" in geometry_expected:
        assert len(geometry["corridors"]) == geometry_expected["corridors"]
    if "overlaps_at_least" in geometry_expected:
        assert len(geometry["overlaps"]) >= geometry_expected["overlaps_at_least"]


@pytest.mark.parametrize("path", GOLDEN_FILES, ids=GOLDEN_IDS)
def test_the_cli_matches_the_engine_exactly(path: Path, tmp_path: Path) -> None:
    """A sandbox result and a server result must not diverge."""
    golden = load_golden(path)
    case_file = tmp_path / "case.json"
    case_file.write_text(json.dumps(golden["input"]), encoding="utf-8")

    from_cli = json.loads(run_cli(["check", "--input", str(case_file)]).stdout)
    in_process = run_case(golden["input"])

    # created_at is a timestamp, so it is the one field allowed to differ.
    from_cli["fit_report"].pop("created_at", None)
    in_process["fit_report"].pop("created_at", None)
    assert from_cli == in_process


@pytest.mark.parametrize("path", GOLDEN_FILES, ids=GOLDEN_IDS)
def test_every_golden_is_deterministic(path: Path) -> None:
    """Same input, byte-identical output. Carbon reproduces what Argon ran."""
    golden = load_golden(path)
    first = run_case(golden["input"])
    second = run_case(golden["input"])
    first["fit_report"].pop("created_at", None)
    second["fit_report"].pop("created_at", None)
    assert dump(first) == dump(second)


# ==========================================================================
# The CLI contract
# ==========================================================================


def test_version_prints_the_solver_string() -> None:
    result = run_cli(["version"])
    assert result.returncode == 0
    assert result.stdout.strip() == SOLVER_VERSION
    assert SOLVER_VERSION.startswith("fit/")


def test_a_result_of_any_kind_exits_zero(tmp_path: Path) -> None:
    """does_not_fit and unknown are answers, not failures."""
    for name in ("does_not_fit_door_swing", "unknown_missing_hinge"):
        golden = load_golden(CASES_DIR / f"{name}.json")
        case_file = tmp_path / f"{name}.json"
        case_file.write_text(json.dumps(golden["input"]), encoding="utf-8")
        result = run_cli(["check", "--input", str(case_file)])
        assert result.returncode == 0, f"{name} should exit 0"


def test_output_can_go_to_a_file(tmp_path: Path) -> None:
    golden = load_golden(CASES_DIR / "fits_empty_room.json")
    case_file = tmp_path / "case.json"
    case_file.write_text(json.dumps(golden["input"]), encoding="utf-8")
    out_file = tmp_path / "result.json"

    result = run_cli(["check", "--input", str(case_file), "--output", str(out_file)])

    assert result.returncode == 0
    assert out_file.is_file()
    document = json.loads(out_file.read_text(encoding="utf-8"))
    assert document["fit_report"]["result"] == "fits"
    assert result.stdout == "", "with --output, stdout stays clean for piping"


def test_input_can_come_from_stdin(tmp_path: Path) -> None:
    golden = load_golden(CASES_DIR / "fits_empty_room.json")
    result = run_cli(["check", "--input", "-"], stdin=json.dumps(golden["input"]))
    assert result.returncode == 0
    assert json.loads(result.stdout)["fit_report"]["result"] == "fits"


# ==========================================================================
# Bad input: exit 2, JSON error, never a traceback
# ==========================================================================


@pytest.mark.parametrize(
    "content,code,field",
    [
        ("not json at all {", "invalid_json", ""),
        ("[1, 2, 3]", "invalid_json", ""),
        ('{"placement": {}}', "missing_field", "room_spec"),
        ('{"room_spec": {}}', "invalid_field", "room_spec"),
    ],
)
def test_malformed_input_exits_two_with_a_json_error(
    tmp_path: Path, content: str, code: str, field: str
) -> None:
    case_file = tmp_path / "bad.json"
    case_file.write_text(content, encoding="utf-8")

    result = run_cli(["check", "--input", str(case_file)])

    assert result.returncode == 2
    assert result.stdout == "", "errors go to stderr, so stdout stays parseable"
    payload = json.loads(result.stderr)
    assert payload["error"]["code"] == code
    assert "message" in payload["error"]
    assert "field_path" in payload["error"]
    if field:
        assert payload["error"]["field_path"].startswith(field)


def test_a_missing_file_exits_two_rather_than_crashing(tmp_path: Path) -> None:
    result = run_cli(["check", "--input", str(tmp_path / "nope.json")])
    assert result.returncode == 2
    assert json.loads(result.stderr)["error"]["code"] == "input_not_found"


@pytest.mark.parametrize(
    "content",
    [
        "not json at all {",
        '{"placement": {}}',
        '{"room_spec": {"dimensions_m": "wrong"}}',
        '{"room_spec": {}, "placement": {"object_id": "x"}}',
    ],
)
def test_bad_input_never_prints_a_traceback(tmp_path: Path, content: str) -> None:
    """The caller is often an agent. A stack trace is neither actionable
    for it nor safe to feed back into a model."""
    case_file = tmp_path / "bad.json"
    case_file.write_text(content, encoding="utf-8")
    result = run_cli(["check", "--input", str(case_file)])

    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert "File \"" not in combined
    assert ".py\", line" not in combined
    json.loads(result.stderr)


def test_a_bad_option_is_reported_against_its_field() -> None:
    golden = load_golden(CASES_DIR / "fits_empty_room.json")
    payload = dict(golden["input"])
    payload["options"] = {"clearance_margin_m": "wide"}
    with pytest.raises(InputError) as excinfo:
        run_case(payload)
    assert excinfo.value.field_path == "options.clearance_margin_m"


def test_load_case_rejects_a_non_object() -> None:
    with pytest.raises(InputError) as excinfo:
        load_case("[1,2,3]")
    assert excinfo.value.code == "invalid_json"


# ==========================================================================
# The output document
# ==========================================================================


def test_the_output_carries_the_solver_version() -> None:
    golden = load_golden(CASES_DIR / "fits_empty_room.json")
    document = run_case(golden["input"])
    assert document["solver_version"] == SOLVER_VERSION
    assert document["fit_report"]["solver_version"] == SOLVER_VERSION


def test_the_output_has_the_documented_top_level_shape() -> None:
    golden = load_golden(CASES_DIR / "fits_empty_room.json")
    document = run_case(golden["input"])
    assert set(document) == {"fit_report", "extra", "solver_version"}
    assert set(document["extra"]) == {"geometry", "extra_checks", "cause"}


def test_a_product_block_can_supply_the_footprint() -> None:
    """Lane A can pass plain dimensions instead of a whole CatalogOffer."""
    golden = load_golden(CASES_DIR / "fits_empty_room.json")
    payload = dict(golden["input"])
    payload["placement"] = {
        "object_id": "obj_desk",
        "pose": {"x_m": 2.5, "y_m": 0.5, "rotation_deg": 0.0},
    }
    payload["product"] = {"width_m": 1.2, "depth_m": 0.6, "dimension_source": "metafield"}
    document = run_case(payload)
    assert document["fit_report"]["result"] == "fits"
