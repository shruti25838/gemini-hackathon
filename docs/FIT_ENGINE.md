# The fit engine, for lane A

`fit/` decides whether a piece of furniture fits in a room and says exactly
why when it does not. It is pure Python with no web, database or network
imports, so it runs unchanged inside an Argon or Carbon sandbox, and a test
reads the source on every build to keep it that way.

Solver version: **`fit/0.2.0`**. It appears in every report and in the command
line output. Quote it when you report a result.

---

## 1. Calling it from a sandbox

### The command line

```bash
python -m fit check --input case.json --output result.json
python -m fit check --input case.json            # result to stdout
cat case.json | python -m fit check --input -    # or from stdin
python -m fit version
```

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | A result of any kind, **including `does_not_fit` and `unknown`**. Those are answers, not failures |
| `2` | Malformed input. A JSON error goes to stderr and stdout stays empty |
| `1` | An unexpected internal failure, also as JSON |

A bad input never produces a traceback. You are usually an agent, and a stack
trace is neither actionable nor safe to feed back into a model, so every
input problem comes back as:

```json
{
  "error": {
    "code": "missing_field",
    "message": "room_spec is required",
    "field_path": "room_spec"
  },
  "solver_version": "fit/0.2.0"
}
```

Codes are `invalid_json`, `missing_field`, `invalid_field`, `input_not_found`
and `internal_error`.

### Getting it into the sandbox

```bash
python scripts/build_fit_package.py --verify
pip install dist/ethyneon_fit-0.2.0-py3-none-any.whl
```

That wheel carries `fit` and `contracts` and needs only `pydantic`. The
`--verify` flag installs it into a throwaway virtual environment and runs a
golden case through it, so the claim that it works standalone is checked
rather than asserted.

`pip install ./fit` also works and gives you `fit` alone, which is enough
inside this repository where `contracts` is already importable.

### In process

```python
from fit.engine import check_fit            # returns a FitReport
from fit.analysis import analyze_fit        # returns the report plus geometry

analysis = analyze_fit(room_spec, scene, placement)
analysis.report.result       # "fits" | "tight" | "does_not_fit" | "unknown"
analysis.geometry            # polygons for drawing
analysis.cause               # one sentence
analysis.as_dict()           # exactly what the command line writes
```

### What it imports

Standard library only, plus the shared models:

| Module | Imports |
| --- | --- |
| `fit/geometry.py` | `math`, `typing`. Nothing else at all |
| `fit/analysis.py` | `dataclasses`, `hashlib`, `typing`, `contracts.*` |
| `fit/engine.py` | `typing`, `contracts.*` |
| `fit/cli.py` | `argparse`, `json`, `pathlib`, `sys`, `pydantic`, `contracts.*` |

So the only third-party dependency is **pydantic**, reached through the
`contracts` models.

**If your sandbox has nothing but the standard library**, `fit/geometry.py`
is usable on its own today: it takes plain tuples and lists and has no
dependency of any kind. The rest needs pydantic. Two ways to remove even that
are possible, and neither has been done because it is a refactor that needs
Person C's approval:

1. Accept plain dicts at the engine boundary and validate with hand-written
   checks instead of pydantic models. Keeps one code path, costs the typed
   models inside the engine.
2. Ship a `fit.pure` entry point that parses the case JSON with hand-written
   validation and calls the same geometry. Keeps the typed path for the
   server, adds a second parser to maintain.

Say which you want and it is a small change. Until then, `pip install
pydantic` in the sandbox is the answer.

---

## 2. The input

One JSON object. Only `room_spec` and `placement` are required.

| Field | Type | Notes |
| --- | --- | --- |
| `room_spec` | `RoomSpec` | Required. The validated room |
| `placement` | object | Required. What you want to put down. See below |
| `placements` | list of `ObjectPlacement` | What is already in the room. Default empty |
| `scene` | `SceneRevision` | Instead of `placements`, if you have a whole scene |
| `offers` | list of `CatalogOffer` | Products, for dimensions and `dimension_source` |
| `product` | object | A simpler alternative to `offers`: `width_m`, `depth_m`, `height_m`, `dimension_source` |
| `options` | object | `clearance_margin_m`, `min_walkway_width_m`, `door_swing_steps` |

The `placement` object:

| Field | Type | Notes |
| --- | --- | --- |
| `object_id` | string | Required |
| `pose` | `{x_m, y_m, rotation_deg}` | Required |
| `footprint` | `{x_m, y_m, width_m, depth_m, rotation_deg}` | Optional if `offer_ref` or `product` gives the size |
| `offer_ref` | string | Matches an `offer_id` in `offers` |
| `replaces_object_id` | string | A swap does not collide with the thing it replaces |
| `category` | string | Turns on the extra checks: `chair` or `drawer_unit` |
| `pullback_m` | number | Seat pull-back distance, for `category: chair` |
| `drawer_extension_m` | number | Open-drawer depth, for `category: drawer_unit` |
| `facing_deg` | number | Which way the front points. Defaults to the pose rotation |

### A real example

This is `fixtures/fit_cases/does_not_fit_door_swing.json`, trimmed to its
input. A 4 by 4 metre room, a 0.9 m door on the south wall hinged left and
opening inward, and a desk dropped inside the door's arc.

```json
{
  "room_spec": {
    "room_revision": 1,
    "session_id": "sandbox",
    "dimensions_m": {
      "width_m":  {"value_m": 4.0, "evidence_status": "confirmed", "source": "live"},
      "depth_m":  {"value_m": 4.0, "evidence_status": "confirmed", "source": "live"},
      "height_m": {"value_m": 2.5, "evidence_status": "confirmed", "source": "live"}
    },
    "walls": [
      {"wall_id": "wall_n", "length_m": 4.0, "color": {"hex": "#cccccc", "evidence_status": "confirmed", "source": "generated"}},
      {"wall_id": "wall_e", "length_m": 4.0, "color": {"hex": "#cccccc", "evidence_status": "confirmed", "source": "generated"}},
      {"wall_id": "wall_s", "length_m": 4.0, "color": {"hex": "#cccccc", "evidence_status": "confirmed", "source": "generated"}},
      {"wall_id": "wall_w", "length_m": 4.0, "color": {"hex": "#cccccc", "evidence_status": "confirmed", "source": "generated"}}
    ],
    "floor": {"floor_id": "floor", "color": {"hex": "#cccccc", "evidence_status": "confirmed", "source": "generated"}},
    "openings": [
      {
        "opening_id": "door_main",
        "wall_id": "wall_s",
        "type": "door",
        "offset_m": 0.2,
        "width_m": 0.9,
        "height_m": 2.03,
        "hinge": {
          "hinge_side": "left",
          "swing_direction": "inward",
          "swing_angle_max_deg": 90.0,
          "evidence_status": "confirmed",
          "motion_evidence_ref": "clip_door_001"
        },
        "evidence_status": "confirmed"
      }
    ],
    "existing_items": [],
    "free_zones": [],
    "evidence_status": "confirmed",
    "source": "generated"
  },
  "placements": [],
  "placement": {
    "object_id": "obj_desk",
    "pose": {"x_m": 3.4, "y_m": 3.4, "rotation_deg": 0.0},
    "footprint": {"x_m": 3.4, "y_m": 3.4, "width_m": 1.0, "depth_m": 0.6, "rotation_deg": 0.0}
  }
}
```

---

## 3. The output

```json
{
  "fit_report": { ... the FitReport contract ... },
  "extra": {
    "geometry": { ... polygons for drawing ... },
    "extra_checks": [ ... chair pull-back, drawer extension ... ],
    "cause": "one sentence"
  },
  "solver_version": "fit/0.2.0"
}
```

### A real example

The output for the case above, abbreviated where it repeats:

```json
{
  "fit_report": {
    "report_id": "fit_2c1a...",
    "room_revision": 1,
    "scene_revision": 0,
    "solver_version": "fit/0.2.0",
    "result": "does_not_fit",
    "tentative": false,
    "assumptions": [
      "clearance margin 0.05 m",
      "minimum walkway width 0.75 m",
      "door swing tested at 7 intermediate angles",
      "room-local metres, walls traversed clockwise from wall_n"
    ],
    "checks_run": [
      {"name": "room_bounds", "result": "pass",
       "detail": "Nearest wall is 0.100 m away.", "measured_clearance_m": 0.1},
      {"name": "door_swing", "result": "fail",
       "detail": "The footprint is inside the swept area of door_main at 26 degrees.",
       "measured_clearance_m": 0.6, "blocking_ids": ["door_main"]}
    ],
    "minimum_clearance_m": 0.1,
    "rejected_placement": {
      "object_id": "obj_desk",
      "pose": {"x_m": 3.4, "y_m": 3.4, "rotation_deg": 0.0},
      "cause": "The footprint is inside the swept area of door_main at 26 degrees.",
      "failing_check": "door_swing"
    },
    "evidence": ["clip_door_001", "door_main"],
    "unknown_reasons": []
  },
  "extra": {
    "cause": "The open door would hit obj_desk by 0.60 m.",
    "extra_checks": [],
    "geometry": {
      "room": {"width_m": 4.0, "depth_m": 4.0},
      "footprint": [[2.9, 3.1], [3.9, 3.1], [3.9, 3.7], [2.9, 3.7]],
      "overlaps": [
        {"with": "door_main", "kind": "door_swing",
         "polygon": [[2.9891, 3.6095], [3.0964, 3.4389], [3.2389, 3.2964],
                     [3.4095, 3.1891], [3.5997, 3.1226], [3.8, 3.1],
                     [3.8, 3.7], [2.9575, 3.7]]}
      ],
      "door_swings": [
        {
          "opening_id": "door_main",
          "hinge": [3.8, 4.0],
          "max_angle_deg": 90.0,
          "swept_polygon": [[3.8, 4.0], [3.8, 3.1], "...9 points..."],
          "angles": [
            {"angle_deg": 0.0,   "leaf": [[3.8, 4.0], [3.8, 3.1]], "hits_placement": false},
            {"angle_deg": 12.86, "leaf": [[3.8, 4.0], [3.6, 3.12]], "hits_placement": false},
            {"angle_deg": 25.71, "leaf": [[3.8, 4.0], [3.41, 3.19]], "hits_placement": true}
          ]
        }
      ],
      "corridors": [],
      "minimum_clearance": {
        "metres": 0.1,
        "between": ["obj_desk", "wall_e"],
        "from_point": [3.9, 3.1],
        "to_point": [4.0, 3.1]
      }
    }
  },
  "solver_version": "fit/0.2.0"
}
```

### What is in `geometry`

| Key | What it is |
| --- | --- |
| `room` | `width_m`, `depth_m` |
| `footprint` | The proposed placement as a polygon |
| `overlaps` | One entry per conflict: `with`, `kind`, and the real intersection `polygon`. Not a bounding box |
| `door_swings` | Per door: the `hinge` point, the `swept_polygon`, and every angle tested with the leaf segment and whether it hits |
| `corridors` | Per walking route: the `polygon`, the `required_width_m`, a `profile` of free width along its length, and the `narrowest` sample |
| `extensions` | Chair pull-back and drawer regions, when checked |
| `minimum_clearance` | The gap in metres **and the two things it was measured between**, with both points |

All coordinates are room-local metres, in one system: the room runs from
`(0, 0)` to `(width_m, depth_m)`, walls are traversed clockwise from `wall_n`,
and an opening's offset is measured from the start of its wall.

### The extra checks

`extra_checks` carries the two PRD checks that have no name in the
`FitCheck` contract yet:

| Name | Needs | When the datum is missing |
| --- | --- | --- |
| `chair_pullback` | `category: chair` and `pullback_m` | `unknown`, naming `pullback_m` |
| `drawer_extension` | `category: drawer_unit` and `drawer_extension_m` | `unknown`, naming `drawer_extension_m` |

Both test the extension region against the room bounds, other objects, the
door swing and the walking route. When one fails, the overall verdict is
`does_not_fit`, and the failure also appears in `checks_run` under the
closest existing check name, because a pulled-back chair fouling the door
really is a `door_swing` conflict.

---

## 4. The two rules that matter most

**Missing data gives `unknown`, never a guess.** If a product has no
documented footprint, or a door has no hinge or no swing direction, the
result is `unknown` and `unknown_reasons` says exactly what was missing. The
engine will not assume a standard door width or a typical desk depth.

**A result built on estimated room size is tentative.** If the room width or
depth is still `estimated` rather than `confirmed`, or the product dimensions
were parsed out of listing text (`dimension_source:
listing_text_inferred`), then `tentative` is `true` and the reason is in
`assumptions`. A tentative `fits` is not a promise. Show it as such.

Same input gives byte-identical output, apart from `created_at`. That is what
lets Carbon independently reproduce what Argon proposed.

---

## 5. Golden cases

`fixtures/fit_cases/*.json`, fifteen of them, each with an `input` you can
feed straight to the CLI and an `expected` block. They cover `fits`, `tight`,
`does_not_fit` by door swing, by overlap with a kept item and by blocking the
route, `unknown` from missing dimensions, from a missing hinge and from a
missing swing direction, `tentative` from estimated scale and from inferred
dimensions, and the chair and drawer checks.

The expectations are written by hand, not captured from the engine, so they
say what the right answer is rather than what the code currently does.

```bash
python scripts/make_fit_cases.py           # regenerate them
pytest tests/fit -q                        # run them through the CLI
```

---

## 6. Proposed contract additions

These are **proposals**, not changes. Nothing in `contracts/` has been
touched. Person C needs to approve each before it is made, and each would go
in its own pull request with both other lane owners notified.

| # | Change | Why |
| --- | --- | --- |
| 1 | `FitCheck.name`: add `"chair_pullback"` and `"drawer_extension"` to the Literal | The two PRD checks have no honest name today and are reported under the closest existing one |
| 2 | `FitReport.geometry`: new optional field, a `FitGeometry` model | Lane B needs the overlap polygons and swept areas to draw the conflict, and today they ride outside the contract |
| 3 | `FitReport.summary`: new optional `str` | The one-sentence cause. `rejected_placement.cause` only exists for `does_not_fit`, so `tight` and `unknown` have nowhere to put it |
| 4 | `FitReport.minimum_clearance_between`: new optional `tuple[str, str]` | A bare number does not say what it was measured between |

Until they are approved, everything above travels in `extra` and the
`FitReport` stays exactly as it is.
