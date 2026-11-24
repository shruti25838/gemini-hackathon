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
| `11-scene-room-form.png` | The Design view with no room yet: the measure-your-room form, and the note that a photo-built room needs the Gemini key while these numbers need nothing | Real page, empty room | **Yes.** It is the clearest single picture of what changed |
| `12-scene-browse-grid.png` | Eight real products from six merchants for "gaming chair", each card captioned **View only, sold by <merchant>** with a link to that merchant, under the attribution line | Real, live Shop MCP | **Yes, with one caveat.** The cards carry other merchants' product photography at card size. The cards name and link to each merchant, which is the attribution, but it is still their work |
| `13-scene-preview-placed.png` | A browse product placed in the room as a preview, tagged `$279.00 preview` | Real | **Yes** |
| `14-scene-drag-blocked.png` | The same chair dragged into the door swing: the product glows **red**, and the panel reads "That spot is blocked. Release to have the server check it anyway" | Real | **Yes.** The best picture of the fit idea |
| `15-scene-drag-clear.png` | Dragged back into open floor: **green**, "Clear here. Release to have the server check it" | Real | **Yes**, as the pair to 14 |
| `16-scene-drop-verdict.png` | After release, the server's verdict: **Tight**, "fits, but barely. Only 0.03 m to spare", with the badge **Tentative: size read from the listing text, not measured** | Real, from the deterministic engine | **Yes.** The tentative badge is the honest part |
| `17-scene-your-list.png` | Your list: two rows with merchant, price, fit badge, "Preview only, sold by …", and a **Buy at <merchant>** link. "Listed prices total $364.99", and the note that purchases happen on the merchant's own site | Real | **Yes.** Shows there is no cart and no checkout |
| `18-scene-stand-in-and-unknown.png` | A second product whose listing states no size. **Unknown** with the missing datum named, a wireframe box tagged `stand-in · size guessed`, the panel reading "50 × 50 × 80 cm · not in the listing, this is a fallback" and "3D model unavailable. Gemini rejected the configured credentials" | Real, and a real failure | **Yes.** It is the honesty artifact of this branch |
| `24-hero-eye-level.png` | The hero: standing inside a 4.00 by 3.20 by 2.60 m room with sage walls and a light oak floor, **both windows in frame**, with a green velvet sofa, a walnut side table, a mid-century armchair, a cube bookcase and a floor lamp. Labels off | Real capture, eight real merchant meshes, no stand-ins | **Yes** |
| `25-hero-plan-labelled.png` | The same room from above with every label on: `window 1 / 1.60 m`, `window 2 / 1.40 m`, `door main / 0.90 m`, all four walls, and eight price tags each reading **preview** with a green dot for a real model. Not one reads stand-in | Real | **Yes.** This is the shot that makes the hero defensible |
| `22-scene-color-wheel.png` | The Room colors panel with the colour wheel: a hue ring and a shade square drawn on a plain canvas, the Walls/Floor target switch, the current swatch, its hex code and a hex field, with the existing swatch rows below | Real | **Yes** |
| `23-scene-wheel-pink-walls.png` | The same room after dragging the ring round to pink and softening it in the square: walls `#d6abbd`, the chip, hex code and field all agreeing, **Undo** showing and **walls chosen by you** in the corner | Real, driven with real mouse events | **Yes.** The pair with 22 is the before and after |
| `20-scene-colors-before.png` | The Room colors panel as it opens: seven wall swatches, five floor swatches, a custom picker for each surface, and the typed-command box. The room is the default off-white | Real | **Yes** |
| `21-scene-colors-after-olive.png` | After typing "make the walls olive": all four walls are olive in the 3D room, the olive swatch is ringed, an **Undo** button has appeared and the panel reads **walls chosen by you** | Real | **Yes.** The pair with 20 is the before and after |
| `19-scene-drag-red-midflight.png` | A preview chair held mid-drag over the door swing in Plan view: the product glows **red**, the panel reads "That spot is blocked. Release to have the server check it anyway", the Move note explains what Move is for, and the hint under the room reads "Press and drag an item to move it" | Real, captured with real mouse events | **Yes.** It is the proof that drag works and the clearest picture of the fit rule |
| `08-studio-browse-mode.png` | Product Studio as it opens: the mode switch with **Browse all Shopify / view only** selected, the attribution line under it, and the masthead reading "Browsing all Shopify". The grid is empty and the 3D pane is waiting | Real page, **nothing searched yet** | **Yes.** It is the clearest picture of the rule: the label is on screen before any result is |
| `09-studio-browse-search.png` | The same page after pressing "Find gaming chairs". The status line reads "Gemini rejected the configured credentials" | Real, and a real failure | **Yes**, if you want to show the failure honestly. Not if you want a product shot |
| `10-studio-store-mode.png` | After switching to **Our store / buyable**. The pill moves, the masthead changes to "Our store", the status line changes | Real | **Yes**, as the before-and-after of the switch |
| `07-lane-a-activity-panel.png` | The same demo after setting the door to hinge left, opens in. The swing renders. The activity panel shows the full trail: intake, brief, planner, door motion, then a refusal and a pause | Real | **Yes, with a caveat.** It contains a deliberate `LaneANotWired` refusal. Post it only if you want the "it refuses rather than guesses" angle, which is a good angle |

Shots 11 to 18 were captured on the browse-first Design view, against a
room typed into the preset form and live Shop MCP results. No Gemini key
and no Storefront credentials were involved in any of them, which is the
point: the flow they show does not need either.

Shot 19 was captured by driving a real `page.mouse.down`, a move in steps
and a `page.mouse.up`, not by setting state. It is the frame that proves
the drag fix: before it, pressing the price pill did nothing at all and
pressing near the product orbited the camera instead.

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
- **Any merchant photo larger than a card thumbnail.** The browse cards
  cap images at 110 px and request them from Shopify's CDN at 220 px
  wide. Nothing here enlarges one, and no screenshot shows one bigger.
  The cards name and link to each merchant, which is the attribution,
  but the photography is still theirs and this repository records no
  licence covering it.
- **A photo-built room.** Still blocked by the same invalid Gemini key.
  The preset form is the way round it and is what shots 11 to 18 use.

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
