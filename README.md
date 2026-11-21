# Ethyneon (Person A build: AI and agents, deep)

A shopper photographs their own room and records the door opening. Ethyneon builds an editable 3D room
from the photo, finds real Shopify products within budget, and lays them out. One Managed Agent proposes
the layout, a second one tests it in a separate sandbox, and the server re-checks everything before
anything is bought.

Everything in this repo was written during the Gemini 4 NYC Hackathon (Nov 21, 2026).

## Run

```bash
uv venv .venv && source .venv/bin/activate && uv pip install -r requirements.txt
cp .env.example .env            # add GEMINI_API_KEY and Shopify Storefront credentials
uvicorn backend.app:app --port 8000
open http://localhost:8000
python -m pytest -q             # 41 tests, no network
python scripts/e2e_mock.py      # whole journey on fixtures (MOCK_AI=1), no keys needed
```

## Layout (team integration contract)

| Folder | Lane | What |
|---|---|---|
| `ai/` | **A (this build's deep lane)** | `analyze_room`, `validate_room_spec`, `handle_command`, `plan_workflow`, `run_workflow`, `run_checkout_gates`, `run_recovery` (exported from `ai/__init__.py`) |
| `fit/` | C | `solver.py`, standard library only, mounted unchanged into the Argon and Carbon sandboxes |
| `backend/` | C (thin here) | FastAPI routes, session state, Shopify Storefront, cart and checkout |
| `frontend/` | B (thin here) | three.js room, uploads, activity panel, review and checkout screens |
| `contracts/` | shared | one Pydantic model per file, owner noted in each; `interfaces.py` has the Protocol stubs and `Command` |
| `fixtures/` | shared | A's `*.sample.json` (regenerate with `scripts/gen_ai_fixtures.py`) and `ai_mock_outputs.json` |

Conventions follow the contract: wall IDs `wall_n/e/s/w`, fit results `fits | tight | does_not_fit | unknown`,
money in minor units, lengths in metres. `MOCK_AI=1` serves saved outputs labeled `mock` and never calls the sandboxes.

## Which model does what

| Job | Model | Where |
|---|---|---|
| Photo -> typed `RoomSpec` with per-field confidence | Gemini 4 Neon | `ai/vision.py` |
| Door clip -> hinge side, swing, angle, evidence timestamp (agentic video) | Gemini 4 Neon, thinking high | `ai/vision.py` |
| Request -> typed workflow DAG over registered ops | Gemini 4 Neon | `ai/workflow.py` |
| Voice/text -> typed command (raw audio in) | Gemini 4 Hydrogen, thinking low | `ai/intake.py` |
| Layout proposal and repair, in an Antigravity sandbox running `solver.py` | Gemini 4 Argon (Managed Agent) | `ai/agents.py` |
| Independent test in a fresh sandbox (door angles, +3 cm tolerance, path) | Gemini 4 Carbon (Managed Agent) | `ai/agents.py` |
| Recovery: pick one registered action after a failed step (rule table if Argon is down) | Gemini 4 Argon | `ai/recovery.py` |
| Gates: photo quality/privacy, door-clip coverage, request routing, catalog prompt injection, checkout audit | Gemini Decision 1 | `ai/gates.py` |

## Rules enforced in code

- The model proposes; code validates and executes. Plans may only use registered ops, with typed args and no cycles. Mandatory steps (scale check, door inspection, commit) are inserted by the validator.
- The layout commits instantly from the server solver; the Argon/Carbon sandbox review runs in the background and can only offer an alternative, never apply one.
- Every Managed Agent claim is re-run by the server with the same `fit/solver.py`. Only server-verified layouts are committed. Agents never touch the cart, the approval or checkout.
- If an agent or model is unavailable, the run falls back to the deterministic solver or a template plan. The fallback is visible in the activity panel and the fit badge (`local-fallback`).
- Hydrogen's intent and Decision 1's route must agree, otherwise Ethyneon asks the user.
- A single confirmed measurement rescales every still-estimated length (one photo has one unknown scale). Confirmed values are never overwritten.
- If the hinge or swing isn't visible, the fit result is `unknown`. It is never guessed.
- Cart and checkout:
  - Only the "Confirm and add to cart" button creates the approval.
  - Any scene change voids it.
  - The checkout link is released only after deterministic reconciliation and a confident Decision 1 `ready`.
  - Payment happens on Shopify's hosted checkout in test mode.

## Evaluation

`python -m eval.run_eval eval/data/cases.json` (format in `cases.example.json`). Rooms are photographed
and tape-measured by people outside the team. About a third are held out from prompt tuning. The script reports:
- room dimension error before and after one confirmed measurement
- door wall, hinge and swing accuracy
- photo-gate accuracy
- fit verdicts vs a **one-call Neon baseline**, counting **false "fits"** (cases where the desk would actually hit the door)

Results are written to `eval/results/`.

## Team contracts

`contracts/schemas.py` is shared with Person B (room/UI) and Person C (commerce). The room-local
frame is in metres, and money is in minor units.

## Third-party

- three.js 0.170 (MIT), loaded from unpkg in `frontend/index.html`
- google-genai, FastAPI, Pydantic, httpx, uvicorn (their own licenses)
- `eval/data/fixture_catalog.json` is test data, used only with `ETHYNEON_FIXTURE_CATALOG=1` and labeled "not live" in the UI. Its images are placehold.co placeholders.
