# Karishma's lane A demo

A self-contained copy of Karishma's front end running against her own backend,
in mock mode. It is here so her work is **runnable** rather than only credited,
and it is kept separate because it is a different system from the one the rest
of this repository builds.

Not part of the product. Nothing here is on the path to anybody's money.

## Run it

```powershell
# From this folder. Uses the root .venv; no extra dependencies.
cd demos\karishma-lane-a
..\..\.venv\Scripts\python -m uvicorn lane_a_demo.app:app --host 127.0.0.1 --port 8010
```

Then open <http://127.0.0.1:8010>.

Mock mode is the default: `lane_a_demo/__init__.py` sets `MOCK_AI=1` and
`ETHYNEON_FIXTURE_CATALOG=1` unless you have deliberately set them otherwise.
Her `shop.py` cannot reach Shopify without `SHOPIFY_STORE_DOMAIN` and
`SHOPIFY_STOREFRONT_TOKEN`, and nothing here sets them.

`GET /api/health` reports what is real:

```json
{"gemini": true, "mock_ai": true, "shopify_live": false,
 "voice_mode": "hydrogen", "fixture_catalog": true}
```

One caveat worth knowing: her `app.py` calls `load_dotenv()` at import time,
which is her code and is left alone. If the repository root has a populated
`.env`, that file will be read. Run this from a shell where you do not mind
that, or clear the variables first.

## What works

Verified by booting it, not by reading it:

| Screen | State |
| --- | --- |
| Page loads, three.js viewport, status pills | works |
| "Use preset room" builds a 3.2 by 3.0 m room with two openings | works |
| Voice and text commands route through her planner | works, labelled `MOCK_AI:` |
| Drag to move an item, with live fit feedback | works |
| Review and approve | works, against her fixture catalogue |
| Upload a room photo, upload a door clip | needs a Gemini key, untested here |

Every mocked value arrives prefixed `MOCK_AI:` in her own copy, which is the
behaviour that made it safe to demo in the first place.

## What this is, exactly

Her front end and her backend, as written, with one change: the imports.

| Here | Was | Why |
| --- | --- | --- |
| `lane_a_demo/` | `backend/` | Renamed so it cannot collide with or shadow the lane C `backend/` package at the repository root |
| `from ai.karishma import ...` | `from ai import ...` | Her AI modules live at `ai/karishma/` in this tree |
| `from ai.karishma._vendor.kschemas import ...` | `from contracts.schemas import ...` | Her contract models are vendored at `ai/karishma/_vendor/kschemas/`; ours have different field names |
| `from ai.karishma._vendor import solver` | `from fit import solver` | Her solver is vendored at `ai/karishma/_vendor/solver.py`, byte identical to her `fit/solver.py` |

Her module bodies, her routes, her front end: unchanged. Taken from
`karishma/full-build`.

## Why it is not the product front end

Two different systems, not one system with two skins.

Her backend exposes seventeen session-scoped routes
(`/api/session/{sid}/photo`, `/door`, `/events`, `/move`, `/undo`, `/preset`,
`/measure`, `/approve`, and more). The lane C backend exposes twelve differently
shaped ones (`/api/state`, `/api/command`, `/api/room/analyze`,
`/api/scene/patch`, `/api/cart/review`, and so on). Beyond `/api/health` and
`/api/session` they do not overlap, and four of hers have no equivalent at all.

More importantly, the two use different geometry. Her solver and our fit engine
disagree on wall traversal: `fit/geometry.py` walks clockwise, so `wall_s` runs
from `(W, D)` to `(0, D)`, while her solver runs it in `+x`. Run both over
`fixtures/fit_cases`, our engine matches the expected verdict on 15 of 15 and
her solver on 7, and every miss but one is a confident "fits" where ours returns
`does_not_fit` or `unknown`. Her solver has no walkway check, no drawer
extension and no chair pull-back.

So the product keeps one engine deciding fits, and this demo stays separate
rather than being wired into it. Her analysis work does run in the product: the
adapter at `ai/karishma_adapter/provider.py` puts her `analyze_room` behind our
contracts, and `/api/health` reports it as `real: karishma` when `MOCK_AI=0` and
a Gemini key is set.

`tests/ai/test_vendored_solver.py` is her own solver test suite, ported.
