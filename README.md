# Ethyneon

Turn a photo of your room into an editable 3D room, shop verified Shopify
products into it, check whether they actually fit, and hand off to a hosted
test checkout.

**Status: Phase 1, lane C core.** State and revisions, the fit engine, the
catalog layer, cart approval and the checkout audit are built and tested.
383 tests pass: 366 offline and 17 against live services.

Catalog reads work against the real development store. **Cart writes are
disabled**, so no live cart is ever created; the write path exists and is
tested with a fake transport, and enabling it is a constructor flag.

## Setup

Needs Python 3.10 or newer and Node 20 or newer.

```bash
# Python
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt    # Windows
.venv/bin/python -m pip install -r requirements.txt        # macOS, Linux

# Web
cd web && npm install && cd ..

# Config. Everything stays mocked until you fill this in.
cp .env.example .env
```

## Run

```bash
scripts/dev              # backend on :8000, web on :5173
scripts/dev backend      # backend only
scripts/dev web          # web only
```

API docs at <http://127.0.0.1:8000/docs>. Health, including which mocks are
on, at <http://127.0.0.1:8000/api/health>.

## Checks

```bash
bash scripts/e2e_mock.sh                      # the gate for every pull request
.venv/Scripts/python -m pytest tests -q       # 366 offline tests, no network
.venv/Scripts/python -m pytest tests -m network -q   # 17 live checks, needs keys
.venv/Scripts/python scripts/check_access.py  # prove access, prints no secrets
.venv/Scripts/python scripts/gen_schemas.py --check
cd web && npm run build                       # type-checks the generated types
```

The default run is offline and deterministic. Tests marked `network` are
deselected unless you ask for them, and they skip rather than fail when a
credential is missing.

After changing any contract model, regenerate or the checks fail:

```bash
.venv/Scripts/python scripts/gen_schemas.py    # schemas + TypeScript
.venv/Scripts/python scripts/make_fixtures.py  # sample fixtures
```

## Mock flags

Set in `.env`. All four default to `1`, so a fresh clone runs fully offline.

| Flag | When on | Owner |
| --- | --- | --- |
| `MOCK_AI` | Lane A returns saved fixtures instead of calling Gemini or the agents | A |
| `MOCK_COMMERCE` | The Shopify cart path reads a local fixture catalog of 17 offers | C |
| `MOCK_ROOM_INPUT` | The frontend loads a saved photo, RoomSpec and door clip | B |
| `MOCK_AGORA` | Two browser tabs share state over the events socket, no video | B |

Rules that are not negotiable:

- A mock returns data in the exact contract format and is validated by the
  same tests as real data.
- A mock result carries a visible label and **never looks live**.
- `SHOP_MCP` is separate from these four. Shop MCP discovery is real by
  default even while `MOCK_COMMERCE` is on, because discovery needs no
  credentials and cannot reach a cart.

## Shop MCP

Real, live, and working. Try it:

```bash
.venv/Scripts/python scripts/check_shop_mcp.py "standing desk" --limit 3
```

Shop MCP is a **discovery surface only**. Its results belong to other
merchants and can never reach a cart: `CatalogOffer` refuses `shoppable:
true` on that surface at the model level, and `CartReview` refuses to be built
on it at all. The purchasable path is the configured development store through
the Storefront API and its hosted test checkout.

The client allows exactly four read-only tools and blocks all twelve purchase
and payment tools before a request is even built. The endpoint, transport and
required headers are undocumented upstream, so the working recipe is written
down in [docs/SHOP_MCP_NOTES.md](docs/SHOP_MCP_NOTES.md).

## Layout

```
contracts/      twelve shared Pydantic models, one file each
  interfaces.py Protocol stubs for every call between lanes
  schemas/      JSON Schema, generated, never hand-edited
fixtures/       one sample per contract, plus failures/
  failures/     deliberately bad data, each saying where it must be caught
ai/             lane A: agents, gates, planner, live guide
web/            lane B: React, TypeScript, Three.js
  src/types/    TypeScript contract types, generated
backend/        lane C: FastAPI app, routes, Shopify, Shop MCP
fit/            lane C: pure geometry, no web, database or network imports
tests/          contracts, ai, web, backend, fit
scripts/        dev, gen_schemas, make_fixtures, check_shop_mcp, e2e_mock
docs/           PRD v3.0, team integration contract, Shop MCP notes
```

`fit/` sits outside `backend/` on purpose: the same code runs on the server
and inside the Argon and Carbon sandboxes, so it may not import anything that
needs a network or a database. A test reads the source and enforces that.

## Conventions

| Topic | Rule |
| --- | --- |
| Length | Metres, as numbers. `3.2`, never `"3.2 m"` |
| Coordinates | One room-local system in metres. An opening's offset is measured from the start of its wall |
| Wall IDs | `wall_n`, `wall_e`, `wall_s`, `wall_w`, set once and never renamed |
| Money | Integer minor units plus an ISO 4217 code. `120000` + `USD` is $1,200.00. Never floats |
| Shopify IDs | Full ID strings as the API returns them. Product ID and variant ID are separate fields |
| Revisions | Integers that only increase. Undo creates a new revision copying an old one |
| Writes | Every write carries `base_revision` and a client `action_id` (UUID). A stale base returns 409 with the latest revision. A repeated `action_id` returns the first result |
| Timestamps | ISO 8601, UTC |
| Evidence status | `estimated`, `confirmed`, `unknown`, `sampled_from_photo`. User-confirmed values are never overwritten by later estimates |
| Fit result | `fits`, `tight`, `does_not_fit`, `unknown` |
| Errors | Every failure returns `{code, message, retryable, details}` |
| Source labels | Anything shown carries `live`, `cached`, `preset`, `generated`, `inferred` or `mock` |

## Safety

- Secrets live in `.env` and server config only. `.env` is git-ignored and
  `.env.example` lists every variable with no values. Nothing secret reaches
  `web/`; the browser talks to the backend, which holds the credentials.
- Payment details never touch this app. Checkout is hosted by Shopify, on a
  development store in test mode.
- Payment is never inferred from the handoff. `order_confirmed` comes only
  from a read-only order lookup or a verified Shopify order event, behind
  `ENABLE_ORDER_LOOKUP`, using a credential separate from the Storefront
  token. Card data is never logged or stored.
- Product text is untrusted. HTML is stripped and the text only ever appears
  inside a delimited data block, never as an instruction. Every action surface
  is a closed allowlist, so text asking for an action cannot produce one.

## Known gaps

| Gap | Detail |
| --- | --- |
| Cart writes | Disabled. No live Shopify cart is created, so no hosted checkout URL is produced yet. The path is written and tested with a fake transport |
| Inventory scope | The Storefront token lacks `unauthenticated_read_product_inventory`, so `quantityAvailable` is not requested. `availableForSale` still gates every line; the quantity check is skipped |
| Order lookup | `ENABLE_ORDER_LOOKUP=0`. Phase 4 needs a read-only order credential, separate from the Storefront token, which is not yet configured |
| Lane A | `ai/` holds only the verified model identifiers. `analyze_room`, the planner, the agents and ADK Live land in Phase 2; `MOCK_AI=1` serves A's fixtures |
| Lane B | `web/` is a Phase 0 shell. The 3D room, placement and activity panel land in Phase 3 |
| Decisions SDK | `google-genai` 2.33.0 has no `.decisions` client, and the guide's `>=3.2.0` is not published. Called over plain v1beta HTTP instead, verified working |
| ADK version | PRD specifies ADK 2.15; installed is 2.11.0, which is the newest on PyPI. `ManagedAgent`, graph workflows and Live all exist in it |
| Shop MCP dimensions | No structured dimension field exists. Sizes are parsed from listing text by plain code, labelled `inferred`, and cap fit at tentative |
| PRD divergence | PRD page 10 routes Shop MCP results into the Storefront cart. This build keeps discovery and purchase separate. See [docs/SHOP_MCP_NOTES.md](docs/SHOP_MCP_NOTES.md) |
| npm version | npm 9.6.4 with Node 24 triggers an engine warning. Harmless, but `npm install -g npm@latest` clears it |

### Verified access, as of 21 November 2026

| Item | Result |
| --- | --- |
| Shopify Storefront token | Confirmed a Storefront token, `Shopify-Storefront-Private-Token` header |
| Store catalog | 37 products visible, 15 Shopify samples excluded, 22 seeded furniture |
| Dimension metafields | 21 products expose all three of `custom.width/depth/height` |
| Gemini models | All six PRD identifiers available: `gemini-4-neon`, `gemini-4-hydrogen`, `gemini-4-argon`, `gemini-4-carbon`, `gemini-decision-1`, `gemini-3.8-live` |
| Decisions API | Live at `v1beta/decisions`; `noul`, `choice` and `score` confirmed |
| Managed Agents | Reachable on this account |
| Shop MCP | Live, 22 tools, 4 allowlisted |

## Event-written code and third-party licenses

Written during the Gemini 4 NYC Hackathon, November 2026.

| Component | License | Note |
| --- | --- | --- |
| All first-party code in this repo | to be chosen | Written at the event |
| FastAPI, Starlette, Uvicorn | MIT / BSD-3 | |
| Pydantic, pydantic-settings | MIT | |
| httpx, websockets, aiosqlite, python-dotenv | BSD-3 / MIT | |
| google-adk, google-genai | Apache-2.0 | |
| pytest, pytest-asyncio, ruff | MIT | |
| React, React DOM | MIT | |
| Vite, @vitejs/plugin-react, oxlint | MIT | |
| TypeScript | Apache-2.0 | |
| three, @react-three/fiber, @react-three/drei | MIT | |
| Product data from Shop MCP | merchant-owned | Displayed as labelled discovery, never copied into the dev store |
| 3D product assets | to be recorded in Phase 3 | Stand-in geometry, labelled representative on screen |
| Room photos | supplied at the event | Consent shown before analysis; EXIF and GPS stripped in the browser |

To be completed as lanes A and B land.
