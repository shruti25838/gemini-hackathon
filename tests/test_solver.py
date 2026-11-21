from backend import solver


def room(door_motion=None):
    return {
        "revision": 1,
        "width_m": {"value_m": 3.2}, "depth_m": {"value_m": 3.0}, "height_m": {"value_m": 2.5},
        "walls": [], "existing_items": [],
        "openings": [{"id": "door1", "wall": "back", "type": "door", "offset_m": 2.2, "width_m": 0.8,
                      "motion": door_motion}],
    }


DESK = {"key": "desk", "category": "desk", "w": 1.4, "d": 0.7, "h": 0.75, "x": 2.5, "z": 0.35, "rot": 0}


def test_desk_in_door_swing_is_no_fit():
    rep = solver.check(room({"hinge_side": "left", "swing": "in"}), [DESK])
    assert rep["status"] == "no_fit"
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
    res = solver.search(r, [], [{**DESK, "x": 0, "z": 0}], preferred_wall="back")
    assert res["report"]["status"] in ("fits", "tight")
    assert res["rejected"], "should record rejected candidates near the door"


def test_hinge_side_changes_swept_region():
    # door on back wall spans x 2.2..3.0. Facing the back wall from inside, left is -x,
    # so a left hinge sits at x=2.2 and sweeps the quarter-disc around it.
    probe = {"key": "p", "category": "x", "w": 0.05, "d": 0.05, "x": 2.3, "z": 0.65, "rot": 0}
    base = room(None)["openings"][0]
    r_left = room({"hinge_side": "left", "swing": "in"})
    r_right = room({"hinge_side": "right", "swing": "in"})
    assert solver.check(r_left, [probe])["status"] == "no_fit"
    assert not any(c["kind"] == "door_swing" for c in solver.check(r_right, [probe])["collisions"])
