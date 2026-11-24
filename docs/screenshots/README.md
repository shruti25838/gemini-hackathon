# Screenshots

Captured 23 November 2026 with headless Chromium at 1600 by 1000, against
servers running on this machine on demo data only: the CC0 sample room sets,
the `ethyneon.myshopify.com` development store, and our own fixtures.

Every image was opened and read before it was committed. None contains a key,
a token, a terminal, a URL bar, a checkout link, a store password, an address,
a real person, or anyone's actual room.

| File | What it shows | Real or not | Safe to post |
| --- | --- | --- | --- |
| `01-backend-health.png` | `GET /api/health`: mocks on, `commerce: live`, `cart_writes: disabled`, and all seven lane A functions labelled individually | Real, live backend | **Yes.** It is the honesty artifact: `analyze_room: real: karishma`, four others `delegated: stub` |
| `02-backend-api-docs.png` | The OpenAPI page, all thirteen routes from session to checkout | Real | **Yes** |
| `03-studio-room-capture.png` | Room Studio step 1: the CC0 sample selector loaded with 15 sets, "Use live camera", "Play demo video", the feature checklist | Real page, **empty 3D pane** | **Yes**, if captioned as the capture step. It does not show a built room |
| `04-studio-product.png` | Product Studio: 3D preview pane and the command box | Real page, **no product loaded** | **Yes**, if captioned as the empty state |
| `06-lane-a-demo-room.png` | Karishma's front end: a 3.2 by 3.0 m preset room in 3D with a door and window, dimension inputs, status pills reading `AI: MOCK (saved outputs)` and `Fixture catalog · not live` | Real render, mocked AI, and it says so on screen | **Yes.** The best single image we have |
| `08-studio-browse-mode.png` | Product Studio as it opens: the mode switch with **Browse all Shopify / view only** selected, the attribution line under it, and the masthead reading "Browsing all Shopify". The grid is empty and the 3D pane is waiting | Real page, **nothing searched yet** | **Yes.** It is the clearest picture of the rule: the label is on screen before any result is |
| `09-studio-browse-search.png` | The same page after pressing "Find gaming chairs". The status line reads "Gemini rejected the configured credentials" | Real, and a real failure | **Yes**, if you want to show the failure honestly. Not if you want a product shot |
| `10-studio-store-mode.png` | After switching to **Our store / buyable**. The pill moves, the masthead changes to "Our store", the status line changes | Real | **Yes**, as the before-and-after of the switch |
| `07-lane-a-activity-panel.png` | The same demo after setting the door to hinge left, opens in. The swing renders. The activity panel shows the full trail: intake, brief, planner, door motion, then a refusal and a pause | Real | **Yes, with a caveat.** It contains a deliberate `LaneANotWired` refusal. Post it only if you want the "it refuses rather than guesses" angle, which is a good angle |

## Not captured, and why

- **A populated browse result grid, and a browse product open in 3D.**
  Both were asked for and neither is possible right now. The command
  box routes a bare product name through the Gemini intent model
  before any search runs, and that key still answers `401`, so the
  grid cannot be filled from the browser in either mode. The 3D pane
  is blocked twice over: nothing can be selected, and the mesh
  generator needs Gemini as well. Without it the pane keeps its
  resting state, the empty grid and "Choose a Shopify result to see
  it in 3D", which is what `09` shows. The browse path itself was
  checked live against Shop MCP and does work: see the report and
  `ethyneon-studio/tests/backend/test_browse_mode.py`.
- **Other merchants' product photography.** A filled browse grid would
  carry it. The cards name and link to each merchant, which is the
  attribution, but it is still their photography and this repository
  records no licence covering it.

- **Room photos to a built 3D room.** The Gemini key is invalid: the Decisions
  and Neon calls return `401 API key not valid`, so `/studio/room/scan`
  answers `provider_authentication_failed`. The page and the sample selector
  work; nothing downstream of the model does.
- **A fit verdict with a door overlap, in a browser.** The fit engine computes
  it correctly, but no front end can currently place a product: the Studio
  needs a scanned room, and the lane A demo's catalogue is shimmed. The engine
  result is in `docs/PROJECT_SUMMARY.md` instead.
- **Product search in the Studio.** The Studio needs
  `SHOPIFY_STOREFRONT_DOMAIN` and `SHOPIFY_STOREFRONT_TOKEN` in
  `ethyneon-studio/.env`. With only `SHOPIFY_SHOPS` set it falls back to Shop
  MCP, which returns other merchants. Those results are view-only and were not
  photographed: they are other people's product photography.
- **The Studio scene and checkout view.** It renders "Waiting for
  geometricconfig.json", a file that exists only on `feature/mitran` and was
  deliberately not taken. Captured, inspected, and not committed, because an
  empty pane citing a missing file is not evidence of anything.
- **Any checkout link.** Never generated, never shown. `LIVE_CART_WRITES` was
  `0` throughout and the audit returned `unavailable`, so the link was
  withheld by design.

## Reproducing them

Both servers must be running, see the root README. Then, from a scratch
directory and not from this repository's requirements:

```powershell
pip install --target $env:TEMP\pwlib playwright
$env:PYTHONPATH = "$env:TEMP\pwlib"
python -m playwright install chromium
```

The captures were taken with a short Playwright script that visits each URL,
waits for the page to settle, and writes a PNG. The script is not committed:
it is six `page.goto` calls and a `page.screenshot`, and keeping a throwaway
automation script in the repository would imply it is maintained.
