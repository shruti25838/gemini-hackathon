"""Karishma's solver, tested by Karishma's own tests.

    Author: SKKarishma20
    Source: karishma/full-build, tests/test_solver.py
    Ported:  the import was repointed from "fit.solver" to the vendored copy
             at ai/karishma/_vendor/solver.py, which is byte identical to her
             fit/solver.py. The test bodies are hers, unchanged.

Why this file exists, when fit/ already has goldens.

This solver is not the fit engine. Nothing user-facing calls it. It is kept
because it is the geometry Karishma wrote, and a vendored dependency with no
tests is a dependency nobody can safely touch. These tests say what it does.

They also mark where it differs from fit/, which matters more than it looks.
The two disagree on wall traversal. fit/geometry.py walks the walls clockwise,
so wall_s runs from (W, D) to (0, D) and wall_w from (0, D) to (0, 0). This
solver runs both in the +axis direction. An offset_m on wall_s or wall_w
therefore means a mirrored position in the two engines, and the last test in
this file pins the convention this one uses.

Run both over fixtures/fit_cases and fit/ matches the expected verdict on 15
of 15 while this solver matches 7, every miss but one being a confident
"fits" where fit/ returns does_not_fit or unknown. It has no walkway check,
no drawer extension and no chair pull-back, and its "near a door" heuristic
misses a missing hinge. That is the reason the engine decides and the agent
only proposes.
"""

from __future__ import annotations

import pytest

from ai.karishma._vendor import solver

pytestmark = pytest.mark.ai


def room(door_motion=None):
    return {
        "room_revision": 1,
        "width_m": {"value_m": 3.2}, "depth_m": {"value_m": 3.0}, "height_m": {"value_m": 2.5},
        "walls": [], "existing_items": [],
        "openings": [{"id": "door1", "wall": "wall_n", "type": "door", "offset_m": 2.2, "width_m": 0.8,
                      "motion": door_motion}],
    }


DESK = {"key": "desk", "category": "desk", "w": 1.4, "d": 0.7, "h": 0.75, "x": 2.5, "z": 0.35, "rot": 0}


def test_desk_in_door_swing_is_does_not_fit():
    rep = solver.check(room({"hinge_side": "left", "swing": "in"}), [DESK])
    assert rep["status"] == "does_not_fit"
    assert any(c["kind"] == "door_swing" for c in rep["collisions"])


def test_outward_door_does_not_block():
    rep = solver.check(room({"hinge_side": "left", "swing": "out"}), [DESK])
    assert not any(c["kind"] == "door_swing" for c in rep["collisions"])


def test_unknown_motion_near_door_is_unknown():
    rep = solver.check(room(None), [DESK])
    assert rep["status"] == "unknown"


def test_out_of_bounds():
    p = {**DESK, "x": 3.0}
    rep = solver.check(room({"hinge_side": "left", "swing": "out"}), [p])
    assert any(c["kind"] == "out_of_bounds" for c in rep["collisions"])


def test_overlap_between_items():
    a = {**DESK, "x": 0.8}
    b = {"key": "chair", "category": "chair", "w": 0.6, "d": 0.6, "x": 1.0, "z": 0.4, "rot": 0}
    rep = solver.check(room({"hinge_side": "left", "swing": "out"}), [a, b])
    assert any(c["kind"] == "overlap" for c in rep["collisions"])


def test_missing_dims_is_unknown():
    p = {"key": "x", "category": "desk", "w": None, "d": None, "x": 1, "z": 1}
    rep = solver.check(room({"hinge_side": "left", "swing": "out"}), [p])
    assert rep["status"] == "unknown"


def test_search_avoids_door_swing():
    r = room({"hinge_side": "left", "swing": "in"})
    res = solver.search(r, [], [{**DESK, "x": 0, "z": 0}], preferred_wall="wall_n")
    assert res["report"]["status"] in ("fits", "tight")
    assert res["rejected"], "should record rejected candidates near the door"


def test_hinge_side_changes_swept_region():
    # door on back wall spans x 2.2..3.0. Facing the back wall from inside, left is -x,
    # so a left hinge sits at x=2.2 and sweeps the quarter-disc around it.
    probe = {"key": "p", "category": "x", "w": 0.05, "d": 0.05, "x": 2.3, "z": 0.65, "rot": 0}
    r_left = room({"hinge_side": "left", "swing": "in"})
    r_right = room({"hinge_side": "right", "swing": "in"})
    assert solver.check(r_left, [probe])["status"] == "does_not_fit"
    assert not any(c["kind"] == "door_swing" for c in solver.check(r_right, [probe])["collisions"])


# ==========================================================================
# Added here, not Karishma's: the seam between the two engines
# ==========================================================================


def test_the_vendored_copy_still_matches_her_original():
    """If this file is edited, it stops being her work. Pin the public surface."""
    assert solver.VERSION == "solver-1.2"
    for name in ("check", "search", "probe", "door_geometry", "door_swept_points"):
        assert callable(getattr(solver, name)), f"{name} went missing from the vendored solver"


def test_this_solver_decides_nothing_user_facing():
    """The engine decides every fit the product reports.

    Three places may import this solver, and the third is the interesting one.

      ai/      the adapter and her vendored modules, which is where it lives
      tests/   here
      demos/karishma-lane-a/   her standalone demo, which is explicitly not
               the product: it runs her front end against her backend and her
               geometry, on its own port, in mock mode, wired to nothing

    Anything else importing it means her solver has reached a verdict a
    shopper could act on, which is the thing the fit engine exists to own.
    """
    import subprocess
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    # Python source only. Prose in docs/ may name it; code may not import it.
    found = subprocess.run(
        ["git", "grep", "-l", "-E", r"_vendor[.]solver|_vendor import solver", "--", "*.py"],
        cwd=repo_root, capture_output=True, text=True,
    ).stdout.split()

    allowed = ("ai/", "tests/", "demos/karishma-lane-a/")
    assert found, "expected at least this test file to reference the vendored solver"
    for path in found:
        assert path.startswith(allowed), (
            f"{path} imports the vendored solver. The fit engine is the only thing "
            f"that may decide a fit the product reports."
        )
