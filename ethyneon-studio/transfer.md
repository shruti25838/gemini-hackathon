# Ethyneon handoff

Use this handoff for the current implementation. The [Ethyneon PRD](Ethyneon_PRD_v3.0.md) has the longer product rationale, evidence rules, and recovery design. The [team integration contract](Ethyneon_Team_Split_and_Integration_Contract.pdf) has the ownership and interface details. Carry forward the owner's **no new test cases** requirement for this phase.

## Product direction

Ethyneon is a **VR-first room designer with Shopify shopping built in**. A shopper uploads a photo of their own room, walks through an editable 3D draft, and furnishes it using their preferences and real Shopify products. They can move, rotate, replace, and preview finishes from text commands. Start with text and use the same command contract for voice transcripts later. The browser and headset should render the same room geometry and selected products.

The first room can be a gaming corner in a rectangular bedroom or shared apartment. Keep the shopper's existing items when requested. The same room, placement, and shopping contracts can serve kitchens, bedrooms, living rooms, workplaces, and libraries. A room photo gives the system visible wall lines, openings, obstructions, and surface details. A confirmed measurement anchors scale. Mark obscured features and unmeasured dimensions as estimates and ask for another view when they affect fit.

The hackathon goal is to show a fresh media input changing an actual 3D scene, a Managed Agent review that can catch and repair a placement, and a Decisions API audit before a Shopify test purchase. Use public Shopify product samples while store access is pending. Use the connected development store in test mode for the purchase demo.

## What runs today

| Area | Current state |
| --- | --- |
| Local app | FastAPI with a WebGL [Product Studio](http://127.0.0.1:8000/studio). The studio displays up to four actual Shopify results in a two-column grid and opens the selected product in an orbitable 3D viewer. |
| Text commands | Hydrogen interprets free-form requests such as `gaing chair`. Common slot, rotation, finish, and attachment commands have a direct parser. The browser applies the resulting action to the current product. |
| Shopify reads | Shop MCP searches, fetches a product, and resolves an exact variant. The server checks Shopify IDs and canonical merchant domains and honors an optional store allowlist. |
| Product 3D | Check Storefront `Model3d` media first. Otherwise send up to four distinct Shopify CDN photos and listing facts to Hydrogen for a geometry and material recipe. Python builds a textured GLB. |
| Product edits | Drag to orbit, scroll to zoom, toggle auto-rotate, or type a rotation. Recoloring changes the preview finish. Headrest lookup selects a separate Shopify variant and attaches it to the chair preview with fit marked unverified. |
| Gemini providers | The backend exposes Gemini 4 Interactions, Decisions, and background Argon and Carbon Antigravity submission and polling. Live verification covers Hydrogen and a Decision 1 call. Managed sandbox access for the room workflow still needs a live check. |
| Agora | The server can issue short-lived RTC and Signaling tokens from an App ID and App Certificate. A live join and cross-device synchronization remain pending. |

The next integration is the full-room path. Photo ingestion and calibration, the ADK 2 runner, shared room revisions, WebXR walking, placement checks, and Shopify checkout are pending. The existing studio manages an individual product preview in the browser. The existing cache covers procedural texture tiles. A product GLB cache is part of the room build.

The live browser run handled a typo in the search, opened the top-right **AirFlex2.0 Mesh Gaming Chair with Foot Rest**, used four photos and merchant-listed dimensions, rotated it left 90 degrees, recolored it green, and added the **Clutch Head Rest Pillow** from Shopify. The chair preview used the lower bound of its adjustable height. These results came from public catalog calls, so ranking and stock can change.

## Run and inspect

From the repository root with Python 3.12 and `uv` available, run the following.

```sh
test -x .venv/bin/python || uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
test -f .env || (umask 077 && cp .env.example .env)
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open [Product Studio](http://127.0.0.1:8000/studio) in a WebGL browser, [API docs](http://127.0.0.1:8000/docs) for request schemas, or [health](http://127.0.0.1:8000/healthz) for process status. The studio uses pinned Three.js imports from a CDN. For a live smoke check, enter `Gaming chair`, `Choose top right`, `Rotate it by 90 degrees left`, `Make it green`, then `Add a headrest`. See the [current README](README.md#product-studio).

| Configuration | Meaning |
| --- | --- |
| `ETHYNEON_CONNECTION_TOKEN` | Shared service bearer token. All `/api` endpoints require it. Use a newly generated value for a fresh environment. |
| `GEMINI_API_KEY` | Server-side key from the temporary hackathon account. `GOOGLE_API_KEY` is the fallback setting. The workspace already has a configured Gemini key. |
| `AGORA_APP_ID` and `AGORA_APP_CERTIFICATE` | Matching Agora project credentials used by the token issuer. Both are absent from the current local configuration. |
| `SHOPIFY_SHOPS` | Optional comma-separated allowlist of `myshopify.com` domains. Leave empty for public sample discovery. |
| `SHOPIFY_STOREFRONT_DOMAIN` and `SHOPIFY_STOREFRONT_TOKEN` | Optional store and public Storefront token. The current environment uses public discovery and tokenless Storefront media where available. The development store remains pending. |

Use [.env.example](.env.example) for setting names. Process environment values override the root `.env`. Keep `.env`, the supplied `Geminitoken` file, and account credential emails private. The server reads its Gemini key from configuration. Browser studio calls use the same-origin `/studio/command` loopback route. For an HTTPS headset or phone deployment, add real user and room authorization and use the bearer-protected API between backends. [Three.js WebXR setup](https://threejs.org/manual/pages/webxr-basics.html)

## Code and interfaces

| Code | Responsibilities |
| --- | --- |
| [backend/app.py](backend/app.py) and [backend/schemas.py](backend/schemas.py) | FastAPI lifespan, shared HTTP client, bearer protection, provider and catalog routes, request validation, and consistent errors shaped as `{code, message, retryable, details}`. |
| [backend/connections.py](backend/connections.py) | Provider requests. Gemini uses `v1beta` Interactions with `Api-Revision: 2026-05-20`. Managed runs use `antigravity-preview-09-2026` with background execution. Shopify uses `https://mcp.shop.com/`. |
| [backend/product_models.py](backend/product_models.py) | Exact variant resolution, Shopify media, reference photo selection, Gemini mesh recipe, and model response. |
| [backend/product_dimensions.py](backend/product_dimensions.py) and [backend/product_geometry.py](backend/product_geometry.py) | Labeled overall dimension extraction and deterministic GLB generation. Geometry is floor-aligned, Y-up, with the proxy front facing +Z. |
| [backend/studio.py](backend/studio.py), [studio.js](backend/static/studio.js), and [viewer.js](backend/static/viewer.js) | Typed intent, catalog lookup, browser state, GLB load, camera controls, finish preview, and headrest mounting. |
| [backend/routes/agora.py](backend/routes/agora.py) and [backend/agora_tokens.py](backend/agora_tokens.py) | Agora token response shared by RTC and Signaling, with channel, UID, App ID, and 15-minute expiry. |

| Existing route | Use |
| --- | --- |
| `POST /api/shopify/catalog/search`, `/product`, `/variant` | Search with text or photo, fetch a product, or verify the exact variant for a given shopping context. |
| `POST /api/shopify/catalog/variant/model` | Pass `variant_id`, `country`, and optional `currency`. Use `model.url` for merchant GLB or glTF. Decode `model.data_base64` for a generated GLB. |
| `POST /api/studio/command` | Pass `command`, visible cards ordered by position, and `selected_variant_id`. Returns results, selection, rotation, color, attachment, or a message. |
| `POST /api/gemini/interactions` and `/decisions` | Submit media or text. Interactions supports structured output. Decisions evaluates named `noul`, `choice`, and `score` questions. |
| `POST /api/gemini/managed-runs` and `GET /api/gemini/interactions/{interaction_id}` | Start an Argon or Carbon sandbox job with a token budget, persist the interaction ID, and poll the same run. |
| `GET /api/agora/token?channel=ethyneon-room&uid=42` | Issue a token after the room backend authorizes membership. Use the numeric UID for RTC and its string form for Signaling. |
| `GET /api/providers` | Reports configuration presence. Use a provider call to verify live access. |

The model response records `source`, `model.scope`, and `dimension_source`. A merchant mesh can apply to the full product, so carry the selected variant and its actual finish separately. A generated mesh records the input photos in `reference_image_urls` and the inferred `materials`. `dimensions_m` comes from explicit selected options or overall dimensions in the listing description. A generated asset with unknown dimensions uses a one-unit height as a visual preview.

The [saved walnut table GLB](samples/shopify-walnut-side-table.glb) and its [metadata](samples/shopify-walnut-side-table.json) are the reusable on-disk sample. Exact variant `gid://shopify/ProductVariant/50876631908516` was generated at the listed 19 × 19 × 24 inches. The live chair and headrest examples used `gid://shopify/ProductVariant/42611714130009` and `gid://shopify/ProductVariant/44745114419351`. Refresh their current records when needed.

## Agent responsibilities for the room workflow

| Agent | Model or runtime | Single owned output |
| --- | --- | --- |
| Room Reconstruction | **Gemini 4 Neon** `gemini-4-neon` | `RoomSpec` from raw photos, with walls, floor, openings, existing objects, colors, and evidence. Use Neon to reason about the layout and propose typed geometry corrections. |
| Preferences | **Gemini 4 Hydrogen** `gemini-4-hydrogen` | `DesignBrief` with room purpose, taste, colors, budget, and items the user wants to keep. |
| Placement Planner | **Gemini 4 Argon** `gemini-4-argon` in a Managed Agent | `PlacementSlots` specifying what the room needs, where it can go, orientation, and allowable footprint. Replan the affected slots on a verified conflict. |
| Shopify Finder | **Gemini 4 Hydrogen** and Shop MCP | Exact Shopify `CatalogOffer` records matched to slots. Use real Shopify samples today. Prices, stock, size, and IDs come from Shopify. |
| Scene Renderer | Three.js and **Gemini 4 Hydrogen** on product cache misses | `SceneManifest` with room geometry, resolved assets, poses, and materials. Three.js renders the walkable scene and the browser performs camera and material updates. |
| Interaction | **Gemini 4 Hydrogen** | A typed edit or purchase-review request. Resolve `move`, `rotate`, `replace`, and `recolor` against the current object and scene revision. |
| Guardrail | **Gemini 4 Carbon** `gemini-4-carbon` in a separate Managed Agent | `GuardrailReport` covering physical fit, clear paths, door or drawer motion, compatibility, budget, availability, and missing evidence. The app checks solver results and provider facts before committing. |
| Checkout | **Gemini Decision 1** `gemini-decision-1` and Shopify | A reconciled purchase review and checkout state. Decision 1 provides the semantic audit. Authorized backend tools handle Shopify writes. |

Hydrogen already powers studio intent and product meshes. Neon, Argon, and Carbon are selected for the room roles above. Antigravity supports distinct model configurations and background sandbox runs. [Gemini model guide](https://aistudio.google.com/docs/choosing-a-model) · [Antigravity model selection](https://aistudio.google.com/docs/antigravity-agent#model-selection) · [Decisions API](https://aistudio.google.com/docs/decisions-api)

## Workflow, scene state, and rendering

1. **Capture and calibrate.** Accept a room photo or later an Agora still. Run Decision 1 for usable photo coverage and route uncertain scans to a better angle or a measurement. Run room reconstruction and preference collection independently, then bind both to the same room session.
2. **Plan and find.** Use ADK 2 dynamic nodes for the allowed roles. Run Argon's placement plan, fan out independent Shopify searches, and join the offers by slot. Let the plan depend on the room, hard constraints, and user taste. Validate operation names and dependencies server-side. Persist a run ID and emitted artifact versions before any pause or resume. [ADK dynamic workflows](https://adk.dev/graphs/dynamic/) · [ADK human input](https://adk.dev/graphs/human-input/)
3. **Check and publish.** Give Carbon a separate read-only evidence packet with the proposed pose, room dimensions, layout, relevant photos or rendered views, and exact offers. Run deterministic clearance and collision checks against the same geometry. Hold unsupported fit results as unknown. Keep the accepted room visible while an edit is pending, and commit only if its base revision still matches.
4. **Render from data.** Use one metre-based, Y-up coordinate frame for walls, floors, openings, furniture transforms, and collision math. Give identifiable objects stable IDs. Build both desktop navigation and the WebXR walk-through from the same `SceneManifest`. The current preview is a starting point for product asset loading and material changes. [Three.js WebXR setup](https://threejs.org/manual/pages/webxr-basics.html)
5. **Reuse assets.** On placement or replacement, check a product asset cache keyed by exact variant, relevant source-photo, listing and dimension fingerprints, and recipe version. Prefer Shopify's original `Model3d` asset. For the fallback, use the selected variant photo first, rank alternate angles and close-ups, infer procedural textures, and preserve listed dimensions. Keep local transforms and finishes as scene data so a move or paint action reuses the same mesh. Refresh live commerce facts independently from assets. [Shopify product media](https://shopify.dev/docs/api/storefront/latest/queries/product)
6. **Respond to edits.** A camera rotation is local. An object rotation or move rechecks the affected clearance. A replacement searches Shopify again and asks the cache for the new variant. Preview recolor changes the material while preserving the purchase ID. A request for a purchasable finish resolves the exact Shopify variant.

The authoritative backend should retain `RoomSpec`, `DesignBrief`, `WorkflowPlan`, `SceneRevision`, `CatalogOffer`, `FitReport`, `CartReview`, and `CheckoutAudit`. Include source and freshness on offers, base revision and action ID on edits, and evidence on measurements. The planned hackathon persistence layer is SQLite with durable ADK session events. Strip photo metadata before storage and scope room media and private checkout links to the user.

For recovery, preserve the last accepted state and retry or replan the affected step within the run's budget. An uncertain scan asks for targeted evidence. An unavailable item reopens its slot. After a polling timeout, recover the Managed Agent run from its saved interaction ID. A timed-out submission with no returned ID remains unresolved. Hold a proposed revision when its reply is stale or a required constraint is unresolved. Hold checkout when the audit is stale or low-confidence. [Managed Agent background runs](https://aistudio.google.com/docs/antigravity-agent#background-execution)

## One checkout confirmation

1. Start a purchase review when the shopper explicitly asks to buy. Select the exact scene items and quantities, refresh their Shopify variants, stock and pricing, and surface required fit issues. A Shopify cart or checkout belongs to one merchant. Public samples can populate the scene, while the hackathon purchase uses the connected development store. [Shop checkout](https://shop.dev/docs/checkout)
2. Reconcile the current Shopify checkout to the accepted scene, purchase selection, variant options, quantities, currency, charges, and applicable budget. Send the relevant evidence to Decision 1. Ask whether the selection and requirements match and whether to continue, refresh, repair, or ask the shopper. Keep buyer details and payment data in the commerce layer. The code verifies IDs, amounts, versions, and approval separately from the decision score. [Decisions API](https://aistudio.google.com/docs/decisions-api)
3. Present one final purchase confirmation against the exact review. Where Shop supports direct completion and returns a ready checkout, use the shopper's chosen payment method and submit once after approval. For hosted test checkout, open the verified Shopify checkout and let Shopify supply the final payment confirmation. A material change to the selection, total, or accepted room invalidates the approval. [Shop checkout completion](https://shop.dev/docs/checkout#complete-a-checkout) · [Storefront cart creation](https://shopify.dev/docs/api/storefront/latest/mutations/cartCreate) · [Shopify test orders](https://help.shopify.com/en/manual/checkout-settings/test-orders)
4. Record an order only when Shopify confirms it. If completion times out, read the existing checkout or await a verified order event and preserve the pending result. Reconcile before any later write. [Shop checkout recovery](https://shop.dev/docs/checkout#if-the-outcome-is-unclear)

## Three projects and their handoffs

| Project | Owns | Supplies |
| --- | --- | --- |
| **Room Studio** | Photo upload, 3D room shell, desktop and headset views, object selection and controls, device pairing, and feedback in the scene. | Source media, selected object and edit event, rendered evidence, and the accepted scene revision shown on each device. Agora RTC carries camera and audio when added. Agora Signaling carries revision notifications. |
| **Design Studio** | Gemini roles, ADK 2 workflow and resumable runs, Decisions API, and Argon and Carbon sandboxes. | Typed room, brief, placement and review artifacts, and stage updates. |
| **Shopify Checkout and Fit** | Backend state, sample and development-store discovery, verified offers, model assets and cache, deterministic fit, cart reconciliation, and order status. | Exact Shopify variants, product dimensions and assets, fit verdict, audited review, and Shopify-confirmed transaction outcome. |

Agree on shared objects and wire shapes before splitting code into separate projects. Keep the backend as the writer of accepted state. Pass validated revisions and action IDs so reconnecting clients and late agent replies can converge on the current room. For Agora, begin with one phone capture and a desktop or headset viewer. Pair to the same room, then publish the committed revision over Signaling. [Team integration contract](Ethyneon_Team_Split_and_Integration_Contract.pdf) · [Agora SDK token reference](https://docs.agora.io/en/api-reference/api-ref/server-sdk/platform/python)

## First implementation slice

Connect one real photo and one confirmed measurement to an editable three-dimensional room. Place one verified Shopify sample from the catalog, show the same item through the desktop walk-through and WebXR where supported, and reuse its mesh on a text move or rotation. Add the Argon proposal and independent Carbon clearance report around one measured doorway. Then connect the development store and drive that exact selection through the Decisions audit and one test purchase confirmation.

Carry forward two observed failures from the product preview. Gemini rejected a deeply constrained response grammar, so the provider schema uses simple shapes and Pydantic enforces detailed local bounds. The five-spoke chair used yaw angles greater than 180 degrees, so the geometry recipe accepts a full turn. The live flow worked after those changes. For the hackathon, capture a real room or clip that needs a second viewpoint or a corrected measurement, show which evidence changed the result, and record the observed latency and failure. Use the [builder guide](%5BEXTERNAL%5D%20Build%20with%20Gemini%204%20NYC%20Hackathon%20Builder%20Guide.pdf) for submission details.
