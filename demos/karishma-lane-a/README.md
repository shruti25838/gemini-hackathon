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

Mock mode is the default, and keeping it that way took more than a flag.

Her `app.py` calls `load_dotenv()` at import time. That is her code and is
left alone, but with no argument it searches upwards from the working
directory, finds the repository root's `.env`, and loads the real Shopify
credentials. Her `shop.py` then checks `live()` **before** `fixture_mode()`,
so a configured store beats the fixture catalogue. Started naively, this demo
talked to the real development store and reported `shopify_live: true`.

So `lane_a_demo/__init__.py` blanks `SHOPIFY_STORE_DOMAIN` and
`SHOPIFY_STOREFRONT_TOKEN` before her import runs. python-dotenv does not
override a variable that is already set, so that is what holds. It also sets
`MOCK_AI=1` and `ETHYNEON_FIXTURE_CATALOG=1`, those by `setdefault` so an
explicit choice still wins.

`GET /api/health` should report:

```json
{"gemini": true, "mock_ai": true, "shopify_live": false,
 "voice_mode": "hydrogen", "fixture_catalog": true}
```

If `shopify_live` is ever `true` here, the demo is on the real store and the
guard has failed. To do that deliberately, set `LANE_A_DEMO_LIVE_SHOPIFY=1`.

The repository root `.env` is still read for everything else, so run this
from a shell where you do not mind that.

## What works

Driven in a real browser on 127.0.0.1:8010, not read off the source.

| Screen | State |
| --- | --- |
| Page loads, three.js viewport, status pills | **works** |
| "Use preset room" builds a 3.2 by 3.0 m room with two openings | **works** |
| Setting the door by hand: hinge left or right, opens in or out | **works**, the swing renders in 3D and the room line reads `confirmed` |
| Text command routes through her intake, brief and planner | **works**, every value labelled `MOCK_AI:` |
| Activity panel shows each step and its provenance | **works** |
| Product search, products list, review and approve | **does not work here.** See below |
| Fit verdict on a placed product | **not reachable**, because no product can be placed |
| Upload a room photo, upload a door clip | **not working.** Needs a valid Gemini key |

### Why the product steps stop

The planner runs, reaches `discover_shopify_variants`, and the activity panel
shows:

```
workflow  inspect_room_motion failed: LaneANotWired: backend.shop.fetch_catalog
          is not wired. Use our CatalogService.search_catalog, which carries
          store, shoppable and dimension_source that her CatalogOffer has no
          field for.
recovery  Recovery chose 'pause' (mock): MOCK_AI: saved recovery choice
workflow  Paused. The last verified room and scene are unchanged.
```

That is `ai/karishma/shim.py` doing its job. The vendored copy of her code is
deliberately not wired to a catalogue, because the integration only connects
`analyze_room`, and a half-connected catalogue would let her `CatalogOffer`,
which has no `shoppable` or `store` field, reach a cart.

This demo points at that vendored copy rather than carrying a second copy of
her AI modules, so it inherits the shim. The trade is honest: the room, the
door and the planner are hers and run, and the product path refuses out loud
rather than pretending.

Her original branch does run the product path end to end against her fixture
catalogue, on `karishma/full-build`.

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
