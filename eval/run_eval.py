"""Score the room-photo-to-fit-verdict pipeline against tape-measured rooms.

    Originally by SKKarishma20 on karishma/full-build as eval/run_eval.py.
    Rewritten here against our modules: her ai.gates, ai.vision, fit.solver and
    contracts.schemas imports do not exist in this tree. The question it asks
    is still hers, and so is the thing it is careful about: a false "fits" is
    the dangerous error, because it sends somebody to checkout for furniture
    that will hit their door.

Usage:

    python -m eval.run_eval                        # offline, stub model
    python -m eval.run_eval eval/data/cases.json   # whatever lane A is configured
    python -m eval.run_eval eval/data/cases.json --offline
    python -m eval.run_eval eval/data/cases.json --group outside

What it measures, per case:

    gate        did the photo gate agree with the case's labels
    geometry    how far the drafted room dimensions are from the tape measure
    verdict     for each probe placement, does the verdict computed on the
                drafted room match the verdict computed on the measured room

The last one is the headline. Both verdicts come from fit/, our engine, run
through fit.cli so the harness shares the path the goldens already cover and
adds no geometry of its own. The only thing that differs between the two runs
is the room: one is what the pipeline inferred from a photograph, the other is
what a tape measure says. So a disagreement is a perception error, not a
geometry error, which is the thing this harness exists to measure.

Offline mode installs the stub lane A, which serves fixtures and calls no
model. It needs no key and touches no network, so the harness is runnable and
testable with nothing configured.

There are no cases in this repository. Writing them is the work; inventing
them would make the numbers meaningless. With no cases the harness prints a
report of zeroes and says so, which is the honest output.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GROUPS = ("team", "outside")
DEFAULT_CASES = REPO_ROOT / "eval" / "data" / "cases.json"


# ==========================================================================
# Case loading. Every case must say who wrote it and which group it is in.
# ==========================================================================


class CaseError(ValueError):
    """A case file that cannot be scored, with the reason."""


def load_cases(path: Path) -> list[dict[str, Any]]:
    """Read and validate a case file. Missing file means no cases, not an error."""
    if not path.is_file():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CaseError(f"{path} is not valid JSON: {exc}") from exc

    cases = payload.get("cases", []) if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise CaseError(f"{path} must hold a list of cases under a 'cases' key")

    for index, case in enumerate(cases):
        validate_case(case, f"{path}[{index}]")
    return cases


def validate_case(case: dict[str, Any], where: str) -> None:
    """Refuse a case that is not attributable. Attribution is not optional.

    The split between team-written and outside-written cases is the whole
    point of reporting per group: a pipeline tuned on its authors' own rooms
    looks better than it is. A case with no author cannot be placed in either
    group, so it cannot be scored.
    """
    for field in ("id", "author", "group", "photo", "truth"):
        if not case.get(field):
            raise CaseError(f"{where}: missing required field '{field}'")

    if case["group"] not in GROUPS:
        raise CaseError(
            f"{where}: group is {case['group']!r}, must be one of {GROUPS}"
        )

    truth = case["truth"]
    for field in ("width_m", "depth_m"):
        if not isinstance(truth.get(field), (int, float)):
            raise CaseError(f"{where}: truth.{field} must be a tape-measured number")

    if case["group"] == "outside" and not case.get("consent", {}).get("granted"):
        raise CaseError(
            f"{where}: an outside case needs consent.granted, see eval/CASES.md"
        )


# ==========================================================================
# Building the two rooms a case compares
# ==========================================================================


def _measured(value: float, status: str = "confirmed") -> dict[str, Any]:
    return {"value_m": float(value), "evidence_status": status, "source": "live"}


def _wall(wall_id: str, length_m: float) -> dict[str, Any]:
    return {
        "wall_id": wall_id,
        "length_m": float(length_m),
        "color": {"hex": "#cccccc", "evidence_status": "confirmed", "source": "generated"},
    }


def room_from_truth(case: dict[str, Any]) -> dict[str, Any]:
    """The room a tape measure says is there."""
    truth = case["truth"]
    width, depth = float(truth["width_m"]), float(truth["depth_m"])
    height = float(truth.get("height_m", 2.5))

    openings = []
    door = case.get("door")
    if door:
        opening: dict[str, Any] = {
            "opening_id": door.get("opening_id", "door_main"),
            "wall_id": door["wall_id"],
            "type": "door",
            "offset_m": float(door["offset_m"]),
            "width_m": float(door["width_m"]),
            "height_m": float(door.get("height_m", 2.03)),
            "evidence_status": "confirmed",
        }
        if door.get("hinge_side") and door.get("swing_direction"):
            opening["hinge"] = {
                "hinge_side": door["hinge_side"],
                "swing_direction": door["swing_direction"],
                "swing_angle_max_deg": float(door.get("swing_angle_max_deg", 90.0)),
                "evidence_status": "confirmed",
                "motion_evidence_ref": door.get("evidence_ref", "tape_measure"),
            }
        openings.append(opening)

    return {
        "room_revision": 1,
        "session_id": f"eval-{case['id']}",
        "dimensions_m": {
            "width_m": _measured(width),
            "depth_m": _measured(depth),
            "height_m": _measured(height),
        },
        "walls": [
            _wall("wall_n", width), _wall("wall_e", depth),
            _wall("wall_s", width), _wall("wall_w", depth),
        ],
        "floor": {
            "floor_id": "floor",
            "color": {"hex": "#cccccc", "evidence_status": "confirmed", "source": "generated"},
        },
        "openings": openings,
        "existing_items": [],
        "free_zones": [],
        "evidence_status": "confirmed",
        "source": "generated",
    }


def room_from_draft(case: dict[str, Any], draft: Any) -> dict[str, Any]:
    """The pipeline's room: drafted dimensions, the case's door.

    The door is taken from the case rather than the draft on purpose. This
    harness measures how well the pipeline reads a room's size and shape. A
    door the model never saw would confound that with a different question.
    """
    room = room_from_truth(case)
    drafted = draft.model_dump(mode="json")["dimensions_m"]
    for axis in ("width_m", "depth_m", "height_m"):
        if drafted.get(axis, {}).get("value_m"):
            room["dimensions_m"][axis] = {
                "value_m": float(drafted[axis]["value_m"]),
                "evidence_status": drafted[axis].get("evidence_status", "estimated"),
                "source": drafted[axis].get("source", "generated"),
            }
    width = room["dimensions_m"]["width_m"]["value_m"]
    depth = room["dimensions_m"]["depth_m"]["value_m"]
    room["walls"] = [
        _wall("wall_n", width), _wall("wall_e", depth),
        _wall("wall_s", width), _wall("wall_w", depth),
    ]
    return room


def verdict(room: dict[str, Any], probe: dict[str, Any]) -> str:
    """Our fit engine's verdict for one probe, through the same path as the goldens."""
    from fit.cli import run_case

    payload = {
        "room_spec": room,
        "placements": [],
        "placement": {
            "object_id": probe.get("object_id", "probe"),
            "pose": {
                "x_m": float(probe["x_m"]),
                "y_m": float(probe["y_m"]),
                "rotation_deg": float(probe.get("rotation_deg", 0.0)),
            },
            "footprint": {
                "x_m": float(probe["x_m"]),
                "y_m": float(probe["y_m"]),
                "width_m": float(probe["width_m"]),
                "depth_m": float(probe["depth_m"]),
                "rotation_deg": float(probe.get("rotation_deg", 0.0)),
            },
        },
    }
    return str(run_case(payload)["fit_report"]["result"])


# ==========================================================================
# Scoring one case
# ==========================================================================


def score_case(case: dict[str, Any], provider: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": case["id"],
        "author": case["author"],
        "group": case["group"],
        "errors": [],
    }

    photo = REPO_ROOT / case["photo"]
    if not photo.is_file():
        out["errors"].append(f"photo not found: {case['photo']}")
        return out

    analysis = provider.analyze_room(photo.read_bytes(), f"eval-{case['id']}")
    out["gate_set"] = analysis.gates.gate_set
    out["gate_passed"] = bool(analysis.gates.all_passed)
    out["source"] = analysis.source

    labels = case.get("labels", {})
    if "is_room" in labels:
        out["gate_correct"] = out["gate_passed"] == bool(labels["is_room"])

    draft = analysis.room_spec_draft
    if draft is None:
        out["errors"].append("the gate returned no draft")
        return out

    drafted = draft.model_dump(mode="json")["dimensions_m"]
    truth = case["truth"]
    for axis in ("width_m", "depth_m"):
        value = drafted.get(axis, {}).get("value_m")
        if value:
            out[f"{axis}_error"] = abs(float(value) - float(truth[axis]))

    probes = case.get("probes", [])
    truth_room = room_from_truth(case)
    draft_room = room_from_draft(case, draft)
    comparisons = []
    for probe in probes:
        expected = verdict(truth_room, probe)
        actual = verdict(draft_room, probe)
        comparisons.append({
            "probe": probe.get("object_id", "probe"),
            "measured_room": expected,
            "drafted_room": actual,
            "agrees": expected == actual,
            # The one that sends somebody to checkout for furniture that will
            # not fit. Counted on its own, never folded into an accuracy score.
            "false_fits": actual == "fits" and expected in ("does_not_fit", "unknown"),
        })
    out["probes"] = comparisons
    return out


# ==========================================================================
# The report
# ==========================================================================


def summarise(results: list[dict[str, Any]], group: str) -> dict[str, Any]:
    mine = [r for r in results if r["group"] == group]
    probes = [p for r in mine for p in r.get("probes", [])]
    gate_calls = [r["gate_correct"] for r in mine if "gate_correct" in r]
    width_errors = [r["width_m_error"] for r in mine if "width_m_error" in r]
    depth_errors = [r["depth_m_error"] for r in mine if "depth_m_error" in r]

    return {
        "cases": len(mine),
        "authors": sorted({r["author"] for r in mine}),
        "cases_with_errors": sum(1 for r in mine if r["errors"]),
        "gate_calls": len(gate_calls),
        "gate_correct": sum(1 for correct in gate_calls if correct),
        "median_width_error_m": round(statistics.median(width_errors), 3) if width_errors else None,
        "median_depth_error_m": round(statistics.median(depth_errors), 3) if depth_errors else None,
        "probes": len(probes),
        "verdicts_agreeing": sum(1 for p in probes if p["agrees"]),
        "false_fits": sum(1 for p in probes if p["false_fits"]),
    }


def print_report(results: list[dict[str, Any]], lane_a: str, cases_path: Path) -> None:
    print()
    print("Ethyneon evaluation")
    print(f"  cases file : {cases_path}")
    print(f"  lane A     : {lane_a}")
    print()

    if not results:
        print("  No cases.")
        print()
        print("  team cases    : 0")
        print("  outside cases : 0")
        print()
        print("  This repository ships no cases. Each one needs a real room, a tape")
        print("  measure and, from anyone outside the team, written consent. Inventing")
        print("  them would make every number below meaningless, so there are none.")
        print()
        print("  eval/CASES.md says how to contribute one.")
        return

    for group in GROUPS:
        stats = summarise(results, group)
        print(f"  {group} cases : {stats['cases']}")
        if not stats["cases"]:
            continue
        print(f"    authors            : {', '.join(stats['authors'])}")
        if stats["cases_with_errors"]:
            print(f"    cases with errors  : {stats['cases_with_errors']}")
        if stats["gate_calls"]:
            print(f"    photo gate correct : {stats['gate_correct']} / {stats['gate_calls']}")
        if stats["median_width_error_m"] is not None:
            print(f"    median width error : {stats['median_width_error_m']} m")
        if stats["median_depth_error_m"] is not None:
            print(f"    median depth error : {stats['median_depth_error_m']} m")
        if stats["probes"]:
            print(f"    verdicts agreeing  : {stats['verdicts_agreeing']} / {stats['probes']}")
            print(f"    FALSE FITS         : {stats['false_fits']}"
                  "   (drafted room says fits, measured room does not)")
        print()

    totals = {
        "cases": len(results),
        "false_fits": sum(p["false_fits"] for r in results for p in r.get("probes", [])),
    }
    print(f"  total cases: {totals['cases']}   total false fits: {totals['false_fits']}")
    if totals["false_fits"]:
        print("  A false fit is the dangerous error. Report it separately, never averaged.")


# ==========================================================================
# Entry point
# ==========================================================================


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", nargs="?", default=str(DEFAULT_CASES),
                        help="case file; defaults to eval/data/cases.json")
    parser.add_argument("--offline", action="store_true",
                        help="force the stub lane A: no key, no network")
    parser.add_argument("--group", choices=GROUPS, default=None,
                        help="score only this group")
    parser.add_argument("--json", action="store_true", help="print raw results as JSON")
    args = parser.parse_args(argv)

    if args.offline:
        os.environ["MOCK_AI"] = "1"

    from backend.config import get_settings
    from backend.lane_a import get_lane_a, install_lane_a_from_settings

    get_settings.cache_clear()
    lane_a = install_lane_a_from_settings(get_settings())
    provider = get_lane_a()

    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = REPO_ROOT / cases_path

    try:
        cases = load_cases(cases_path)
    except CaseError as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2

    if args.group:
        cases = [c for c in cases if c["group"] == args.group]

    results = [score_case(case, provider) for case in cases]

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        print_report(results, lane_a, cases_path)

    # A case that could not be scored is a failure. No cases is not.
    return 1 if any(r["errors"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
