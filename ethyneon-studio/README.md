> **Ethyneon Studio (feature/mitran).** The full working flow: live camera or photos → Decision 1 full-coverage gate → Gemini 4 Neon room → chat or voice planning (Argon, Gemini 3.8 Live) with drag-and-drop → Design view with cached 3D Shopify products, guarded edits, Shopify link → 3D, and a Decision 1 checkout audit. Run every command below from this `ethyneon-studio/` folder, copy `.env.example` to `.env` with your own keys, and open http://127.0.0.1:8000/.

# Ethyneon Python connection layer

This FastAPI service provides the connection endpoints for Room Studio, Design Studio, and Shopify Checkout. It follows the team's `GET /api/agora/token` route and `{code, message, retryable, details}` error format.

## Run locally

```sh
test -x .venv/bin/python || uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
test -f .env || cp .env.example .env
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Set the values in `.env` before starting the service. Process environment values take precedence.

| Setting | Purpose |
| --- | --- |
| `ETHYNEON_CONNECTION_TOKEN` | A shared backend bearer token, generated with `openssl rand -hex 32`. Send it as `Authorization: Bearer <token>` on `/api` requests. |
| `GEMINI_API_KEY` | Sign in with the hackathon account and create an API key in its Gemini project. [Google AI Studio API keys](https://aistudio.google.com/api-keys) |
| `AGORA_APP_ID` and `AGORA_APP_CERTIFICATE` | Values from the same certificate-enabled Agora project. Only the App ID and issued room tokens go to the browser. |
| `SHOPIFY_SHOPS` | Optional comma-separated `myshopify.com` domains. Set it to the development-store domain to restrict discovery to that store. Public Shop catalog reads work without a shopper account. |
| `SHOPIFY_STOREFRONT_DOMAIN` and `SHOPIFY_STOREFRONT_TOKEN` | Optional development-store domain and public Storefront access token for native product 3D media. |

Open `http://127.0.0.1:8000/docs` for typed requests and the bearer-token field. `GET /healthz` checks that the server is running. `GET /api/providers` reports configuration presence. It does not make provider calls.

## Integration routes

| Project | Route | Request and result |
| --- | --- | --- |
| Room Studio | `GET /api/agora/token?channel=ethyneon-room&uid=42` | Returns `appId`, `rtcToken`, `rtmToken`, `expireAt`, `channel`, and `uid`. The token has RTC and Signaling privileges and lasts 60 minutes. Use the numeric UID for RTC and its string form for Signaling. |
| Design Studio | `POST /api/gemini/interactions` | Send `input` as text or Gemini content blocks. Select one of the four Gemini 4 models, and optionally pass an Interactions `response_format`. |
| Room and Design Studios, Checkout | `POST /api/gemini/decisions` | Send `input` and named `questions` with `noul`, `choice`, or `score` types. The reply includes Gemini's answers and confidence. |
| Design Studio | `POST /api/gemini/managed-runs` | Send `input` and select Argon or Carbon. Starts a background Antigravity run with a token budget and returns its interaction ID. |
| Design Studio | `GET /api/gemini/interactions/{interaction_id}` | Poll the Gemini interaction or managed run. Persist the returned ID before polling. |
| Shopify Checkout and Design Studio | `POST /api/shopify/catalog/search` | Send `query` or an `image` with `content_type` and base64 `data`. Include `country`, `currency`, and optional price bounds in minor units. Replies preserve Shopify variant IDs and catalog warnings. |
| Shopify Checkout | `POST /api/shopify/catalog/product` | Send `product_id` and optional `selected` option names and labels. Compare Shopify's actual selected options with the request. |
| Shopify Checkout | `POST /api/shopify/catalog/variant` | Send the exact `variant_id` and shopping context. A substituted variant is rejected. |
| Room Studio | `POST /api/shopify/catalog/variant/model` | Send the same exact `variant_id` and shopping context. Returns a merchant model URL or a generated GLB with its source label and exact Shopify variant. |
| Design Studio | `POST /api/studio/command` | Send `command`, up to four `visible` cards in grid order, and the `selected_variant_id`. Returns a catalog result or a typed preview edit. |
| Room Studio | `POST /api/room/capture/check` | Send one to five base64 room `photos` and any `required_features`. Returns the Decision 1 capture review and next camera instruction. |
| Room Studio | `POST /api/room/scan` | Uses the same capture input and accepts an optional `measurement` (`axis`, `value_m`). Decision 1 reviews the photos first; Gemini 4 Neon drafts the room only when the review passes. Returns the review as `gate`, with `room: null` when coverage is not enough. |
| Room Studio | `POST /api/room/{room_id}/revise` | Send `base_revision`, a client `action_id`, and the change. Supports category, measurements, surface colors, `preference`, `move_item`, and `add_product`. A stale base returns 409. A repeated `action_id` returns the first result. |
| Room Studio | `POST /api/room/{room_id}/plan` | Send `base_revision` and `instruction` to the Argon chat. Include `previous_plan_id` to continue the conversation. API clients can provide `target_slot_id` explicitly. |
| Room Studio | `GET /api/room/{room_id}/plan/{plan_id}` | Read the status, chat `messages`, and latest room JSON `draft`. |

For Agora, call the token route from the Room Studio backend after it checks who can join the room. The browser joins Agora with the returned token. Keep the shared connection token between backends.

Shop MCP discovery returns only Shopify variants from identifiable Shopify stores. The checkout project still verifies store ownership and current stock and price through the connected development store before creating its cart. The connection layer exposes read-only catalog routes. Gemini and Managed Agents provide results that the workflow and checkout code validate before using them for state changes or purchases.

## Shopify product 3D models

The model endpoint looks for Shopify `Model3d` media through the connected storefront or the public storefront of the exact variant found by Shop MCP. It prefers a GLB source and reports that the mesh applies to the product. When a browser-compatible merchant mesh is unavailable, or when the variant specifies a finish and has photos, Gemini 4 Hydrogen reads up to four Shopify photos, the selected options, and the listing description. Variant photos and finish options guide colors. Additional views help establish shape and material. Python builds a Y-up, floor-aligned GLB with approximate shapes and procedural surface textures based on the visible materials. [Shopify Storefront Model3d](https://shopify.dev/docs/api/storefront/latest/objects/Model3d) · [Shopify product media](https://shopify.dev/docs/api/storefront/latest/queries/product)

For the generated path, decode `model.data_base64` to load the GLB with Three.js `GLTFLoader`. `reference_image_urls` records which photos Gemini received. `selected_finishes` lists the Shopify finish options, and `surface_palette` records each generated part's hex color and material. `dimensions_m` uses labeled overall dimensions from the selected Shopify options or listing description. The generated preview uses the minimum listed height for adjustable furniture and returns the full `height_range_m`. If dimensions are unknown, the generated mesh uses a one-unit height. Use `model.url` directly for merchant assets. [Three.js GLTFLoader](https://threejs.org/docs/pages/GLTFLoader.html)

The public Shopify sample is in `samples/shopify-walnut-side-table.glb`. Its accompanying JSON records the exact Shopify variant and source photo. To generate a fresh preview, send this body to `POST /api/shopify/catalog/variant/model` with the backend bearer token.

```json
{"variant_id": "gid://shopify/ProductVariant/50876631908516", "country": "US", "currency": "USD"}
```

## Room Studio

Open [Room Studio](http://127.0.0.1:8000/studio/room) to turn two to five photos into an editable 3D room draft.

1. Add photos. Photo 1 faces the main wall, which becomes `wall_n`. Photos of the other walls improve depth and openings. The browser re-encodes each photo to strip EXIF and GPS data, and the server re-encodes it again. Photos are analysed and never stored.
2. Tick the features the room has (door, window, cupboard, pillar). Each time a photo is added or removed, the page calls the capture check and shows coverage, quality, a feature checklist, and the next camera instruction. A non-room photo disables building. **Build 3D room** appears only after the review passes. The scan re-runs the review and calls Gemini 4 Neon only on a pass; otherwise it returns `room: null` with the review. The line under the button names the model at work (`gemini-decision-1`, then `gemini-4-neon`). Building needs at least two photos and full coverage: Decision 1 must see all four walls, their corners, the floor, and every door and window across the photos (coverage 3.0 of 4). Photos smaller than 320 px on a side are refused before any Gemini call.
3. The 3D room shows walls, a floor finish, door swings, and windows, with walls nearest the camera cut away. Confirm a real width, depth, or height to anchor scale. An estimated partner dimension, the openings, and the detected items rescale with it. Change the room category or the surface colours. Each edit creates a new revision.

The room frame is in metres, Y-up, with the origin at the `wall_n`/`wall_w` floor corner, +x along `wall_n` and +z toward `wall_s`. Opening offsets are measured from the start of the wall. Object poses are 6 DOF: `position` (x, y, z) plus `rotation` (yaw, pitch, roll in degrees). See [backend/room.py](backend/room.py). Detected furniture is stored in `existing_items` and hidden by default to keep the room empty.

### Scene JSON and placement

Room Studio lists detected walls, openings and fixtures in room coordinates. Gemini 4 Neon classifies the photographed room and located features. Gemini 4 Hydrogen extracts a design brief from user preferences, including intended purpose, styles, colors and priorities.

The scene includes window areas and centers, plus candidate positions for 60 × 60 cm paintings on observed walls. The preview estimates relative daylight from window area, distance and facing direction. Openings and nearby fixtures at hanging height limit painting locations.

Select a movable object and edit X, Y, Z or yaw. The server checks room boundaries and overlapping footprints before saving the next revision. The same accepted document drives the viewer and the **Download scene JSON** button. Use **Place a Shopify product** with a verified variant ID and an available position. The listing needs explicit overall dimensions. The scene preserves the variant, listing, returned price, model reference, dimensions and pose.

### Room JSON chat

Open [Room JSON chat](http://127.0.0.1:8000/studio/room/chat) or select **Room JSON chat** from the Room Builder. The chat collects width, depth, and ceiling height, then asks what you want in the room. Argon drafts the layout, and Shopify supplies exact variants with dimensions that fit the planned slots. Review the proposed items and their positions in the JSON pane, then select **Confirm placement** once to save them to `geometricconfig.json`. [Antigravity Managed Agents](https://aistudio.google.com/docs/antigravity-agent)

### Camera guidance

Call `POST /api/room/capture/check` from the Room Builder backend for immediate guidance between photos. The local browser can call `POST /studio/room/capture/check`. Keep the photos in capture order so camera directions refer to the last image. Send base64 bytes for a JPEG, PNG, or WebP.

```json
{
  "photos": [{"content_type": "image/jpeg", "data": "<base64 image bytes>"}],
  "required_features": ["door", "window"]
}
```

`required_features` is optional. Use `door`, `window`, `cupboard`, and `pillar` for features the shopper knows are present. Decision 1 also flags partly visible features it can identify. `not_visible` means the submitted views did not show that feature. The service evaluates blur and lighting on the latest photo and coverage across all photos in one call. [Decisions API media and scoring](https://aistudio.google.com/docs/decisions-api#media)

Show `follow_up.message` as the primary hint, use `follow_up.direction` for a camera arrow, and use `follow_up.target` for a feature label. `guidance` contains the ordered hints and `features` contains visibility and confidence per feature. A `pass` has enough coverage for a draft and can still suggest another viewpoint. `needs_more` requests a retake or more coverage. `rejected` means the photos provide insufficient evidence of a room.

## Sample rooms

`samples/rooms/` holds 28 photo sets of seven plain, empty rooms (no loose furniture), each with 2, 3, 4, or 5 photos of the same room: `kitchen`, `gaming-room`, `living-room`, `working-room`, `bedroom`, `study-room`, and `guest-room`. The folder name is the intended use of the empty room. The photos are ordinary 100° camera views cut from CC0 [Poly Haven](https://polyhaven.com/hdris) interior panoramas by `scripts/make_room_samples.py`. `manifest.json` records the source, headings, and the Decision 1 result for each set: single photos and two-photo sets need more coverage, every five-photo set builds, and three or four photos build when they cover every wall.

## Product catalog

`samples/catalog/products.txt` is the full demo shopping list: 540 real Shopify products for 5 room types, 27 object types, and 7 taste styles (modern, minimalist, scandinavian, mid_century, industrial, farmhouse, boho). Each style has one main product and two alternatives of the same object type; tech items (gaming chair, monitor, gaming storage) vary by type option instead. `products.json` holds the same entries as data: exact `variant_id`, USD price in minor units, store, listing link, photo, `style` or `tech_option`, `role` (main or alternative), rooms, mount (floor, wall, desk, nightstand), the listed size when the listing states one, and a typical size otherwise. Rebuild both with `.venv/bin/python scripts/build_catalog.py`; it drops implausible prices and titles that do not name their object type. The Design view uses this file for alternatives: a placed product found in the catalog offers the other products of its object type, and anything else falls back to a live Shopify search.

## Models and what they do

A small chip at the bottom left of every page names the model at work while a request runs.

| Step | Model | What it does |
| --- | --- | --- |
| Room photos | `gemini-decision-1` (Decisions API) | Scores each photo set: is it a room, coverage, image quality, blur, lighting, room type, features, next camera move. Building starts only at full coverage with at least 2 photos. |
| Room photos | `gemini-4-neon` | Reads 2 to 5 photos into the RoomSpec: room size, walls, floor, doors, windows, and existing fixtures (heater, air conditioner, screen, pillar, wardrobe, cupboard). |
| Plan · chat and voice | `gemini-4-hydrogen` | Turns the shopper's preferences into a design brief (purpose, styles, colours, priorities). |
| Plan · chat and voice | `gemini-4-argon` (Antigravity Managed Agent) | Plans which furniture the room needs and where it goes, and replans on a conflict. |
| Plan · voice mode | `gemini-3.8-live` | Two-way voice (and optional camera) conversation; drives the same room chat through tools. |
| Plan · products | Shopify catalog (Shop MCP) | Finds and verifies exact Shopify variants, prices, stock, and listing sizes. |
| Design view | `gemini-4-hydrogen` | Builds a 3D model from a product's Shopify photos when the store has no 3D asset (red dot → green). |
| Design view | Guardrails (code) | Refuses placements outside the walls, overlapping products or fixtures, in a door swing, or bigger than the replaced item. |
| Checkout | Shopify catalog + `gemini-decision-1` | Re-verifies every variant, price, and stock, then audits the cart against the room and brief. Demo stops before placing an order. |
| Product Studio | `gemini-4-hydrogen` | Interprets typed product commands (search, select, rotate, recolour). |

## App flow

Every page has the same top bar: **1 · Room photos** (`/studio/room`) → **2 · Plan with chat** (`/studio/room/chat`) → **3 · Design & checkout** (`/studio/scene`). After the room builds, **Plan furniture with chat** opens the chat with that room. The chat writes the confirmed room to `geometricconfig.json`, and the Design view renders it.

## Demo product and 3D model table

`samples/catalog/demo_30_models.md` (and `.csv`) lists the 30 demo products with room, type, price, size, the Shopify listing link, the 3D model link, and whether the model is ready. Every model link has the form `/studio/scene/models/<numeric variant id>.glb` and serves Codex's file from `samples/models/` or the app's generated model. Rerun `.venv/bin/python scripts/model_table.py [base_url]` after adding models to refresh the ✅/❌ column.

## Design view

Open [Design view](http://127.0.0.1:8000/studio/scene) to render the central session file `geometricconfig.json` in the repository root (override with `GEOMETRIC_CONFIG_PATH`). The page polls it every 1.5 s, so edits made by the chat agent appear without a reload. A half-written or invalid file keeps the last valid room on screen.

| Field | How the view uses it |
| --- | --- |
| `dimensions.width/depth/height.value_m` | Room shell in metres (required). |
| `openings[]` | `type`, `wall`, `width_m`, `height_m`, and `position` as the bottom centre of the opening on its wall. Optional `offset_m`, `sill_m`, `hinge`, `swing`. |
| `existing_items[]` | `kind`, `fixed`, `pose`, optional `label` and `size`. Missing sizes use defaults per kind; a pillar defaults to full height. |
| `placed_products[]` | `id`, `label`, exact Shopify `variant_id`, `price` in minor units, `size` (width, depth, height), and a 6-DOF `pose`. Optional `listing_url` and `model` (`url` or `data_base64`). |
| `walls[]`, `floor`, `design_brief`, `lighting` | Optional colours and materials, the brief shown above the room, and lighting extras. |

**3D status.** Each price tag, product row, panel title, and alternative card has a dot: green filled means a 3D model is cached, red hollow means none yet, amber pulsing means one is being built. Cached models load into the room at once; a product without one shows as a sized box until it is clicked, which builds the model from the Shopify listing (merchant `Model3d` media, otherwise the Gemini photo-to-GLB recipe) and turns the dot green. Prepared models go in `samples/models/<numeric variant id>.glb` (with an optional `.json` of facts) and count as cached; generated ones are cached in `.cache/product-models/`. `POST /studio/scene/models/status` reports `cached`, `building`, or `missing` per variant.

**Editing.** Select a product to move it (drag on the floor), rotate it 15° at a time, swap its position with another product, replace it with a Shopify alternative, or remove it. `POST /studio/scene/edit` applies each edit to `geometricconfig.json` with the page's `base_etag` (a changed file returns 409) and a client `action_id`, then bumps `revision`. Guardrails in `backend/scene_edit.py` refuse a pose outside the walls, overlapping another product or fixture (wardrobe, pillar, heater), or inside a door's swing or a doorway's clearance, and refuse a replacement larger than the space of the product it replaces. A refused edit leaves the file unchanged, the product snaps back, and the panel shows the reason.

**Checkout.** **Review & checkout** calls `POST /studio/scene/checkout`, which re-verifies every product as the exact Shopify variant with live price and stock, reruns the placement guardrails, and sends the evidence to Gemini Decision 1 (`purchase_scope_matches`, `required_constraints_resolved`, `fits_brief`, `next_action`). The review is `ready` only when every code check passes and Decision 1 confidently answers `ready`; then **Continue to checkout** shows each store's order and the total before shipping and tax. **Demo mode:** the flow stops there. **Place order** is disabled, no store checkout or payment link is returned, no Shopify cart is created, and no order is placed (`order.placed: false`). Any edit requires a fresh review.

## Product Studio

With the local server running, open [Product Studio](http://127.0.0.1:8000/studio) in a WebGL-enabled browser. Use these text commands in order, or click a product card to select it.

1. **Gaming chair** loads live Shopify results into a two-column grid.
2. **Choose top right** opens the second result as a 3D preview. Drag to orbit, scroll to zoom, toggle auto-rotate, or type **Rotate it by 90 degrees left**.
3. **Make it green** recolors the preview. The original Shopify variant and listing stay linked.
4. **Add a headrest** searches Shopify for a chair headrest and attaches the chosen accessory model to the chair preview. The headrest keeps its own Shopify variant and the preview displays its unverified fit status.

The browser talks to the loopback studio route. `/api/studio/command` exposes the same command handler with backend bearer authentication for integration and future voice transcripts.

## Verify

```sh
.venv/bin/python -m unittest discover -s tests/backend -v
```

The automated suite covers the local HTTP contract, Gemini and Shop MCP request formats, Shopify-only catalog filtering, ambiguous managed-run timeouts, and the Agora SDK token. A live Agora join requires the two client devices and their Agora project. The Gemini key comes from the temporary account's AI Studio project.

[Agora SDK token reference](https://docs.agora.io/en/api-reference/api-ref/server-sdk/platform/python) · [Shop MCP quickstart](https://shop.dev/docs/quickstart) · [Gemini Decisions API](https://aistudio.google.com/docs/decisions-api) · [Antigravity Managed Agent](https://aistudio.google.com/docs/antigravity-agent)
