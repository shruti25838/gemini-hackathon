# Lane C handoff

Person C owns commerce, the fit engine and the backend. This is everything
the other two lanes need in order to build against it, and everything C needs
back from them.

Current state: lane C is complete and runs alone. Lanes A and B are
represented by labelled stubs.

**The money path is verified live on the development store.** A real cart was
created from an approved review, reconciled line by line, passed the
deterministic checks and the Decision 1 audit, released its hosted checkout
link, and was paid with the Shopify test gateway: card 1 was approved and the
order appeared in Shopify Orders; card 2 was declined as expected. The order
total was $329.00, which is exactly the 32900 integer minor units the
approval carried, so the money arithmetic held all the way from the catalog
read to the Shopify order.

Cart writes are **off again by default**. Catalog reads stay live.

Nothing in `contracts/` or `interfaces.py` changes without the owner's
approval and a pull request of its own.

---

## 1. Routes C serves

All under `backend/app.py` unless noted. Every write carries `base_revision`
and a client-made `action_id` (UUID). Every failure returns
`{code, message, retryable, details}`.

| Method | Route | What it does | Status |
| --- | --- | --- | --- |
| GET | `/api/health` | Mock flags, commerce mode, whether lane A is stubbed | live |
| POST | `/api/session` | Creates a session, returns `session_id` and current revisions | live |
| POST | `/api/room/analyze` | Calls A's `analyze_room`, returns a draft plus its validation. **Commits nothing** | live, stubbed A |
| POST | `/api/room/confirm` | Validates and commits a new `room_revision` | live |
| GET | `/api/state` | Current RoomSpec, SceneRevision, CartReview and recent events. Takes `since_revision` | live |
| POST | `/api/command` | Calls A's `handle_command`, returns a proposal | live, stubbed A |
| POST | `/api/scene/patch` | Validates and commits a scene patch | live |
| POST | `/api/input/respond` | Answers a paused workflow | **501**, needs A |
| GET | `/api/catalog/search` | Shoppable store offers plus labelled discovery results | live |
| POST | `/api/cart/review` | Builds a `CartReview` from selected variants | live |
| POST | `/api/cart/confirm` | Re-reads price and stock, records the approval | live |
| POST | `/api/checkout/continue` | Reconciles, runs the Decision 1 audit, returns the URL only if both pass | live, no URL until cart writes are enabled |
| GET | `/api/agora/token` | B owns `backend/routes/agora.py`; C registers it | stub, labelled mock |
| WS | `/ws/events` | `ActivityEvent` stream and scene-revision notices | live |
| WS | `/ws/live` | A owns `backend/routes/live_ws.py`; C registers it | stub |

### Error codes you will see

| Code | HTTP | Meaning |
| --- | --- | --- |
| `stale_base_revision` | 409 | Your base is behind. `details.latest_revision` has the current one. Rebase and retry |
| `room_invalid` | 422 | The draft failed validation. `details.errors` is field-level. The previous room is untouched |
| `patch_rejected` | 422 | A scene action, ID or position was refused |
| `no_shoppable_lines` | 400 | Nothing selected can be bought |
| `cart_changed` | 409 | Price or stock moved. The approval was voided and `details.changes` says what moved |
| `not_approved` | 400 | Checkout needs an approved review |
| `consent_required` | 400 | A photo was sent before consent |
| `unknown_session` | 404 | No such session |
| `not_implemented_yet` | 501 | Scaffolded, lands in a later phase |

### Reconnect contract

The socket is a convenience, not the source of truth. Events are written to
SQLite **before** they are pushed, so after any drop:

```
GET /api/state?session_id=...&since_revision=<last scene revision you saw>
```

---

## 2. Functions C provides

Python, callable on the server. Signatures are in `contracts/interfaces.py`.

| Function | Module | Returns |
| --- | --- | --- |
| `search_catalog(requirements, limit)` | `backend/catalog.py` | `list[CatalogOffer]` |
| `get_offer(variant_id)` | `backend/catalog.py` | a fresh `CatalogOffer`, never cached |
| `check_fit(room_spec, scene, placement, offers)` | `fit/engine.py` | `FitReport` |
| `analyze_fit(...)` | `fit/analysis.py` | `FitAnalysis`: the report plus overlap polygons, door swept areas, corridor profiles, the measured clearance with its endpoints, and a one-sentence cause |
| `compute_budget(lines, budget)` | `backend/budget.py` | `BudgetResult`, integer minor units |
| `commit_revision(kind, patch, base_revision, action_id)` | `backend/revisions.py` | `CommitResult` or a conflict |
| `emit_event(activity_event)` | `backend/events.py` | writes to SQLite and pushes to `/ws/events` |
| `build_audit_inputs(cart_review)` | `backend/checkout.py` | the minimal evidence packet |
| `build_cart_review(...)` | `backend/cart.py` | `CartReview` |
| `confirm_cart(...)` | `backend/cart.py` | approved `CartReview` |
| `continue_checkout(...)` | `backend/checkout.py` | `(CheckoutAudit, checkout_url or None)` |
| `validate_room_spec(draft)` | `backend/validation.py` | `ValidationResult`. See the note in section 4 |

`fit/` imports nothing but the standard library and `contracts/`, so the same
code runs inside the Argon and Carbon sandboxes.
`tests/fit/test_no_forbidden_imports.py` reads the source and enforces that.

### The fit engine has its own guide and command line

Lane A should read **[docs/FIT_ENGINE.md](FIT_ENGINE.md)** rather than this
section. It has the input and output JSON shapes with worked examples, the
exit codes, the golden cases, and the rules about tentative and unknown.

```bash
python -m fit check --input case.json --output result.json
python -m fit check --input case.json            # result to stdout
cat case.json | python -m fit check --input -    # or from stdin
python -m fit version

python scripts/build_fit_package.py --verify     # standalone wheel for a sandbox
python scripts/make_fit_cases.py                 # regenerate the golden cases
```

Exit codes are `0` for a result of any kind including `does_not_fit` and
`unknown`, `2` for malformed input with a JSON error and no traceback, and
`1` for an internal failure.

The engine is installable on its own as `ethyneon-fit`. The wheel carries
`fit` and `contracts` and needs only pydantic;
`scripts/build_fit_package.py --verify` installs it into a throwaway virtual
environment and runs a golden case through it, so the claim is checked rather
than asserted. `fit/geometry.py` on its own has no dependency at all.

Four contract additions are proposed in section 6 of that document, covering
the two new check names, the geometry block, the plain-English cause and the
clearance endpoints. **None has been made**; everything extra travels outside
the `FitReport` until Person C approves them.

### Rules C enforces, which you cannot work around from another lane

- Only `commit_revision` writes state. Agents and models return proposals.
- A product reaches a scene or cart only with a verified `CatalogOffer` for
  that exact variant. Verified means all three: the configured store, that
  exact variant ID, and available for sale. A Shop MCP discovery variant and
  an out-of-stock variant are both refused at `POST /api/scene/patch` with
  `unverified_variant`.
- A Shop MCP discovery offer can never be shoppable. The `CatalogOffer` model
  itself refuses it, and `CartReview` refuses to be built on that surface.
- Money is integer minor units plus an ISO 4217 code. A mixed-currency cart
  is an error, not a conversion.
- A fit result is `unknown` whenever a dimension, hinge or swing direction is
  missing. Results built on estimated room scale or listing-text dimensions
  are `tentative`.
- Payment is never inferred from the checkout handoff.

---

## 3. Functions C expects from A and B

### From Person A, in `ai/`

| Function | Called by C from | Currently |
| --- | --- | --- |
| `analyze_room(image, session_id)` | `POST /api/room/analyze` | stub, fixture |
| `validate_room_spec(draft)` | before every room commit | **real**, C owns it |
| `handle_command(session_id, command, revisions)` | `POST /api/command` | stub, keyword router |
| `plan_workflow(brief, room_spec, scene)` | not yet called | stub, fixed plan |
| `run_workflow(plan_id)` | not yet called | stub, yields nothing |
| `run_checkout_gates(evidence_packet)` | `POST /api/checkout/continue` | **real**, Decisions API |
| `run_recovery(recovery_run)` | not yet called | stub, first allowed action |

### From Person B, in `web/`

B calls C over REST and the events socket; C calls nothing in `web/`. What C
needs from B:

- EXIF and GPS stripped in the browser **before** upload, and the consent
  screen shown first. `POST /api/room/analyze` refuses `consent_given: false`.
- `base_revision` and a fresh `action_id` UUID on every write.
- A reconnect through `GET /api/state?since_revision`, not a socket replay.
- Source labels rendered for every item: `live`, `cached`, `preset`,
  `generated`, `inferred`, `mock`. `web/src/components/SourceBadge.tsx` is the
  single place that styling is decided, and a mock must never look live.
- Regenerate types after any contract change: `python scripts/gen_schemas.py`.
- A stand-in is identified by `variant` being `null` with
  `asset_is_representative` `true`: it must show a not-for-sale label and no
  add-to-cart button, and a discovery (Shop MCP) product can never be placed
  in the scene at all and belongs only in a "similar elsewhere" link-out row.

B also owns `backend/routes/agora.py`. C registers it and does not edit it.

---

## 4. How to swap each A-facing stub

C never imports lane A's implementation directly. It asks the registry in
`backend/lane_a.py`, which holds a labelled stub until A ships.

### All seven at once

```python
# At startup, before the first request.
from backend.lane_a import set_lane_a
from ai.agents import RealLaneA        # Person A's module

set_lane_a(RealLaneA())
```

`set_lane_a` verifies the provider against all seven function names first and
raises `TypeError` listing what is missing, so a half-finished module fails
loudly at startup instead of leaving some calls quietly on the stub. Pass
`verify=False` only if you know why.

The provider needs no base class. Anything with the seven methods works.
`tests/backend/test_lane_a_seam.py` shows a complete example.

`GET /api/health` reports `lane_a` and `lane_a_is_stub`, so you can always see
which is installed.

### `run_checkout_gates`, separately

This one is already real, and it is injected rather than looked up, so it can
be replaced on its own:

```python
from backend.decisions import DecisionsClient
services.checkout.decisions = DecisionsClient(api_key)   # the live path
services.checkout.use_stand_in = False                   # no local fallback
```

With `use_stand_in=False` and no reachable Decisions API, checkout **pauses**
rather than passing. That is deliberate.

Behaviour today:

| Condition | What answers | Label on each answer |
| --- | --- | --- |
| API key configured | the real Decisions API at `v1beta/decisions` | `live` |
| no key, `use_stand_in=True` | a local rule-based stand-in | `local_stand_in` |
| no key, `use_stand_in=False` | nothing; the audit outcome is `unavailable` | none |

The stand-in derives its verdict from the deterministic facts in the evidence
packet. It never invents one, and its confidence is capped below 1.0 because
a rule is not a calibrated probability.

### `validate_room_spec`, a note

This is listed as A's in the integration contract, but C cannot commit a room
without it, so C wrote a real one in `backend/validation.py` rather than wait.
The stub delegates to it. If A ships their own, installing the provider
replaces it everywhere, including inside `commit_revision`.

C's validator checks: finite dimensions, supported room-size bounds, exactly
the four known walls, known opening types, openings inside their wall,
openings not taller than the room, and free zones inside the room. It also
preserves user-confirmed values against a later estimate.

### `handle_command`, a note

The stub recognises a wall colour change and nothing else. Everything else
comes back `needs_input`, which is the same shape A's real router must return.
It never emits an unregistered scene action.

---

## 5. Fixture files

One sample per contract, generated from the models by
`python scripts/make_fixtures.py`, so they cannot drift.

| File | Contract | Owner |
| --- | --- | --- |
| `fixtures/room_snapshot.sample.json` | `RoomSnapshot` | B |
| `fixtures/room_spec.sample.json` | `RoomSpec` | A |
| `fixtures/design_brief.sample.json` | `DesignBrief` | A |
| `fixtures/workflow_plan.sample.json` | `WorkflowPlan` | A |
| `fixtures/placement_review.sample.json` | `PlacementReview` | A |
| `fixtures/recovery_run.sample.json` | `RecoveryRun` | A |
| `fixtures/scene_revision.sample.json` | `SceneRevision` | B |
| `fixtures/activity_event.sample.json` | `ActivityEvent` | B |
| `fixtures/catalog_offer.sample.json` | `CatalogOffer` | C |
| `fixtures/fit_report.sample.json` | `FitReport` | C |
| `fixtures/cart_review.sample.json` | `CartReview` | C |
| `fixtures/checkout_audit.sample.json` | `CheckoutAudit` | C |

Mock-mode and supporting fixtures:

| File | Used for |
| --- | --- |
| `fixtures/catalog_offers.sample.json` | 17 offers for `MOCK_COMMERCE`, including missing-dimension, partial-footprint and out-of-stock cases |
| `fixtures/room_analysis.sample.json` | served by the `analyze_room` stub |
| `fixtures/command_result.sample.json` | a `CommandResult` sample |
| `fixtures/gate_answers.sample.json` | a `GateAnswers` sample |
| `fixtures/command.sample.json` | a `Command` sample, the shape B builds |
| `fixtures/fit_report_does_not_fit.sample.json` | a failing fit, for the replay |
| `fixtures/budget_lines.sample.json` | `BudgetLine` samples |
| `fixtures/failures/failure_cases.json` | ten deliberately bad samples, each saying where it must be caught |
| `fixtures/fit_cases/*.json` | 15 golden fit cases, each with an input the CLI takes and a hand-written expected block |
| `fixtures/photos/` | **not committed.** B supplies `room_good.jpg` and `room_poor.jpg`; see the README there |

---

## 6. Mock flags

Set in `.env`, which is git-ignored.

| Flag | Default | Effect when `1` | Owner |
| --- | --- | --- | --- |
| `MOCK_AI` | `1` | Lane A returns fixtures. The lane A stub is installed regardless until `set_lane_a` is called | A |
| `MOCK_COMMERCE` | **`0`** | Catalog reads come from `fixtures/catalog_offers.sample.json` instead of the store | C |
| `MOCK_ROOM_INPUT` | `1` | The frontend loads a saved photo and RoomSpec instead of capturing | B |
| `MOCK_AGORA` | `1` | Two browser tabs share state over the events socket, no video | B |
| `SHOP_MCP` | `1` | Shop MCP discovery is real. Independent of the four above, since discovery needs no credentials and cannot reach a cart | C |

`MOCK_COMMERCE=0` is the current setting and catalog reads are live. Turning
it on falls back to the fixture catalog, which is still validated by the same
tests as real data.

Rules: a mock returns data in the exact contract format, carries a visible
label, and never looks live.

---

## 7. Environment variables

Names only. Values live in `.env`, which is git-ignored and never committed.
`.env.example` lists every one of these with no values.

**Mock flags:** `MOCK_AI`, `MOCK_COMMERCE`, `MOCK_ROOM_INPUT`, `MOCK_AGORA`

**Gemini and ADK:** `GOOGLE_API_KEY`, `GEMINI_API_KEY`,
`GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`,
`ADK_MODEL_ROOM`, `ADK_MODEL_CATALOG`, `ADK_MODEL_LIVE`, `ADK_MODEL_DECISION`,
`MANAGED_AGENT_ARGON`, `MANAGED_AGENT_CARBON`, `AGENT_TOKEN_BUDGET_PER_RUN`

**Decisions API:** `GEMINI_DECISIONS_BASE_URL`, `GEMINI_DECISIONS_MODEL`

**Shopify Storefront:** `SHOPIFY_STORE_DOMAIN`, `SHOPIFY_STOREFRONT_TOKEN`,
`SHOPIFY_API_VERSION`, `SHOPIFY_DIMENSION_METAFIELD_NAMESPACE`,
`SHOPIFY_DIMENSION_METAFIELD_WIDTH`, `SHOPIFY_DIMENSION_METAFIELD_DEPTH`,
`SHOPIFY_DIMENSION_METAFIELD_HEIGHT`

**Live cart writes:** `LIVE_CART_WRITES`, `ALLOWED_CART_STORE_DOMAINS`

**Order confirmation:** `ENABLE_ORDER_LOOKUP`, `SHOPIFY_ORDER_LOOKUP_TOKEN`

**Shop MCP:** `SHOP_MCP`, `SHOP_MCP_ENDPOINT`, `SHOP_MCP_PROTOCOL_VERSION`,
`SHOP_MCP_TIMEOUT_SECONDS`, `SHOP_MCP_CACHE_TTL_MS_OVERRIDE`

**Agora:** `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE`, `AGORA_TOKEN_TTL_SECONDS`

**Backend:** `ETHYNEON_DB_PATH`, `ETHYNEON_HOST`, `ETHYNEON_PORT`,
`ETHYNEON_LOG_LEVEL`, `ETHYNEON_CORS_ORIGINS`, `APPROVAL_TTL_SECONDS`,
`CHECKOUT_AUDIT_TTL_SECONDS`

**Fit engine:** `FIT_CLEARANCE_MARGIN_M`, `FIT_MIN_WALKWAY_WIDTH_M`,
`FIT_DOOR_SWING_STEPS`

Nothing secret reaches `web/`. The browser talks to the backend, which holds
the credentials. `GOOGLE_API_KEY` wins over `GEMINI_API_KEY` when both are set.

---

## 8. Merge checklist

In the integration doc's order: contracts, backend core, AI, frontend.

1. `pytest tests/contracts` passes. Every fixture validates.
2. `python scripts/gen_schemas.py --check` passes. Schemas and TypeScript
   match the models.
3. `bash scripts/e2e_mock.sh` passes.
4. `python scripts/check_access.py` passes for whoever owns the credential.
5. `cd web && npm run build` type-checks against the generated types.

A contract change goes in its own pull request, never mixed with feature
code. Adding a field needs one approval; renaming or removing one needs both
other owners.

---

## 9. Known gaps C is handing over

| Gap | Detail |
| --- | --- |
| Cart writes | Verified live, then switched off. Needs `LIVE_CART_WRITES=1` **and** the store on `ALLOWED_CART_STORE_DOMAINS`. See section 10 |
| Inventory scope | The Storefront token lacks `unauthenticated_read_product_inventory`, so `quantityAvailable` is not requested. `availableForSale` still gates every line |
| Order lookup | `ENABLE_ORDER_LOOKUP=0` and `SHOPIFY_ORDER_LOOKUP_TOKEN` unset. Until both are set, payment stays `payment_unconfirmed` and the refusal says why |
| `/api/input/respond` | Returns 501. It needs A's resumable workflow |
| Decisions SDK | `google-genai` 2.33.0 has no `.decisions` client and the guide's `>=3.2.0` is not published, so it is called over plain v1beta HTTP |
| Room photos | `fixtures/photos/` is empty. B supplies the two real images |


---

## 10. Live cart writes

The only code in this project that mutates a real store is
`backend/cart_write.py`, reached through `CartService.write_cart_once`.

Two gates. One place decides: the Storefront client's own
`allow_cart_writes` is derived from `settings.cart_writes_allowed`, so the
client and the policy cannot disagree. They are also re-checked at the moment
of the write, not only at construction:

```
LIVE_CART_WRITES=1
SHOPIFY_STORE_DOMAIN is listed in ALLOWED_CART_STORE_DOMAINS
```

Either one off raises `live_cart_writes_refused` (HTTP 403) in the standard
error envelope. Both default to off, so a fresh clone cannot write anywhere.

The sequence:

1. The pending cart reference is persisted with `write_outcome: unknown`
   **before** the mutation leaves. If the process dies mid-write, the next
   attempt finds `unknown` and reads back instead of creating a second cart.
2. `cartCreate` with the exact approved variant IDs and quantities.
3. The cart is read back from Shopify. The mutation response is never
   trusted on its own.
4. If the read-back is short of approved lines, `cartLinesAdd` adds exactly
   the shortfall. A cart that already matches is never topped up.
5. Reconciliation against the approval: variant IDs, quantities, currency,
   and the subtotal in integer minor units. A mismatch sets `failed` and
   `needs_review`, and no link is released.

The hosted checkout URL is stored server side on `CartRef.checkout_url` and
released only by `continue_checkout`, and only when all of these hold: the
audit is for this exact approval fingerprint, the cart fingerprint still
matches what was audited, and the audit has not expired. A cart edited after
the audit releases nothing and forces a new audit.

Payment is never inferred from the handoff. `order_confirmed` needs
`ENABLE_ORDER_LOOKUP=1` and a `SHOPIFY_ORDER_LOOKUP_TOKEN` that differs from
the Storefront token. Without both, the state stays `payment_unconfirmed`.

This was confirmed in the live run and is worth being precise about: the
order really was placed and really is visible in Shopify Orders, and the app
still reports `payment_unconfirmed`. That is correct, not a bug. The app
does not claim a payment it has not independently verified, and order lookup
is off.

### Verified live

| Step | Result |
| --- | --- |
| Cart created on the dev store | one cart, one `cartCreate`, read back and reconciled |
| Deterministic checks | all passed |
| Decision 1 audit | `ready` |
| Hosted checkout link released | yes, bound to that approval and cart |
| Test card 1 | approved; order visible in Shopify Orders; total $329.00 = 32900 minor units, matching the approval |
| Test card 2 | declined, as expected |
| App payment state afterwards | `payment_unconfirmed`, because order lookup is off |

### The dev-store password page

A Shopify development store puts a storefront password page in front of
everything, including the hosted checkout. Opening the checkout link in a
fresh browser shows that page rather than the checkout. Either sign in as the
store owner in the same browser, or enter the store password. This is a
property of development stores, not of the checkout link, and it will not
appear on a live store.

**The checkout link is withheld from `GET /api/state`.** It is a bearer link
to a hosted checkout, and `/api/state` is a broad read, so the field comes
back as `null` there even once a cart exists. The contract is unchanged, since
`checkout_url` is optional, and a client can still tell "no cart" from "cart
exists, link withheld". The link is stored server side and released only by
`POST /api/checkout/continue`, after an audit bound to that exact approval and
cart. It is never logged and never appears in an event.


---

## 11. Demo day checklist, lane C

Everything here is lane C only. Three minutes, and the gates stay shut until
the moment they are needed.

### Before the demo

| Check | Expected | Command |
| --- | --- | --- |
| Offline suite is green | 444 passed | `.venv/Scripts/python -m pytest tests -q` |
| Access to the real services | 21 pass, 1 known SDK gap | `.venv/Scripts/python scripts/check_access.py` |
| Catalog reads are live | `commerce: live` | `curl -s localhost:8000/api/health` |
| Cart writes are OFF | `LIVE_CART_WRITES is off` | `.venv/Scripts/python scripts/live_cart_smoke.py` |
| Shop MCP is reachable | 22 tools, 4 allowlisted | `.venv/Scripts/python scripts/check_shop_mcp.py "desk" --limit 3` |

Default posture: `LIVE_CART_WRITES=0`, `ENABLE_ORDER_LOOKUP=0`,
`MOCK_COMMERCE=0`. Catalog reads real, nothing writable.

### To run the live money path

One flag, and only this one:

```
LIVE_CART_WRITES=1          # in .env
```

`ALLOWED_CART_STORE_DOMAINS` already defaults to the dev store, so leave it
alone. Then:

```
.venv/Scripts/python scripts/live_cart_smoke.py --yes-write-to-my-dev-store
```

Without `--yes-write-to-my-dev-store` it rehearses and writes nothing. Use
`--query "monitor"` to pick a different product.

**Set `LIVE_CART_WRITES` back to `0` the moment you are done.** Check it took
effect, because a `.env` edit that does not save is easy to miss:

```
.venv/Scripts/python scripts/live_cart_smoke.py | grep LIVE_CART_WRITES
```

It must print `off`.

### At the hosted checkout

| Thing | Value |
| --- | --- |
| Storefront password page | Expected on a dev store. Sign in as the store owner in the same browser, or enter the store password |
| Approved payment | Shopify test gateway **card 1** |
| Declined payment | Shopify test gateway **card 2** |
| What the app shows afterwards | `payment_unconfirmed`, by design |

The app never sees the card. Payment details go to Shopify's hosted page.

### If a judge asks "did it really charge?"

It really placed an order on the development store in test mode, and the
order is in Shopify Orders. The app deliberately does not say "paid":
`order_confirmed` requires `ENABLE_ORDER_LOOKUP=1` and a separate read-only
credential, neither of which is set. Showing `payment_unconfirmed` next to a
real order is the honest answer, and it is the behaviour the tests pin.

### Do not do these

- Do not pipe the smoke script's output to a file. The checkout link is
  printed to the terminal on purpose and belongs nowhere else.
- Do not enable cart writes against any store other than the dev store. The
  allowlist refuses it, but do not rely on that alone.
- Do not claim a payment the app has not confirmed.
