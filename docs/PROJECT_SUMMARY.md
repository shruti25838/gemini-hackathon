# Ethyneon

Built at the Gemini 4 NYC Hackathon, November 2026.

## The problem

You can see a desk you like online. You cannot see whether it fits.

Furniture listings give you dimensions and leave the geometry to you, so
people buy with a tape measure in one hand and a guess in the other. The
measurement that actually catches the problem is almost never the one on the
listing. It is the door. A desk that fits the wall perfectly can still stop
the door opening, and nothing in a normal shopping flow will tell you that
until the thing is in your hallway.

Returns on large furniture are expensive and miserable for everyone involved.

## Who it is for

Somebody furnishing one room who is about to spend several hundred dollars on
a single large item, and who does not want to measure a door swing arc by
hand.

## What it does

Measure the room, or photograph it. Get an editable 3D version. Browse real
products across every merchant on Shopify, drop one in as a preview, and get
a plain-English answer about whether it fits, including what it collides
with and why. Then buy it on the merchant's own page.

### The product source changed, on purpose

This started as "search one connected development store and buy there". A
development store with four desks is not furniture shopping, so the demo
path is now browse-first: every merchant on Shopify, view only, and the
purchase happens on their site rather than ours.

Nothing was deleted to do it. The cart, the approval record, the Decision 1
checkout audit and the live cart write are all still in the repository and
still tested. They are unreachable from the Design view, and a test is what
keeps them unreachable. A preview item is refused by the checkout review
before a cart line exists, so it can never reach an approval, the purchase
server or a link.

The practical gain is that the whole flow now runs with **no Gemini key and
no Storefront credentials**: the room is typed in from tape measurements,
browse search needs neither, and the fit verdict is pure geometry.

```
  photos of a room
        │
        ▼  Decision 1 photo gate: enough coverage? a person in frame?
   3D room draft            Gemini 4 Neon reads walls, floor, doors, windows
        │
        ▼  search the connected development store, verified variants only
   product in the room      3D model from the merchant's own Model3d, or
        │                   generated from the listing's photos
        ▼  the fit engine decides
   fits / tight / does not fit / unknown
        │                   with the overlap polygon and the cause in words
        ▼  you approve the cart, line by line
   deterministic checks + a Decision 1 audit
        │
        ▼
   hosted Shopify test checkout
```

## What is real, and what is not

Nothing in this table is a claim we have not run.

| Piece | Status |
| --- | --- |
| Contracts, JSON schemas, fixtures | **Real.** Twelve models, one file each, generated schemas checked in CI |
| The fit engine (`fit/`) | **Real.** Pure geometry, no network or database. 15 of 15 golden cases |
| Room photo gate and room draft | **Not working today.** The code path is real and installed, but the Gemini key is invalid: every call returns `401 API key not valid`, and `/studio/room/scan` answers `provider_authentication_failed`. With a working key this is the one lane A function that is genuinely real |
| Studio pages: room, scene, product, voice, chat | **Real.** All five boot and serve. Room and product render their empty states correctly; the scene view waits on a file that was deliberately not taken from `feature/mitran` |
| Product 3D models | **Not demonstrated.** Our development store's products carry no photographs and publish no `Model3d`, so there is no merchant mesh to load and nothing for the generator to read. Generation also needs Gemini, which is down. Anything the 3D view would show for one of our products today is a **plain stand-in**, not a model of that product |
| Catalog reads from the dev store | **Real and verified in this run.** `GET /api/catalog/search?query=Walnut Gaming Desk` returns 4 shoppable offers from `ethyneon.myshopify.com` with confirmed dimensions from metafields |
| Shop MCP discovery | **Real.** Four read-only tools allowlisted, twelve purchase tools blocked |
| Cart review, approval, reconciliation | **Real and verified in this run**, against a live dev-store variant |
| Live cart write | **Real, and off by default** behind two gates |
| Hosted checkout handoff | **Real, verified earlier and separately.** The paid run was done through `scripts/live_cart_smoke.py` with `LIVE_CART_WRITES=1`: card 1 approved and the order appeared in Shopify Orders, card 2 declined. It was **not** done through the Studio's checkout button, which has never been exercised against a real payment |
| Payment confirmation | **Not done, deliberately.** The app reports `payment_unconfirmed` after a real paid order, because order lookup is off. We do not claim money we have not read back |
| Lane A in the running backend | **Real for room-photo analysis only.** `analyze_room` is Karishma's code. `validate_room_spec` and `run_checkout_gates` are delegated to our own modules. The other four are labelled stubs. `/api/health` names all seven |
| The Argon placement planner and Carbon | **Not exercised.** Neither ran at any point in this demo. Argon appears in the Studio's room planner and Carbon in the sandbox design, and neither was reached |
| `web/` React app | **A shell.** Health and contract types only. The Studio is the front end |
| Evaluation harness | **Runs, with zero cases.** See below |

### Test counts

| Suite | Environment | Result |
| --- | --- | --- |
| Root (`tests/`) | `.venv`, Python 3.10 | **787 pass offline** |
| Studio (`ethyneon-studio/tests/`) | own `.venv`, Python 3.12 | **293 pass offline** |
| Live checks | needs credentials | 17, deselected by default, skip rather than fail |
| `scripts/e2e_mock.sh` | the pull request gate | all 6 steps pass |

Both suites were run with credentials blanked and the network blocked.

### What a live run looks like today

Both servers were started and walked on 23 November 2026, on demo data only.
What worked, and what did not:

| Step | Result |
| --- | --- |
| Backend boots, `/api/health` honest about every mock and every lane A function | **works** |
| Live dev-store search for "Walnut Gaming Desk" | **works.** 4 shoppable offers, Walnut $329.00 and Black $299.00, dimensions 1.40 x 0.60 x 0.75 m from metafields, no photographs on any of them |
| Place that variant in a room, verified against the store | **works.** A `variant_verified` event is written |
| Cart review and approval | **works.** $329.00, within budget, `approved` |
| Checkout audit with cart writes off | **works, and correctly withheld the link.** The audit returned `unavailable` because the Decisions call needs the same invalid Gemini key, and because the audit did not pass, no checkout URL was produced. It is absent from the response and absent from `/api/state` |
| Fit verdict, desk inside an inward-opening door | **works.** `does_not_fit`, failing check `door_swing`, cause: *"The open door would hit obj_desk by 0.60 m."* |
| All five Studio pages serve, 28 CC0 sample sets listed | **works** |
| Room photos to a 3D room | **does not work.** Invalid Gemini key |
| Product search inside the Studio | **does not work as our store.** The Studio needs its own `SHOPIFY_STOREFRONT_DOMAIN` and `SHOPIFY_STOREFRONT_TOKEN`; with only `SHOPIFY_SHOPS` it falls back to Shop MCP, which returns other merchants |
| Karishma's lane A demo: preset room, door swing, planner, activity trail | **works**, every mocked value labelled |

One bug was found and fixed during this run. The Storefront client fell back
from the private to the public auth header on 401 only, and our store answers
403, so every live catalog read had been failing while `/api/health` still
reported `commerce: live`. Health reports configuration, not reachability.

### The evaluation harness has no cases, on purpose

[`eval/run_eval.py`](../eval/run_eval.py) scores the whole pipeline against rooms somebody measured
with a tape, and reports cases written by the team separately from cases
written by anyone else. A pipeline evaluated only on its authors' own rooms
looks better than it is.

We shipped **zero cases**. Each one needs a real room, a tape measure, a note
about the door, and written consent. We did not have those, and fabricating
them would have made every number in the report a lie. The harness runs, is
tested, and prints zeroes. [eval/CASES.md](../eval/CASES.md) says how to contribute one.

## The agent proposes, the code decides

This is the design decision the whole project rests on, and we can show the
number behind it.

We have two pieces of geometry in the tree. The fit engine in `fit/`, which is
deterministic and is the only thing allowed to return a verdict. And the
solver written inside the AI lane, vendored at
`ai/karishma/_vendor/solver.py`.

Run both over the same 15 golden cases in `fixtures/fit_cases`:

| | Matches the expected verdict |
| --- | --- |
| `fit/`, the deterministic engine | **15 of 15** |
| the AI lane's solver | **7 of 15** |

Seven of the eight disagreements are the solver returning a confident "fits"
where the engine returns `does_not_fit` or `unknown`. It has no walkway check,
no drawer extension and no chair pull-back, and its "near a door" heuristic
misses a missing hinge. The two also traverse walls in opposite directions, so
a door offset means a mirrored position in each.

None of that makes the solver bad work. It makes it the wrong thing to let
near a shopper's money. So the model reads the room and proposes placements,
and `fit/` decides every verdict a user sees. `unknown` wins over a guess: if
a dimension, hinge or swing direction is missing, the answer is `unknown` and
the report says exactly what was missing, rather than filling the gap.

## Safety design

- **Only `commit_revision` writes state.** Agent code returns proposals, never
  writes. Karishma's `Session.commit_scene` and `Session.set_room` raise
  rather than pretend.
- **A scene item or cart line needs a verified dev-store variant that is
  available.** `CatalogOffer` refuses `shoppable: true` on any discovery
  surface at the model level, so it cannot drift.
- **Shop MCP is discovery only and can never reach a cart.** Four read-only
  tools on the allowlist, twelve purchase and payment tools refused before a
  request is even built.
- **`LIVE_CART_WRITES` is 0 by default and sits behind two gates.** Both must
  be open, checked immediately before any write.
- **The checkout link is released only after the deterministic checks and the
  Decision 1 audit, and never appears in logs or `/api/state`.** A test pins
  that with a sentinel token.

A sixth, added here: [`scripts/secret_scan.py`](../scripts/secret_scan.py) runs before every commit and
fails on anything shaped like a credential or on a tracked `.env`, database,
cache or room photograph. It reports the file and the rule and never the
matched value.

## Who built what

Three people, three lanes. Worth saying plainly, because the lanes were
integrated by copying files between branches rather than by merging them, so
`git log` credits the integrator rather than the author in several places.

| Contributor | Lane | What they built |
| --- | --- | --- |
| **SKKarishma20** | A, the AI | Room analysis from a photograph, the Decision 1 photo gates, the Gemini 4 Neon room draft, the workflow planner, recovery, the geometry solver, and a complete vanilla three.js front end on her own backend. Her code is vendored unchanged at `ai/karishma/`, her analysis runs in the product behind `ai/karishma_adapter/`, her solver tests are at [`tests/ai/test_vendored_solver.py`](../tests/ai/test_vendored_solver.py), and her front end runs at [`demos/karishma-lane-a/`](../demos/karishma-lane-a/README.md) |
| **mitran27** | B, the Studio | The whole of `ethyneon-studio/`: the room, scene, product, voice and chat pages, room capture and photo guidance, the room planner, the Shopify-to-GLB product model builder, and the 28 CC0 sample room sets with their provenance manifest. The live camera and demo video capture controls |
| **Shruti Sridhar** | C, the money path | Contracts and schemas, the fit engine, state and revisions, the catalog layer, the cart review and approval, the live cart write and its gates, the checkout audit and handoff, the Shop MCP client and allowlist, the lane A adapter, and the integration of all three lanes |

### Branches

| Branch | Contributor | Where it went |
| --- | --- | --- |
| `shruti/full-build` | Shruti Sridhar | Merged. Lane C |
| `karishma/full-build` | SKKarishma20 | Merged, though three "keep lane C files" resolutions meant her `frontend/`, `eval/` and five test files became ancestors of main without their content landing. Recovered on `release/best-of` |
| `feature/mitran` | mitran27 | Partly taken, by copying named paths. Its Studio backend was not taken: that branch deletes `storefront.py` and `ethyneon_client.py`, which would have removed the dev-store verification path |
| `integrate/lane-a`, `integrate/lane-b`, `feat/studio-storefront-search`, `cleanup/untrack-local-files` | Shruti Sridhar | All merged |

## Known gaps

- **The Gemini key is invalid.** Every model call returns `401 API key not
  valid`, so the photo gate, the room draft, the product mesh generator, the
  Decision 1 checkout audit and the Argon planner are all unreachable right
  now. Nothing is wrong with the code paths; the credential needs replacing.
- No evaluation cases, as above.
- Payment is never confirmed from the handoff.
- Four of the seven lane A functions are stubs, and say so.
- Our development store's products have no photographs, so no product has a
  real 3D model. Anything shown in a 3D product view is a plain stand-in.
- The Studio needs its own Storefront domain and token to search our store.
- The `web/` React app is a shell; the Studio is the real front end.
- No license has been chosen, so the code is not licensed for reuse yet.
- `feature/mitran` cannot be checked out on Windows at all: it contains
  `com1.txt` and `com2.txt`, and `COM1` and `COM2` are reserved device names.
