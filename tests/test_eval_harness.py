"""The evaluation harness: attribution is enforced, and the scoring works.

Two jobs here.

The first is attribution. The harness refuses a case that does not say who
wrote it and which group it belongs to, and refuses an outside case with no
recorded consent. That is not bureaucracy: a pipeline evaluated only on its
authors' own rooms looks better than it is, and the team-versus-outside split
is the only thing in the report that catches it.

The second is that the machinery actually scores something. The repository
ships no cases on purpose, so these tests build a temporary one from a
committed CC0 sample photograph and the stub lane A. That exercises the whole
path, photo to two fit verdicts, with no key and no network, without anybody
pretending a fabricated room is evaluation data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eval import run_eval

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_PHOTO = (
    "ethyneon-studio/samples/rooms/kitchen-5-photos/photo-1.jpg"
)


def a_case(**overrides: Any) -> dict[str, Any]:
    case = {
        "id": "temporary-test-case",
        "author": "the test suite",
        "group": "team",
        "photo": SAMPLE_PHOTO,
        "truth": {"width_m": 3.2, "depth_m": 2.9, "height_m": 2.5},
        "door": {
            "wall_id": "wall_s",
            "offset_m": 0.2,
            "width_m": 0.9,
            "hinge_side": "left",
            "swing_direction": "inward",
        },
        "probes": [
            {"object_id": "desk", "width_m": 1.0, "depth_m": 0.6, "x_m": 1.6, "y_m": 0.4},
        ],
        "labels": {"is_room": True},
    }
    case.update(overrides)
    return case


def write_cases(tmp_path: Path, *cases: dict[str, Any]) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": list(cases)}), encoding="utf-8")
    return path


# ==========================================================================
# No invented cases ship with the repository
# ==========================================================================


def test_the_repository_ships_no_cases() -> None:
    """Writing cases is the work. Inventing them makes the numbers a lie."""
    assert not (REPO_ROOT / "eval" / "data" / "cases.json").exists()


def test_a_missing_case_file_is_zero_cases_not_an_error(tmp_path: Path) -> None:
    assert run_eval.load_cases(tmp_path / "nothing-here.json") == []


def test_the_example_is_an_example_and_not_a_usable_case() -> None:
    """It documents the shape. It must not be scoreable, or someone will
    publish a report built on a room that does not exist."""
    example = REPO_ROOT / "eval" / "data" / "cases.example.json"
    assert example.is_file()

    with pytest.raises(run_eval.CaseError):
        run_eval.load_cases(example)


def test_the_empty_report_says_zero_for_both_groups(capsys) -> None:
    run_eval.print_report([], "stub (MOCK_AI is on)", Path("eval/data/cases.json"))
    printed = capsys.readouterr().out

    assert "team cases    : 0" in printed
    assert "outside cases : 0" in printed
    assert "ships no cases" in printed


# ==========================================================================
# Attribution is required
# ==========================================================================


@pytest.mark.parametrize("missing", ["id", "author", "group", "photo", "truth"])
def test_a_case_missing_a_required_field_is_refused(missing: str) -> None:
    case = a_case()
    del case[missing]

    with pytest.raises(run_eval.CaseError, match=missing):
        run_eval.validate_case(case, "where")


def test_the_group_must_be_team_or_outside() -> None:
    with pytest.raises(run_eval.CaseError, match="group"):
        run_eval.validate_case(a_case(group="friends"), "where")


def test_an_outside_case_needs_recorded_consent() -> None:
    with pytest.raises(run_eval.CaseError, match="consent"):
        run_eval.validate_case(a_case(group="outside"), "where")

    run_eval.validate_case(
        a_case(group="outside", consent={"granted": True, "date": "2026-11-21"}),
        "where",
    )


def test_a_team_case_does_not_need_consent() -> None:
    run_eval.validate_case(a_case(), "where")


def test_truth_must_be_numbers_not_guesses() -> None:
    with pytest.raises(run_eval.CaseError, match="width_m"):
        run_eval.validate_case(a_case(truth={"width_m": "about three", "depth_m": 2.9}), "where")


# ==========================================================================
# The scoring machinery
# ==========================================================================


def test_the_two_rooms_differ_only_in_the_drafted_dimensions() -> None:
    """A disagreement must be a perception error, not a geometry error."""
    from backend.lane_a import get_lane_a, reset_lane_a

    reset_lane_a()
    case = a_case()
    analysis = get_lane_a().analyze_room(
        (REPO_ROOT / SAMPLE_PHOTO).read_bytes(), "eval-test"
    )
    truth_room = run_eval.room_from_truth(case)
    draft_room = run_eval.room_from_draft(case, analysis.room_spec_draft)

    assert truth_room["openings"] == draft_room["openings"], "the door must be identical"
    assert truth_room["existing_items"] == draft_room["existing_items"]
    assert truth_room["dimensions_m"] != draft_room["dimensions_m"]
    assert draft_room["dimensions_m"]["depth_m"]["value_m"] == pytest.approx(4.0)


def test_a_case_is_scored_end_to_end_with_no_key_and_no_network(tmp_path: Path) -> None:
    path = write_cases(tmp_path, a_case())
    exit_code = run_eval.main([str(path), "--offline", "--json"])

    assert exit_code == 0


def test_the_report_counts_each_group_separately(tmp_path: Path, capsys) -> None:
    path = write_cases(
        tmp_path,
        a_case(id="team-one", author="Shruti Sridhar", group="team"),
        a_case(id="team-two", author="SKKarishma20", group="team"),
        a_case(
            id="outside-one",
            author="a neighbour",
            group="outside",
            consent={"granted": True, "date": "2026-11-21"},
        ),
    )
    assert run_eval.main([str(path), "--offline"]) == 0
    printed = capsys.readouterr().out

    assert "team cases : 2" in printed
    assert "outside cases : 1" in printed
    assert "Shruti Sridhar" in printed and "SKKarishma20" in printed
    assert "a neighbour" in printed
    assert "FALSE FITS" in printed


def test_group_filtering_narrows_the_run(tmp_path: Path, capsys) -> None:
    path = write_cases(
        tmp_path,
        a_case(id="team-one", group="team"),
        a_case(
            id="outside-one",
            group="outside",
            consent={"granted": True, "date": "2026-11-21"},
        ),
    )
    assert run_eval.main([str(path), "--offline", "--group", "outside"]) == 0
    printed = capsys.readouterr().out

    assert "team cases : 0" in printed
    assert "outside cases : 1" in printed


def test_a_false_fit_is_counted_on_its_own(tmp_path: Path) -> None:
    """The drafted room is deeper than the real one, so a probe can clear a
    door in the draft that it hits in the measured room. That is the error
    this harness exists to find, and it is never averaged into a score."""
    case = a_case(
        truth={"width_m": 3.2, "depth_m": 2.0},
        door={
            "wall_id": "wall_s", "offset_m": 0.2, "width_m": 0.9,
            "hinge_side": "left", "swing_direction": "inward",
        },
        probes=[{"object_id": "desk", "width_m": 1.0, "depth_m": 0.6, "x_m": 2.6, "y_m": 1.6}],
    )
    path = write_cases(tmp_path, case)
    run_eval.main([str(path), "--offline"])

    from backend.lane_a import get_lane_a, reset_lane_a

    reset_lane_a()
    scored = run_eval.score_case(case, get_lane_a())
    probes = scored["probes"]

    assert probes, "the probe should have been scored"
    assert set(probes[0]) >= {"measured_room", "drafted_room", "agrees", "false_fits"}
    assert isinstance(probes[0]["false_fits"], bool)


def test_a_case_pointing_at_a_missing_photo_fails_the_run(tmp_path: Path) -> None:
    path = write_cases(tmp_path, a_case(photo="eval/data/not-committed.jpg"))

    assert run_eval.main([str(path), "--offline"]) == 1


def test_a_malformed_case_file_fails_cleanly(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text("{not json", encoding="utf-8")

    assert run_eval.main([str(path), "--offline"]) == 2
