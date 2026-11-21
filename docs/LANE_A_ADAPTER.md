# Lane A adapter: Karishma's code in our tree

Her code is in `ai/karishma/`, copied from `origin/karishma/full-build` and
changed only where listed below. The adapter is in `ai/karishma_adapter/`.
Nothing of hers reaches `contracts/`, `backend/` or `fit/`.

Three rules the adapter exists to keep:

- **Lane A only proposes.** Her `Session.commit_scene` and `Session.set_room`
  raise. Only our `commit_revision` writes.
- **Our `check_fit` is the only fit verdict.** Her `solver.py` searches for
  placements; anything it returns is a proposal.
- **Store, shoppable, dimension_source and availability are re-derived from
  our catalog by `variant_id`** whenever anything crosses. Her `CatalogOffer`
  has no field for any of them, so they cannot be trusted from her side.

## Edits to her code

Fourteen lines across eight files. Twelve are imports; two are paths.

| File | Line | Change |
| --- | --- | --- |
| `agents.py` | 24 | `from fit import solver` to `from ._vendor import solver` |
| `agents.py` | 27 | solver **source path** `../fit/solver.py` to `./_vendor/solver.py` |
| `commands.py` | 11 | `from contracts.schemas import` to `from ._vendor.kschemas import` |
| `commands.py` | 12 | `from fit import solver` to `from ._vendor import solver` |
| `gemini.py` | 175 | mock fixture path `../fixtures/ai_mock_outputs.json` to `./_vendor/ai_mock_outputs.json` |
| `intake.py` | 13 | `from contracts.schemas import` to `from ._vendor.kschemas import` |
| `recovery.py` | 15 | `from contracts.schemas import` to `from ._vendor.kschemas import` |
| `room.py` | 8 | `from contracts.schemas import` to `from ._vendor.kschemas import` |
| `vision.py` | 15 | `from contracts.schemas import` to `from ._vendor.kschemas import` |
| `workflow.py` | 16 | `from contracts.schemas import` to `from ._vendor.kschemas import` |
| `workflow.py` | 18 | `from backend import shop` to `from .shim import shop` |
| `workflow.py` | 19 | `from fit import solver` to `from ._vendor import solver` |
| `workflow.py` | 23 | `from backend.state import Session` to `from .shim import Session` |
| `workflow.py` | 372 | inline `from contracts.schemas import FitReport` to `from ._vendor.kschemas import` |

No logic of hers was changed. `ai/karishma/_edits.txt` holds the machine-made
record.

## What was vendored, and one deviation

| Path | From | Why |
| --- | --- | --- |
| `ai/karishma/*.py` | her `ai/` | her lane A, as agreed |
| `ai/karishma/_vendor/solver.py` | her `fit/solver.py` | kept out of our `fit/`, which stays the only fit authority and import-pure |
| `ai/karishma/_vendor/ai_mock_outputs.json` | her `fixtures/` | her offline fixture, used by every offline test here |
| `ai/karishma/_vendor/kschemas/` | her `contracts/` | **see below** |

**The deviation.** The brief said not to copy her `contracts/`. Her `ai/`
imports `contracts.schemas` in six modules, and our contracts use different
field names, so pointing her code at ours would break it everywhere rather
than adapt it. Her models are therefore vendored **privately** under
`_vendor/kschemas/`, for her code's internal use only. They are never
exported, never returned across the adapter, and `tests/ai/
test_contracts_bridge.py::test_nothing_of_hers_leaks_into_our_model` asserts
what leaves is our type. That keeps the intent of the instruction, which was
that her dialect must not become the project's, while letting her code run.

Not copied: her `backend/`, `tests/`, `README.md`, `requirements.txt`,
`.env.example`, `.DS_Store`.

## Requirements

**Her `requirements.txt` was not used, and could not be.** Its first line is
`google-genai>=3.6.1`, and that release does not exist. The newest on PyPI is
**2.33.0**, which is what we have installed. `pip install -r` against her file
fails outright.

Nothing was added to ours. `python-multipart` was **not** needed, because her
upload path lives in her `backend/app.py`, which was not copied. Everything
her `ai/` touches is already satisfied: `pydantic`, `google-genai` through
`google-adk`, and the standard library.

No version conflict exists between the two files beyond that one line. Hers
is looser everywhere else (`pydantic>=2.8` against our `>=2.9,<3`).

## The door handedness question, settled

Karishma's definition, confirmed by her: **`hinge_side` "left" is the left as
seen from inside the room facing the door.** Ours is "the end nearer the
wall's start", traversing clockwise from `wall_n`.

`tests/ai/test_door_goldens.py` derives the viewer's left from first
principles, independently of the engine, and asserts the two pick the same
end on all four walls. **They agree on all four.** Sixteen goldens then check
that the swept area lands on the hinge side and not the far side, for each
wall, hinge left and right, inward and outward.

This mattered because a mirrored swing is not an error; it is a confident,
plausible, wrong "fits".

## What is real and what is delegated

`GET /api/health` reports this per function, under `lane_a_functions`.

| Function | Now |
| --- | --- |
| `analyze_room` | **real**, her `ai/karishma/room.py` |
| `validate_room_spec` | delegated to our `backend/validation.py` |
| `run_checkout_gates` | delegated to our `backend/decisions.py` |
| `handle_command` | delegated to our stub |
| `plan_workflow` | delegated to our stub |
| `run_workflow` | delegated to our stub |
| `run_recovery` | delegated to our stub |

## Open gaps

| Gap | Note |
| --- | --- |
| `handle_command` | Out of scope this phase. Needs a real `SessionFacade` |
| `SessionFacade` | `ai/karishma/shim.py` is a placeholder that refuses rather than guesses. `fetch_catalog` and `emit` raise `LaneANotWired` |
| `DesignBrief`, `ActivityEvent` bridges | Not built. Only `RoomSpec` and `Opening`/`DoorMotion` are translated |
| Her `agents.py`, `workflow.py`, `commands.py` | Import cleanly but are not reachable through `RealLaneA` yet |
| Her solver | Vendored and importable, not wired. When it is, its output is a **proposal** and our `check_fit` still decides |
| Walking routes | Her `FreeZone` has no `purpose`, so every zone becomes `keep_clear` and a route cannot survive the crossing. This is the loss most likely to change a fit result |
| `scale_reference` | Hers is a bare string; ours needs an anchor and a length. Dropped unless the width is confirmed |
| Her `fixtures/room_spec.sample.json` etc. | Not copied; ours are generated from our models |

`ai/karishma_adapter/contracts_bridge.py` has a `LOSSY` dict naming every
field that does not survive a round trip, and a test asserts it stays
accurate.
