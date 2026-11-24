# Ethyneon

Turn a photo of your room into an editable 3D room, shop verified Shopify
products into it, check whether they actually fit, and hand off to a hosted
test checkout.

**Status: lane C complete, money path verified live.** State and revisions,
the fit engine, the catalog layer, cart approval, the live cart write and the
checkout audit are built and tested. **1108 tests pass offline across the two
suites: 787 here and 321 in the Studio**, plus 17 live checks that are
deselected unless you ask for them.

The whole money path has run end to end on the development store: a real cart
created from an approved review, reconciled line by line, passed by the
deterministic checks and the Decision 1 audit, handed off to Shopify's hosted
checkout, and paid with the test gateway. Card 1 was approved and the order
appeared in Shopify Orders; card 2 was declined. The order total of $329.00
matched the 32900 integer minor units the approval carried exactly.

Catalog reads are live. **Cart writes are off by default** and need two gates
opened deliberately. Payment is never claimed from the handoff: the app still
reports `payment_unconfirmed` after a real order, because order lookup is off.

## Running lane C alone

Lane C is self-contained. It boots, serves every route and passes its whole
test suite with no teammate code and no API keys at all. Lanes A and B are
represented by labelled stubs, and `GET /api/health` reports
`lane_a_is_stub: true` so a stub can never be mistaken for the real thing.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt    # Windows
cp .env.example .env            # every mock on, no credentials needed
scripts/dev backend             # http://127.0.0.1:8000/docs
```

With no `.env` at all, every mock defaults to on and nothing reaches a
network. Add credentials only for what you want to make real:

| To make this real | Set | Effect |
| --- | --- | --- |
| Catalog reads | `MOCK_COMMERCE=0`, `SHOPIFY_STORE_DOMAIN`, `SHOPIFY_STOREFRONT_TOKEN` | Live products from the dev store |
| The checkout gate | `GOOGLE_API_KEY` | Real Decisions API; otherwise a labelled local stand-in answers |
| Discovery | `SHOP_MCP=1` | Live Shop MCP. Needs no credentials, and can never reach the cart |

Cart writes stay disabled regardless, so no live cart is created.

```bash
# The whole offline suite. No network, no keys, deterministic.
.venv/Scripts/python -m pytest tests -q

# Live checks. Skip rather than fail when a credential is missing.
.venv/Scripts/python -m pytest tests -m network -q

# Prove access to Shopify, Gemini, Managed Agents and Decisions.
# Prints no secret values.
.venv/Scripts/python scripts/check_access.py
.venv/Scripts/python scripts/check_access.py --only shopify

# One real Shop MCP search, printed raw.
.venv/Scripts/python scripts/check_shop_mcp.py "standing desk" --limit 3

# The gate that must pass before any pull request merges.
bash scripts/e2e_mock.sh
```

Lane A has landed, and startup now decides for itself. With `MOCK_AI=0` and
`GOOGLE_API_KEY` or `GEMINI_API_KEY` set, Karishma's adapter is installed;
anything less keeps the labelled stub, because a provider that cannot reach
its model is worse than a stub that admits what it is. The boot line says
which is on, and names no credential:

```
  lane A: karishma+ours, 1 of 7 functions real
  lane A: stub (MOCK_AI is on)
```

`/api/health` then reports each of the seven functions separately, so a
delegated call can never be demoed as an agent:

| function | with the real adapter installed |
| --- | --- |
| `analyze_room` | `real: karishma` |
| `validate_room_spec` | `delegated: ours (backend.validation)` |
| `run_checkout_gates` | `delegated: ours (backend.decisions)` |
| the other four | `delegated: stub (out of scope this phase)` |

To install a provider by hand, for example a different one:

```python
from backend.lane_a import set_lane_a
from ai.karishma_adapter.provider import RealLaneA

set_lane_a(RealLaneA())     # raises if any of the seven functions is missing
```

See [docs/LANE_C_HANDOFF.md](docs/LANE_C_HANDOFF.md) for every route, every
function, and how to swap each stub individually, and
[docs/LANE_A_ADAPTER.md](docs/LANE_A_ADAPTER.md) for what the adapter
translates.

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
.venv/Scripts/python -m pytest -q             # 787 offline tests, no network
.venv/Scripts/python -m pytest -m network -q  # 17 live checks, needs keys
.venv/Scripts/python scripts/secret_scan.py   # run before every commit
.venv/Scripts/python scripts/check_access.py  # prove access, prints no secrets
.venv/Scripts/python scripts/gen_schemas.py --check
cd web && npm run build                       # type-checks the generated types
```

The Studio has its own suite, in its own venv, run from its own folder:

```powershell
cd ethyneon-studio
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest                # 321 offline
```

The default run is offline and deterministic. Tests marked `network` are
deselected unless you ask for them, and they skip rather than fail when a
credential is missing.

`scripts/secret_scan.py` reads every tracked file and fails on anything
shaped like a credential, or on a tracked `.env`, database, cache, room
photograph or key file. It prints the file and the rule, never the matched
value. Run it before every commit.

The evaluation harness scores the room-photo-to-fit-verdict pipeline against
rooms somebody measured with a tape. It runs offline against the stub model
with no key:

```bash
.venv/Scripts/python -m eval.run_eval --offline
```

**It ships zero cases**, and prints a report of zeroes saying so. Writing
cases is the work; inventing them would make the numbers meaningless.
[eval/CASES.md](eval/CASES.md) says how to contribute one, and why every case
records its author and whether it came from inside or outside the team.

After changing any contract model, regenerate or the checks fail:

```bash
.venv/Scripts/python scripts/gen_schemas.py    # schemas + TypeScript
.venv/Scripts/python scripts/make_fixtures.py  # sample fixtures
```

## Mock flags

Set in `.env`. All four default to `1`, so a fresh clone runs fully offline.

| Flag | Current | When on | Owner |
| --- | --- | --- | --- |
| `MOCK_AI` | `1` | Lane A returns saved fixtures instead of calling Gemini or the agents | A |
| `MOCK_COMMERCE` | **`0`** | Catalog reads use a local fixture catalog of 17 offers instead of the dev store | C |
| `MOCK_ROOM_INPUT` | `1` | The frontend loads a saved photo, RoomSpec and door clip | B |
| `MOCK_AGORA` | `1` | Two browser tabs share state over the events socket, no video | B |

Catalog reads are currently **live** against the development store. Cart
writes are disabled at the client level regardless of any flag.

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

Shop MCP is now the Design view's product source, not just a discovery
surface beside it. The demo path is: measure a room, browse every
merchant, place a product as a **preview**, and buy it on that
merchant's own page. Browse results come from other merchants, are for
viewing only, and cannot be bought here.

A preview item is refused by `CheckoutStudio.review` before a cart line
exists, so it never reaches an approval, the purchase server or a
checkout link. Lane C's cart, audit and fit engine all stay in the
repository and are still tested; they are simply not the demo path.

The Product Studio has a mode switch beside its command box.
**Browse all Shopify** searches Shop MCP across every merchant, with the
`SHOPIFY_SHOPS` filter dropped so "all" means all.
Every browse card names its merchant and links to their listing, carries
no add, review or checkout control, and is refused by the scene and
checkout routes on the server, which ask the development store itself
rather than trusting the merchant a result claims. **Our store (buyable)**
is the Storefront API path and is unchanged.

The client allows exactly four read-only tools and blocks all twelve purchase
and payment tools before a request is even built. The endpoint, transport and
required headers are undocumented upstream, so the working recipe is written
down in [docs/SHOP_MCP_NOTES.md](docs/SHOP_MCP_NOTES.md).

## Layout

```
contracts/      twelve shared Pydantic models, one file each
  interfaces.py Protocol stubs for every call between lanes
  schemas/      JSON Schema, generated, never hand-edited
fixtures/       one sample per contract, plus failures/ and fit_cases/
  failures/     deliberately bad data, each saying where it must be caught
  fit_cases/    15 golden fit cases, one per outcome the contract names
ai/             lane A: the adapter, and Karishma's modules vendored
  karishma/     her code, unchanged, with her contracts under _vendor/
  karishma_adapter/  translates between her dialect and our contracts
web/            lane B: React and TypeScript shell. Health and contract types
                only; the 3D room and review card are not built here
backend/        lane C: FastAPI app, routes, Shopify, Shop MCP
fit/            lane C: pure geometry, no web, database or network imports
eval/           the room-photo-to-fit-verdict harness. No cases ship
tests/          contracts, ai, web, backend, fit
scripts/        dev, gen_schemas, make_fixtures, check_shop_mcp, e2e_mock,
                secret_scan
docs/           PRD v3.0, team integration contract, Shop MCP notes
demos/          runnable work that is not the product
  karishma-lane-a/  her front end on her backend, mocked, its own port
ethyneon-studio/  lane B: Mitran's Studio, its own venv (3.12) and suite
```

### How the pieces connect

```
  room photos ──► STUDIO :8001 ──► its own room, scene and voice pages
  (CC0 samples)        │
                       │  storefront.py searches the connected dev store
                       │  ethyneon_client.py asks this server about money
                       ▼
                 BACKEND :8000
                   contracts/   the shapes everything agrees on
                   fit/         the only thing that decides a fit
                   catalog.py   only a dev-store offer is shoppable
                   shop_mcp.py  discovery, allowlisted, never a cart
                   cart_write   two gates, off by default
                   checkout.py  link released only after the audit
                   revisions.py commit_revision, the only writer
                   ai/karishma_adapter  lane A, when a key is configured
```

The Studio renders rooms and products and decides nothing about money. Every
purchase question goes to this server over `ETHYNEON_API_BASE`, and with that
unset the Studio has no purchase path at all. It is a separate service with
its own `.env` and dependencies, and its own pytest suite:

```powershell
cd ethyneon-studio
.venv\Scripts\python -m pytest        # 321 offline
```

Its `pytest.ini` exists because both projects ship a top-level package named
`backend`; without it the suite imports the wrong one. Its README has the
two-port setup.

`demos/karishma-lane-a/` is not part of the product and is wired to nothing.
It runs Karishma's front end against her own backend in mock mode so her work
is runnable rather than only credited. Its README says what works.

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
| Cart writes | Verified live, then switched off. Needs `LIVE_CART_WRITES=1` and the store on `ALLOWED_CART_STORE_DOMAINS`. Run `scripts/live_cart_smoke.py` |
| Order lookup | `ENABLE_ORDER_LOOKUP=0` and no `SHOPIFY_ORDER_LOOKUP_TOKEN`, so payment stays `payment_unconfirmed` even after a real order. That is deliberate |
| Dev-store password | A development store shows a storefront password page in front of the hosted checkout. Sign in as the store owner in the same browser, or enter the store password |
| Inventory scope | The Storefront token lacks `unauthenticated_read_product_inventory`, so `quantityAvailable` is not requested. `availableForSale` still gates every line; the quantity check is skipped |
| Order lookup | `ENABLE_ORDER_LOOKUP=0`. Phase 4 needs a read-only order credential, separate from the Storefront token, which is not yet configured |
| Lane A | `ai/` holds only the verified model identifiers. `analyze_room`, the planner, the agents and ADK Live land in Phase 2; `MOCK_AI=1` serves A's fixtures |
| Lane B | `web/` is a Phase 0 shell. The 3D room, placement and activity panel land in Phase 3 |
| Decisions SDK | `google-genai` 2.33.0 has no `.decisions` client, and the guide's `>=3.2.0` is not published. Called over plain v1beta HTTP instead, verified working |
| ADK version | PRD specifies ADK 2.15; installed is 2.11.0, which is the newest on PyPI. `ManagedAgent`, graph workflows and Live all exist in it |
| Shop MCP dimensions | No structured dimension field exists. Sizes are parsed from listing text by plain code, labelled `inferred`, and cap fit at tentative |
| PRD divergence | PRD page 10 routes Shop MCP results into the Storefront cart. This build keeps discovery and purchase separate. See [docs/SHOP_MCP_NOTES.md](docs/SHOP_MCP_NOTES.md) |
| npm version | npm 9.6.4 with Node 24 triggers an engine warning. Harmless, but `npm install -g npm@latest` clears it |

## Demo day checklist, lane C

Default posture: catalog reads live, nothing writable.

```
MOCK_COMMERCE=0        # live catalog
LIVE_CART_WRITES=0     # no writes            <- the one that matters
ENABLE_ORDER_LOOKUP=0  # payment never claimed
```

Before the demo:

```bash
.venv/Scripts/python -m pytest -q                # 787 offline, no network
.venv/Scripts/python scripts/check_access.py     # prints no secrets
.venv/Scripts/python scripts/live_cart_smoke.py  # must say LIVE_CART_WRITES off
```

To run the live money path, open one flag in `.env`:

```
LIVE_CART_WRITES=1
```

then:

```bash
.venv/Scripts/python scripts/live_cart_smoke.py --yes-write-to-my-dev-store
```

Without the flag it rehearses and writes nothing. At the hosted checkout, a
development store shows its storefront password page first: sign in as the
store owner in the same browser, or enter the store password. Then pay with
the Shopify test gateway, **card 1 to approve** and **card 2 to decline**.

Afterwards the app reports `payment_unconfirmed` even though the order is
real and visible in Shopify Orders. That is correct: `order_confirmed`
requires `ENABLE_ORDER_LOOKUP=1` and a separate read-only credential.

**Set `LIVE_CART_WRITES` back to `0` when you are done, and check it took:**

```bash
.venv/Scripts/python scripts/live_cart_smoke.py | grep LIVE_CART_WRITES
# must print: off
```

Do not pipe the smoke script to a file. The checkout link is printed to the
terminal deliberately and belongs nowhere else.

See [docs/LANE_C_HANDOFF.md](docs/LANE_C_HANDOFF.md) section 11 for the full
version.

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
| Live cart write | One cart created, read back and reconciled against the approval |
| Hosted test checkout | Card 1 approved, order in Shopify Orders; card 2 declined |

## Event-written code and third-party licenses

Written during the Gemini 4 NYC Hackathon, November 2026.

### License

**The team has not chosen a license yet, so this code is not licensed for
reuse.** Default copyright applies: it is public to read, and nobody has
permission to copy, modify or redistribute it until we pick one and say so.
There is deliberately no `LICENSE` file rather than a placeholder that would
imply otherwise.

Third-party dependencies keep their own licenses, listed below.

| Component | License | Note |
| --- | --- | --- |
| All first-party code in this repo | **none chosen yet, see above** | Written at the event |
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
| Room photo sets in `ethyneon-studio/samples/rooms/` | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) | Cut from [Poly Haven](https://polyhaven.com/hdris) interior panoramas. Per-set source recorded in `manifest.json` |

No room photograph of anyone's actual home is committed. The sample sets
above are the only room imagery in the repository, and they are empty rooms
derived from CC0 panoramas. A photo a shopper uploads at runtime has its EXIF
and GPS stripped in the browser and is never written to this repository.
`fixtures/photos/README.md` explains why the two named fixture photos are
still absent.

### Third-party assets and licenses, lane B (`ethyneon-studio/`)

No browser library is vendored: each loads from a CDN at a pinned version. A
test reads every file in `backend/static/` and fails if any absolute URL
points somewhere not listed here.

| Asset | License | Note |
| --- | --- | --- |
| three.js 0.180.0, from jsDelivr | MIT | `three.module.js` and `examples/jsm` loaders |
| Agora RTC SDK NG 4.23.4, from jsDelivr | Agora SDK license, free tier | Voice only |
| `samples/rooms/`, 28 photo sets of 7 empty rooms | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) | Cut from [Poly Haven](https://polyhaven.com/hdris) interior panoramas by `scripts/make_room_samples.py`. `manifest.json` records each set's source URL, headings and licence. **Committed.** |
| Shopify product data, photos and copy | merchant-owned | Labelled discovery only. Never mirrored into the development store, never purchasable. |
| Inter, Helvetica Neue, SF Mono, Menlo | system fonts | Named in CSS stacks, not bundled |

**Generated 3D product models are deliberately not committed.** The 608
meshes and the alternatives catalogues that exist on `feature/mitran` are
built from other merchants' product photography and listing data, and this
repository records no licence covering them. `.gitignore` excludes
`samples/models/` and `samples/catalog/`, and a test asks git that nothing
outside `samples/rooms/` is tracked. Whoever generated them keeps them
locally; the Studio's model endpoint rebuilds them on demand.
