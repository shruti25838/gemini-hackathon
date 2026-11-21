# Ethyneon 3.0

**Product requirements and technical architecture**  
**Ar · C₂H₂ · Ne** · Ethyne–argon–neon motif  
Hackathon edition · November 21, 2026

## Product overview

Ethyneon turns a photo of the shopper’s own room into editable 3D geometry. The shopper points their phone where they want a change and explains what they need. Ethyneon generates a workflow using the room evidence, budget, and preferences. It finds verified Shopify products, checks the layout, and puts the design on both devices. The shopper can move furniture, change finishes, or switch the room’s purpose. Every purchasable item in the scene links to an exact Shopify variant the shopper can review and buy through hosted checkout.

**The first user** is a grad student upgrading a small gaming corner in a shared apartment. They already own one monitor. They want a dual-monitor setup under **$1,200 USD in merchandise**, with a clear doorway and a room that can still serve another purpose. The camera shows which monitor they mean and how the door opens. Their voice sets the budget, preferences, and priorities. Their confirmed measurements establish the scale of the fit preview.

**The judging moment** starts with a fresh room photo or a still from the phone camera. The judge sees their walls, window, and doorway take shape in 3D, corrects a measurement, and opens the real door on camera. Ethyneon replays that motion and shows where a proposed desk overlaps the swing. One Managed Agent proposes a layout. A second finds a collision in a separate sandbox, and the first repairs the affected placement. Then the judge says, “Make that wall olive, keep my monitor, and show me the walnut desk.” Both screens update, the activity panel shows which steps ran, and the checkout review contains the exact walnut variant.

The hackathon build generates an approximate rectangular room from one image and concentrates the fit demo on one measured corner and one moving door. The user can correct the geometry before using it for fit checks. A labeled preset room is available as a fallback. The payment path uses a Shopify development store in test mode. The project mark **Ar · C₂H₂ · Ne** is a proposed chemical motif representing Argon, Carbon, Hydrogen, and Neon.

## Scope and release priorities

| Priority | Deliverable | Acceptance |
| --- | --- | --- |
| **P0 · Hackathon** | Photo or Agora still to an editable 3D room, two devices, voice edits, adaptive ADK 2 workflows, Managed Agent placement checks, Shopify-only products, and a Decisions API audit before hosted test checkout. | A judge supplies fresh media and a preference. A voice-directed move shows the style assessment, clearance checks, and any suggested repair. The approved Shopify cart passes the checkout audit. |
| **P1 · Product depth** | Two room configurations sharing a shopping list, a second camera angle, styled previews and textures from Nano Banana, optional Lyria ambient audio, product comparison, order-status integration, signed approval records, and spatial audio for collaborators. | Reuse the same verified scene and catalog records. Identify generated imagery, tentative fit, data source, and payment status. |
| **P2 · Expansion** | Irregular room shapes and higher-fidelity reconstruction, multiroom layouts, calibrated AR and VR walkthroughs, additional room profiles, and shopping from multiple verified Shopify merchants. | Each preview carries its fit evidence and confidence. Each checkout stays associated with the Shopify merchant and exact variants that can fulfill it. |

The door swing is the single moving constraint required for the hackathon. The first extension tests a chair pulled back or a second room configuration with the same products. A confirmed measurement sets the scale of the corner. A fit result also requires confirmation of the relevant hinge, swing, boundaries, and documented product dimensions. Surface previews and generic furniture proxies remain available when detailed geometry is unknown.

## Shopper experience and room editing

1. **Join and build your room.** Open Ethyneon on a laptop and join by QR code from a phone. Upload a room photo or capture a clean frame from the Agora camera. The app builds a draft 3D room beside the photo. The shopper can correct dimensions, surfaces, and openings. The live guide then asks them to point at the item to keep and open the door.
2. **State the goal.** The shopper speaks a budget, a use for the room, and personal preferences. Ethyneon displays its understanding as editable constraints. Hard requirements include the merchandise budget, fixed or locked objects, and required clearance. Preferences include finish, lighting, and aesthetic style.
3. **Watch the workflow run.** The laptop shows the scan, the current 3D scene, and a short activity panel. It reports evidence requests, product lookups, solver results, and proposed changes as they happen. The shopper can continue talking while longer steps run.
4. **Edit the result.** Select, drag, rotate, or remove an object. Speak a command such as “Make the wall olive” or “Move the desk by the window.” Preview placement conflicts and accept an alternative. Compare the photo, the original 3D room, and the furnished room. Undo restores an earlier design as a new revision.
5. **Review and buy.** A voice command can prepare a proposed cart. The shopper confirms the exact items and current prices. On **Continue to checkout**, Ethyneon compares the workflow, the approved selections, and the live Shopify cart, then runs the Decisions API checkout audit. Shopify hosts the test checkout after those checks pass.

| Edit | Scene and workflow behavior |
| --- | --- |
| “Make that wall olive and warm up the lights.” | Resolve the selected wall, update its material and the light settings, and record the revision for undo. The product selections carry forward. |
| “The desk wall is 3.2 metres, and the window is farther left.” | Record the measured wall and corrected opening in `RoomSpec`, regenerate the affected geometry, and recheck any dependent placement. |
| “Move the drawer unit under the window.” | Preview the requested position. Managed Agents evaluate how it looks, whether people can still pass, and whether the unit or an open drawer conflicts with nearby objects. Show the cause of any conflict and offer a checked alternative. |
| “Make this desk walnut.” | Resolve the exact Shopify finish and refresh availability, price, and dimensions. Rerun the fit check only if the dimensions or placement change. |
| “Keep the lamp and turn this into a reading room.” | Lock the lamp and generate a new plan for the same space. Save the current version so the shopper can switch back. Reading-room support uses the same scene and workflow contracts. |
| “Add those two things to my cart.” | Resolve both selections or ask which items the shopper means. Prepare an itemized review for explicit confirmation. |

The editor supports reversible surface, lighting, furniture, and layout changes. Use corrections to align the preview with the real room. Let the shopper create a separate hypothetical layout when they want to explore moving a wall or doorway. Each version carries its own measurements and evidence status.

### Managed Agent review for furniture moves

“Move the drawer” starts an ADK 2 placement workflow. Resolve the selected object and destination first. If several objects match, highlight them and ask the shopper to choose. Distinguish moving the drawer unit from opening an individual drawer. Render the requested position as a tentative 3D preview and keep the accepted room available while checks run. Dropping an object after a manual drag uses the same workflow.

ADK starts two Managed Agent tasks for the proposed furniture move using the requested pose, measured geometry, the existing scene, and current user preferences. It selects the applicable checks for that move, such as drawer extension and doorway access when the unit is near a walking route.

| Managed Agent | What it checks | What the shopper sees |
| --- | --- | --- |
| **Argon · style and alternatives** | Inspect the current and proposed renders for proportions, alignment, lighting, and the shopper’s taste. If the requested pose conflicts with a requirement, look for a nearby alternative that preserves their intent. | A short design assessment grounded in their preferences and a side-by-side preview of any alternative. |
| **Carbon · movement and interference** | Run the room checker to see whether the intended walking route still has sufficient clearance, the unit overlaps a wall or another object, and doors or drawers can open where their motion is known. Identify items the move would make harder to reach. | Highlight the walkway, object, or opening that would be blocked. Show measured clearance where the inputs support it. |

The server verifies the relevant clearance and collision checks against the current room revision. A style preference can rank feasible positions. A blocked route or object conflict sends the proposal to a bounded repair. For example, the assistant might say, “Under the window fits your style, but the open drawer would narrow the path from the door to your desk. Want to see it beside the desk?” Run the same checks on an alternative and let the shopper choose it. If a measurement or opening range is missing, label the affected check unknown and request the needed evidence. Commit and sync an accepted move through Agora. A position-only edit uses the same selected item and can reuse the verified catalog record.

## Room photo and procedural 3D

1. **Capture and prepare.** Accept an uploaded room photo or a still from the phone’s live camera. Show consent before analysis. In the browser, decode and re-encode the image to remove EXIF and GPS metadata, resize it to the configured limit, and convert HEIC where supported. Offer camera capture or a supported format if decoding fails.
2. **Check image quality.** Run Decisions API questions for room relevance, visibility of the needed features, quality, and whether an identifiable person is visible in the design image. Ask for an empty-room frame, a crop, or a clearer angle when appropriate. Drop rejected images from the app’s storage. Camera and mic permissions apply separately to the live Agora session.
3. **Draft a `RoomSpec`.** Gemini 4 Neon proposes a rectangular room with approximate dimensions, wall and floor regions, door and window openings, existing items, and free zones. It returns typed fields with confidence and evidence references. Browser code samples colors from the image regions and labels them *sampled from photo* because lighting can affect them. [Gemini 4 Neon](https://aistudio.google.com/docs/models/gemini-4-neon) · [Gemini image understanding](https://aistudio.google.com/docs/image-understanding)
4. **Verify and build.** Validate the draft on the server. Three.js builds the walls, floor, and openings from the validated data. Show editable numerical dimensions, openings, and colors alongside the source image. The user confirms one real length to anchor the model and corrects the other measurements that affect the planned check.
5. **Use live motion.** Agora video supplies a separate clip of the door opening or a person showing a relevant path. Align the observed hinge, swing direction, and clearance with the correct opening in `RoomSpec`. Ask for a targeted view or measurement if the evidence is insufficient. The annotated motion becomes an input to the Managed Agents and the replay.
6. **Persist the design.** Save the validated geometry, revisions, and derived evidence. Delete room photos at session end from storage controlled by the app. Use separately consented media for evaluation. If the input fails, offer another photo, manual dimensions, or a labeled preset. Keep the last valid room available while correcting an invalid draft.

### RoomSpec and fit rules

| Field | Contract |
| --- | --- |
| `dimensions_m` and `scale_reference` | Width, depth, and height in metres, with source per dimension. An example measured desk wall is `width = 3.2 m`. An estimated depth can remain estimated after that width is confirmed. |
| `walls`, `floor`, and `openings` | Stable wall and floor IDs, colors and their sources, and an opening’s wall, type, offset, width, height, and sill where applicable. A door can carry hinge and motion evidence. |
| `existing_items` and `free_zones` | Identified items, bounds if known, and whether the shopper wants to keep them. Code clips free zones to the valid room and treats locked items as placement constraints. |
| `evidence` and `status` | Capture frame and region references, observed features, estimated or confirmed geometry, uncertainty, and corrections. Preserve user-confirmed values over later estimates until the user changes them. |

Use one room-local coordinate system in metres for geometry, placements, door motion, and the fit solver. Define each opening offset from the start of its wall. Reject non-finite dimensions, unknown wall or opening types, openings outside a wall, and free zones outside the room. Set supported room-size bounds for the demo. A new draft only replaces the active model after validation. Apply `apply_room_spec`, `set_wall_color`, `set_room_dimension`, and `set_opening` through the same validated revision mechanism as furniture edits.

Fit uses deterministic geometry and documented product dimensions. Preserve the distinction between **fits**, **tight**, **does not fit**, and **unknown**. A photo establishes an approximate layout, and one scale reference confirms only the dimension it measures. The app can issue a checked fit result when the relevant room bounds, opening, clearance, and product dimensions are supported. Missing dimensions produce an unknown result. Product dimensions can come from configured Shopify metafields or attributable merchant listing data.

## Workflow generation and execution

**Room evidence determines which steps are needed. User requirements determine what those steps optimize.** A clip that shows a hinge and swing can add a doorway check. A locked monitor adds a preservation constraint. A color-only edit needs a material update. A changed budget can reopen the affected shopping branches while preserving compatible selections.

Gemini 4 Neon proposes a typed workflow plan against a registered set of operations. The backend verifies operation names, input types, data dependencies, access, resource limits, and required checks. The generated portion is a directed acyclic graph. Repairs run in a bounded loop owned by the application. ADK 2 executes validated operations using a `Workflow` with dynamic `ctx.run_node()` calls. It can fan out independent branches, join their outputs, and resume a paused run from persisted state. [ADK graph workflows](https://adk.dev/graphs/) · [ADK dynamic workflows](https://adk.dev/graphs/dynamic/) · [ADK resumability](https://adk.dev/runtime/resume/)

```text
Uploaded photo or Agora still → Decision 1 → Neon RoomSpec → Editable 3D room
                                                                  │
Phone camera and mic ── Agora RTC ── Second device                 │
        ├─ Active speech ── ADK Live ── Validated scene edits       │
        └─ Motion evidence and user brief ───────────────────┐    │
                                                             ▼    ▼
                                                        Neon workflow plan
                                                                  │
                                                    ADK runs approved steps
                                                    ├─ Shop MCP discovery
                                                    ├─ Argon proposes a layout
                                                    └─ Carbon tests the layout
                                                                  │
                                     Server checks fit, price, and revision
                                                    ├─ 3D edit via Agora Signaling
                                                    └─ Review → Checkout audit → Shopify
```

### Example plan for a fresh room scan

After the user approves the photo-derived room preview, they point at a monitor and say, “Keep this one. Build me a dual-monitor setup under $1,200 and keep the door clear.” An illustrative validated plan looks like this.

```json
{
  "room_revision": 3,
  "scene_revision": 7,
  "goal": "dual_monitor_gaming_corner",
  "constraints": {
    "merchandise_budget_minor": 120000,
    "currency": "USD",
    "locked_objects": ["existing_monitor"],
    "keep_door_clear": true
  },
  "steps": [
    {"id": "room", "op": "inspect_room_motion", "after": []},
    {"id": "shop", "op": "discover_shopify_variants", "after": []},
    {"id": "scale", "op": "confirm_measurement_if_needed", "after": ["room"]},
    {"id": "plan", "op": "search_layouts", "after": ["shop", "scale"]},
    {"id": "test", "op": "test_layout_motion", "after": ["plan"]},
    {"id": "preview", "op": "prepare_scene_preview", "after": ["test"]}
  ]
}
```

The example shows generated dependencies. The runtime supplies trusted inputs and inserts any mandatory checks itself. A lighting-only request can run a single validated scene operation. A furniture move runs the managed placement checks for the affected routes, openings, and neighboring objects. A desk swap can reuse the room evidence and product shortlist, then rerun the price, fit, and render steps. A new photo or corrected `RoomSpec` invalidates dependent placements. The plan stores a base revision so a late result cannot overwrite a newer edit.

### Decisions API and human input

| Gate | Input and use | Recovery |
| --- | --- | --- |
| Room photo | Score whether it is a room photo, whether a person is visible, and whether the image is usable for layout. | Ask for a clean photo, show a labeled preset, or offer manual room dimensions. |
| Scan coverage | Use `gemini-decision-1` with the captured media to score whether the relevant floor and doorway are visible and which follow-up is needed. | Show a targeted rescan instruction or request a specific measurement. |
| Target selection | Check whether an utterance, current screen selection, and camera evidence resolve references such as “that monitor.” | Highlight candidates and let the user tap the intended one. |
| Request and product evidence | Route a request to a style edit, placement change, search, or cart review. Score uncertain visual attributes separately from catalog facts. | Ask a short clarifying question or label the attribute inferred. |
| Checkout audit | Decision 1 evaluates whether the live Shopify cart corresponds to the user’s confirmed purchase, current scene, and workflow constraints. Code separately checks the exact lines, amount, approval, and revisions. | Route discrepancies to the managed recovery workflow. Require a fresh audit after refresh, repair, or clarification. |

Use a `choice` or a rubric where it fits the gate. `noul` is the API’s yes/no type. Confidence thresholds are tuned on observed cases. A high model score can select a processing path, while verified measurements, exact commerce records, and user approvals control state changes. [Gemini Decisions API](https://aistudio.google.com/docs/decisions-api)

When a workflow needs an answer, an ADK `RequestInput` node supplies the marked frame, the requested measurement, and a response schema. Persist the run and interruption ID. The authorized user can respond on the laptop after the phone disconnects, and the server resumes the same run. Use the documented resume behavior for dynamic parent nodes and keep durable session events available across restarts. [ADK human input](https://adk.dev/graphs/human-input/) · [ADK dynamic workflow interruptions](https://adk.dev/graphs/dynamic/)

## Gemini models and Managed Agents

| Role | Model or runtime | Output and responsibility |
| --- | --- | --- |
| Live guide | **Gemini 3.8 Live** through ADK `run_live()` | Streams spoken interaction, accepts interruptions, and proposes typed commands. Slow catalog and solver work can continue in background tasks while the guide listens. |
| Catalog scout | **Gemini 4 Hydrogen** | Runs quick, parallel searches and exact variant lookups through Shopify catalog tools. Uses the cropped product image as an optional Shop MCP similarity input and reports missing facts. |
| Room investigator and workflow planner | **Gemini 4 Neon** | Generates the typed `RoomSpec` from a clean photo, uses agentic video understanding on selected clips, and builds a typed workflow from evidence and intent. A targeted higher-thinking follow-up can investigate a conflict between views. |
| Layout and recovery planner | **Gemini 4 Argon** in an Antigravity Managed Agent | Runs the layout solver, compares variants and positions, assesses how proposed moves look against user preferences, and proposes recovery operations when a step fails. |
| Independent tester | **Gemini 4 Carbon** in a separate Antigravity Managed Agent | Tests circulation, access, object interference, and door or drawer clearance. Investigates difficult cases such as conflicting measurements or a cart that disagrees with the confirmed scene. Returns checks for the app to rerun. |
| Visual preview | **Nano Banana 2.1**, when included | Generates a styled image from a confirmed room frame, the accepted layout render, and catalog images. The image is labeled as a visualization. The 3D manifest carries measured positions and product identities. |
| Ambient audio | **Lyria**, when included | Generates an optional room ambience loop. Keep a separate control for volume and mute during live speech. |

Use ADK 2.15’s current live tool behavior, typed workflow nodes, versioned artifacts, and multi-run evaluation. Verify the exact pinned Python package and model access against the hackathon project before integration. Gemini 4 Interactions calls receive the raw audio and video. The documented Antigravity Managed Agent interface accepts text and inline images, so transfer versioned scene data, confirmed measurements, and selected evidence frames to the sandboxes. [ADK 2.15 release](https://github.com/google/adk-python/releases/tag/v2.15.0) · [Gemini Live](https://aistudio.google.com/docs/models/gemini-3.8-live) · [Gemini 4 models](https://aistudio.google.com/docs/choosing-a-model) · [Agentic video](https://aistudio.google.com/docs/video-understanding) · [Antigravity inputs and model selection](https://aistudio.google.com/docs/antigravity-agent) · [Gemini image generation](https://aistudio.google.com/docs/image-generation)

### Sandbox proposal, test, and repair

- **Inputs.** Mount a versioned solver and its instructions or skills. Supply room evidence, read-only verified Shopify offers, the user’s budget, and locked items. The sandbox proposes selections from the supplied catalog IDs. Use sandbox hooks to validate report files or record solver executions. Keep payment permissions in the application’s commerce endpoints. [Managed Agent hooks](https://aistudio.google.com/docs/agent-hooks)
- **Proposal.** Argon searches viable poses and variants, checking footprints against room bounds, fixed objects, and a swept area for the observed door motion. It records the solver version, inputs, layout, rejected candidates, and whether fit is checked or unknown. For the two-configuration extension, it tries both room states against a common shopping list.
- **Independent test.** Carbon receives the manifest, evidence, and the checker in a fresh sandbox. It can probe intermediate door positions, compare documented versus assumed dimensions, or test clearance at the edge of a specified tolerance. Its report names the failing motion, item, and source data. For the hackathon, a door-swing overlap is sufficient to make the difference visible.
- **Acceptance and repair.** The server reruns the fit check, checks every proposed shoppable item against the verified Shopify catalog, and refreshes the exact variant from its Shopify store. A failure returns the exact constraint to the planner for a limited repair. If no feasible solution exists, the user decides which constraint may change. Sandbox output is validated before it can change the shared scene.
- **Progress and budgets.** Run long interactions in the background with a bounded token budget. Persist the interaction ID and publish completed tool and solver events to the UI. A later request can resume a completed sandbox interaction when the artifacts and base revision are compatible. A superseded workflow leaves the latest accepted room available.

The recent Argon SF projects *LabTwin* and *Morf* demonstrate the value of an inspectable simulation and visible correction. *SpatialTwin* is an adjacent precedent for walkthrough video and sandbox testing. Ethyneon applies those patterns to a live consumer decision made against an exact product and price, with a reversible scene edit and a verified cart. The agent’s proposal and independently reproducible failure are the Managed Agents prize demonstration. [SF winners recap](https://www.teapaw.org/cerebral_valley/status/2118858875374125548) · [SpatialTwin](https://cerebralvalley.ai/e/gemini-4-argon-sf/hackathon/gallery/77) · [Google Research on Antigravity teamwork](https://research.google/blog/advancing-science-engineering-with-antigravitys-teamwork-feature/) · [Managed Agent skills](https://aistudio.google.com/docs/custom-agents) · [Background execution](https://aistudio.google.com/docs/background-execution)

## 3D scene, Agora, and live activity

**Frontend.** Use React, TypeScript, and Three.js with React Three Fiber. Generate the shell from `RoomSpec` and use a small GLB asset set for products. A deterministic scene reducer applies accepted patches to geometry, material, light, object pose, and variant. Include orbit and zoom, camera presets for desk, doorway, and whole room, and daylight, evening, and RGB lighting presets. Selecting an object highlights its product card. Label representative geometry and display the exact listing image and variant alongside it. The door-swing replay links to its evidence frame. The immersive extensions can add a recorded orbit, AR anchors where supported, or spatial audio for collaborators.

**Agora.** RTC carries the phone’s camera and mic and the other participant’s live tracks. Joining from a second device enables a remote collaborator to talk, point out a feature, and watch edits. Agora Signaling sends scene-revision notifications and transient cursor or selection events. Clients fetch authoritative state using a revision number when they reconnect or miss an update. Test camera start, device switching, track loss, audio feedback, and reconnect on the actual demo devices. [Agora RTC for web](https://docs.agora.io/en/realtime-media/rtc/get-started-sdk/web) · [Agora Signaling for web](https://docs.agora.io/en/realtime-media/rtm/quickstart/web)

**Media to Gemini.** The capture client supplies selected frames, bounded clips, and the active editor’s permitted audio for model analysis. RTC keeps the participants connected. An ADK live session handles the assistant conversation independently of the long design job. Tag media by device and capture time, preserve enough frames to replay evidence, and validate any reference to “there” against a selected object, frame, or tap. Keep ordinary controls available if voice or visual targeting is ambiguous.

**Live activity.** The Shopify Live View attachment inspires an activity view for the room itself. Select the desk to see its history from placement through variant swap, cart, and checkout. The panel shows actual ADK and app events such as *waiting for measurement*, *door swing overlaps*, *walnut variant verified*, *cart needs review*, *checkout checks passed*, and *checkout opened*. Shopify Live View remains a merchant view of aggregate store activity. Ethyneon uses its own room events and checks Shopify for the specific order outcome. The host can share a demonstration view with sensitive checkout details hidden. [Shopify Live View](https://help.shopify.com/en/manual/reports-and-analytics/shopify-reports/live-view)

### State contracts

| Record | Required fields and source |
| --- | --- |
| `RoomSnapshot` | Media references, source device and timestamps, observed objects, fixed features, measured anchors, units, and evidence status. |
| `RoomSpec` | Validated procedural geometry, wall and floor colors and sources, openings, existing items, free zones, user corrections, and field-level provenance. |
| `DesignBrief` | Room use, locked objects, maximum merchandise subtotal and currency, required items, preferences, and user-confirmed constraints. |
| `WorkflowPlan` | Plan ID, approved operation names, dependencies, input and base revisions, state, and references to resulting artifacts. |
| `SceneRevision` | Parent revision, validated object placements, fixture and material state, selected exact variants, and fit status. Undo restores a prior design in a new revision. |
| `PlacementReview` | Base scene revision, selected object and requested pose, style assessment, affected paths and openings, fit evidence, alternatives tested, and accepted pose when selected. |
| `CatalogOffer` | Verified Shopify store, Shopify API surface, product and variant IDs, selected options, amount in minor units, currency, availability, Shopify listing link, dimensions when documented, and freshness timestamp. |
| `FitReport` | Room and scene revisions, solver version, geometry and assumptions, checks run, minimum clearance if measured, rejected placement, and supporting evidence. |
| `CartReview` | User, Shopify store, approved product subset and exact variant quantities, relevant brief and scene revisions, merchandise subtotal and currency, approval record, and cart reference. |
| `CheckoutAudit` | Workflow run and accepted scene revisions, approval fingerprint, Shopify cart fingerprint, deterministic reconciliation results, Decisions API answers and confidence, outcome, and expiration. |
| `RecoveryRun` | Failed step and error class, triggering revision, active interaction ID, allowed actions, attempt count, last verified state, and outcome or pending input. |

The FastAPI backend is authoritative. Use persistent ADK sessions and a small database for revisions, action IDs, cart decisions, and run records. SQLite suits a single-instance hackathon deployment. Store scan clips and reports as session-scoped artifacts. Validate proposed actions server-side and check the base revision when committing. Treat guest suggestions as input to the authorized editor. Keep private checkout URLs, credentials, and media access scoped to the right user. Apply session-bound retention to raw room photos and short-lived access to the live evidence clips used for the replay.

## Shopify discovery and checkout

**Shopify is the sole product source.** All shoppable recommendations, room placements, replacements, and cart lines require an exact Shopify variant verified for the selected store. The backend enforces this at catalog intake, when committing the scene, and before creating a cart. Keep the shopper’s existing furniture as room context. A generated image or a generic 3D asset can illustrate a product whose identity comes from Shopify.

**Shopify discovery.** Use Shop MCP `search_catalog` for requirements or an approved photo crop, filter to the configured Shopify stores, and use `get_product` for the exact size and finish. Validate the returned store and variant before offering it. Real price, availability, and merchant-listed dimensions come from Shopify. If the shop lacks a verified match, show another Shopify variant or leave that spot unfilled. If a product lacks the facts for a compatibility check, show which specification the check needs. [Shop MCP discovery](https://shop.dev/docs/search)

**Development-store checkout.** For the demo, the selected store is the connected Shopify development store. Use its Storefront API to verify each selectable variant and create the cart. Query its Storefront catalog directly if Shop MCP does not surface that store. Resolve each offer to a verified Storefront variant before sending it to a Storefront cart. Use the identifier returned by the API that will receive the cart request. An expansion can support one checkout per verified Shopify merchant. [Storefront ProductVariant](https://shopify.dev/docs/api/storefront/latest/objects/ProductVariant) · [Storefront cartCreate](https://shopify.dev/docs/api/storefront/latest/mutations/cartCreate) · [Shop MCP checkout](https://shop.dev/docs/checkout)

1. Voice or the UI prepares a review of the items the shopper explicitly wants to buy. Show exact variants, quantities, seller, merchandise subtotal, and any known charges. For “buy just the walnut desk,” verify that approved subset of the scene. Keep owned furniture and unselected items outside the purchase scope.
2. Refresh live stock and price. If either changes, update the review and ask for confirmation of the new version. **Confirm and add to cart** binds approval to the shopper, exact variant quantities, amounts, currency, and accepted scene and brief revisions.
3. Create or update the Shopify cart once for that approved action. Check the returned lines and totals. If the request outcome is unclear, reconcile the Shopify cart before any further mutation.
4. On **Continue to checkout**, fetch the current Shopify cart, reconcile it against the approval, and run the Decisions API audit described below. A passing audit applies to that cart and approval only. Recheck the revisions and fingerprints before privately opening Shopify’s hosted test checkout.
5. Distinguish *cart ready*, *checkout audit pending*, *needs review*, *checkout opened*, *payment unconfirmed*, and *order confirmed*. Confirm payment only from a scoped order lookup or verified Shopify order event that matches the purchase. Keep payment credentials on Shopify’s hosted page.

For the hackathon, purchases use test mode. The review states whether the budget applies to merchandise or the final total. If shipping or taxes are unknown, show the subtotal and leave those charges for the shopper to review at checkout. An explicit final-total limit stays unresolved until the required total is available. AP2-style signed approval records are a later enhancement. [Shopify cart](https://shopify.dev/docs/api/storefront/latest/objects/Cart) · [Shopify test orders](https://help.shopify.com/en/manual/checkout-settings/test-orders) · [Shopify order events](https://shopify.dev/docs/api/webhooks/latest#list-of-topics-orders/paid)

### Decisions API checkout audit

Make this a required ADK node after cart reconciliation and before the hosted handoff. Give `gemini-decision-1` a minimal evidence packet containing the confirmed purchase request and hard constraints, accepted workflow and fit status, selected scene objects and Shopify variants, approved review, and freshly read cart lines and amounts. Use exact normalized IDs and quantities. Keep the buyer’s contact details, credentials, private checkout link, and payment data outside the packet.

| Question | Answer type | What Ethyneon uses it for |
| --- | --- | --- |
| `purchase_scope_matches` | `noul` | Check whether the cart includes what the shopper selected and approved, with the chosen finishes, and excludes owned items they marked to keep. |
| `required_constraints_resolved` | `noul` | Check whether the accepted workflow resolved the shopper’s hard constraints for these products or left fit, compatibility, or another required condition unresolved. |
| `next_action` | `choice` | Choose from `ready`, `refresh_cart`, `repair_plan`, `ask_shopper`, or `hold_checkout`. Confidence determines whether Ethyneon can use the chosen route or must ask for review. |

The server independently compares store, exact variant IDs and quantities, currency, stock, amounts and applicable budget, fit status where required, approval, and revision. A `ready` route requires those checks to pass, both affirmative probabilities and their confidence to meet the tuned thresholds, and a confident `ready` decision. Record the result and bind it to the approved review and live cart fingerprints. Before handoff, a subsequent edit, changed cart, refreshed amount, or expired audit requires a new check. Low confidence, disagreement, or unavailable Decisions API pauses checkout while preserving the verified cart. Any repaired workflow returns to reconciliation and a fresh Decisions API audit. A changed purchase requires renewed shopper approval. If the user returns from a hosted checkout to change the purchase, start a fresh review. [Gemini Decisions API confidence](https://aistudio.google.com/docs/decisions-api#confidence)

## Managed recovery and failovers

All supported failure paths report a typed failure into an ADK `RecoveryWorkflow`. The backend immediately holds the affected action and retains the last verified room, scene, and cart state. Pass the failed step, source revision, evidence, provider status, and checked retry count to the Argon Managed Agent when available. It proposes a typed recovery action from the allowlist. Carbon can independently test a proposed repair when the outcome depends on geometry, conflicting evidence, or a checkout mismatch. Code validates the proposed action and executes it through the original workflow tools.

Recovery can request a targeted rescan or measurement, switch to a supported input mode, retry an idempotent read once after backoff, resume a saved job, refresh Shopify, replan affected nodes, request a new review, reconcile a cart or order through a read, or pause. Use ADK `RequestInput` for shopper decisions. Keep the agents on read-only evidence and analysis tools. The application owns approvals, cart writes, and the checkout handoff. When a Managed Agent service is unavailable, apply the recorded fallback for that error class and surface the degraded state. [ADK dynamic workflows](https://adk.dev/graphs/dynamic/) · [ADK human input](https://adk.dev/graphs/human-input/) · [Managed Agent background execution](https://aistudio.google.com/docs/background-execution)

| Failure or edge case | Managed recovery path | Safe behavior while recovering |
| --- | --- | --- |
| Unreadable photo, missing angle, person in frame, or unsupported upload | Ask for a clean photo, targeted angle, or supported format. Offer manual room input or the labeled preset. | Preserve the last usable room and remove rejected app copies. |
| Invalid room geometry, missing measurement, or conflicting views | Ask for the missing dimension or confirm the relevant wall or opening. Rebuild affected geometry and recheck placements. | Mark the affected fit unknown until supported by evidence. |
| Agora interruption, unavailable mic, or ambiguous pointing | Reconnect to the latest scene revision, offer manual upload or typed input, and ask the shopper to pick the intended object. | Keep the accepted design and pause any ambiguous edit. |
| Gemini or Decisions timeout, rate limit, or malformed response | Retry one eligible read or model call within the run budget. Resume with new evidence or a supported input mode when possible. | Keep the last verified scene. Checkout waits for a valid Decisions audit. |
| Managed Agent timeout, partial output, or outage | Check the stored interaction status before starting another run. Resume a usable session or use the configured deterministic fallback for the failed step. | Label partial work and keep it outside the accepted scene until verified. |
| Collision, unsupported fit, infeasible budget, or unknown dimensions | Ask Argon for a bounded repair using verified options and have Carbon test it when needed. Ask the shopper before relaxing a hard constraint. | Show what is still unresolved and preserve the prior scene. |
| Missing, out-of-stock, wrong-store, or stale Shopify variant | Refresh the selected Shopify store, search for a verified alternative, and return to review if the purchase changes. | Cached listings are read-only during a Shopify outage. |
| Duplicate command, stale revision, delayed result, or instruction embedded in catalog text | Deduplicate by action ID, re-read the committed revision, and apply only registered operations to validated data. | Preserve current state and ask for a fresh action if needed. |
| Cart differs from approved variants, quantity, price, or currency | Describe the exact discrepancy, reconcile with Shopify, and prepare a revised review for the shopper. Rerun the checkout audit after the repair. | Hold the hosted handoff until the approved purchase and live cart match. |
| Low-confidence audit, unknown user intent, or a late change after approval | Ask the shopper to confirm the unresolved detail, then refresh and reassess the workflow and cart. | Require a current, passing audit before handoff. |
| Shopify write times out or the cart outcome is unclear | Read the current Shopify cart and reconcile the existing action. Escalate unresolved outcomes for review. | Hold further writes while the previous mutation outcome is unknown. |
| Checkout declined, abandoned, or payment status unknown | Keep the cart, check Shopify order state where available, and offer a buyer-initiated recovery if appropriate. Deduplicate provider events. | Keep status pending or unconfirmed until verified. |
| Unrecognized failure or exhausted recovery budget | Surface the affected step and preserve enough context for manual review. | Pause the affected action and keep the last verified state available. |

## Room profiles and expansion

All profiles reuse the camera, edit, workflow, scene, and commerce contracts. Each profile defines the relevant objects, actions, and checks. A user can combine profiles within a room and lock the items they want to keep.

| Room | Evidence and checks | Commerce examples |
| --- | --- | --- |
| Gaming room | Monitor and desk placement, door and chair clearance, visible sources of screen glare. | Desks, chairs, monitor arms, lighting. |
| Living room | Seating positions, walkways, TV sightlines, door swings. | Sofas, rugs, tables, media consoles. |
| Bedroom | Space around a bed, closet access, drawer clearance. | Beds, dressers, nightstands, curtains. |
| Kitchen | Space for movable furniture, visible fixtures, stool pullback, drawer or door motion. | Stools, carts, storage, lighting. |
| Workplace | Seat count, circulation, desk arrangement, screen orientation. | Desks, chairs, lamps, dividers. |
| Library | Wall and shelf placement, reading light, access around seating. | Bookcases, chairs, lamps, side tables. |

## Element 119 evaluation

**What the raw media provides.** The photo carries the appearance of surfaces, visible openings, and the relative position of objects. The clip adds the direction and extent of the door’s motion, changing occlusion, and the timing of a pointing gesture. Separate audio tracks preserve which device supplied a turn, interruptions, and overlaps. Compare the same task with a text description, a photo, a photo plus one video stream, and a second view when available. That shows what each input adds to the generated workflow and fit checks.

Recruit people outside the team to supply room photos, record walkthroughs, and give their own instructions. Start with three to five real rooms or corners. Hold back some recordings and instructions from prompt tuning. Independently measure relevant room dimensions and check the recorded opening direction and referenced object. Report dimension error before and after the user’s scale correction for the dimensions tested. Keep team-authored code and payment cases separate from the live media evaluation.

| Stress case | Record | Recovery to test |
| --- | --- | --- |
| Poor or misleading room photo | Room relevance, openings detected, estimated dimension error, and whether the privacy and quality gates work. | Ask for a fresh image or manual measurements. Verify that invalid geometry leaves the last good room available and that the labeled preset works. |
| Longer and messier footage | Compare short and longer real clips. Record doorway and target accuracy, processing time, bytes, model calls, and time to a verified scene. | Analyze selected intervals and keyframes. Ask for the missing angle when evidence is insufficient. |
| Two camera streams | Stress time alignment, contradictory viewpoints, and stale selection. Record whether the right evidence is attached to a fit result. | Process per-device evidence independently, join by time and room revision, and confirm conflicting details. |
| Overlapping speech and dim video | Record wrong-target edits, requests for clarification, and recovery time. | Use the active editor’s input, highlight the selected target, and request a tap or guided rescan. |
| Moving door and uncertain measurements | Record false fit claims, reproducible collisions, and corrected placements. | Require a measured constraint where fit depends on it. Feed the independent test result into one targeted repair. |
| Voice-directed furniture move | Try an attractive placement that blocks a walking route, overlaps an object, or prevents a drawer or door from opening. Check that the style and clearance findings are shown separately. | Ask the agents to find and test a nearby placement. Sync it only after the shopper accepts the alternative and the server verifies it. |
| Busy live session and late results | Measure median and tail latency from a voice command to both screens updating. Check for stale commits or dropped interruptions. | Keep fast edits responsive, persist background jobs, and reject or recompute results built on older revisions. |
| Commerce drift and repeated actions | Check the exact merchant, variant, quantity, amount, and approval revision. Simulate a price change, unavailable variant, decline, or response timeout. | Refresh and review changes, reconcile unclear cart outcomes, and require an explicit new action where appropriate. |
| Workflow-to-cart mismatch | Introduce a wrong finish, unexpected extra item, outdated approval, or unresolved required constraint in the dev-store path. Measure whether the audit blocks handoff and how long recovery takes. | Show the discrepancy, run the managed recovery, collect approval for the corrected purchase, and run the audit again. |

Use ADK multi-run evaluation to report how often each held-out case succeeds and whether all repeats succeed. Inspect tool argument matches for merchant, variant, and quantity. Report sample counts, measured latency and cost, the first failure observed, and how the workflow changed after recovery. Label cached catalog results, representative assets, or recorded fallback footage on screen. A prerecorded run can support recovery during a total outage. Judging centers on the live path with new input. [ADK 2.15 evaluation features](https://github.com/google/adk-python/releases/tag/v2.15.0)

### Acceptance checks

- A fresh room photo or Agora still builds an editable procedural room. A dimension correction updates the geometry and its source. Invalid drafts cannot reach the scene. The upload path strips metadata and session cleanup removes app copies of raw photos.
- The judge can change the room or constraints with their own camera input and words. At least two different requests exercise visibly different workflow steps.
- The stage run shows a real provider response, a sandbox-generated proposal, an independently reproducible fit result, and the accepted scene on both devices.
- The stage run includes a voice-directed furniture move with a style assessment and an independent movement and interference check. Demonstrate one checked alternative when the requested position conflicts with a measured constraint.
- The stage run includes one exact-variant commerce change. Every shoppable item comes from a verified Shopify store. Product, scene, and cart identities agree.
- The lab includes a real failure, a correct measurement or rescan, and the verified recovery. Unknown fit and checkout outcome each have a visible status.
- A cart mutation requires the current approval. The checkout link is released only after deterministic reconciliation and a passing Decisions API audit for that approval and current cart. Payment confirmation comes from the appropriate Shopify response or event.
- Each failover test creates a visible recovery event. Check that the Managed Agent selects only a registered recovery action and that the app’s fallback preserves state if the agent is unavailable.

Add deterministic cases for an opening outside its wall, unknown product dimensions, estimated room clearance, anchored dimension edits, and a product from outside the approved Shopify catalog. Test the checkout audit with the wrong variant, wrong quantity, a newer scene revision, expired approval, a conflicted or failed Decisions response, and a Shopify write with an uncertain outcome. Verify that recovery preserves the appropriate state and triggers a new checkout audit before handoff. Keep numeric targets in the test report and record the observed results and sample size.

## Build plan and demo

### Workstreams

| Workstream | First deliverable | Integration dependency |
| --- | --- | --- |
| ADK and Gemini | Decision 1 capture and checkout gates, Neon `RoomSpec` and workflow outputs, ADK runner, live command tools, Argon recovery and Carbon audit contracts. | Hackathon Gemini key, pinned ADK and GenAI SDK, validated Managed Agents access. |
| Room and Agora | Metadata-safe photo capture, procedural Three.js shell, room correction controls, QR join, live camera and audio, door replay, room edits, activity panel. | `RoomSpec` schema, Agora RTC and Signaling project, short-lived join tokens, two devices with secure camera access. |
| Shopify and verification | Shopify-only discovery and dev-store verification, budget and fit checks, exact-variant cart review, immutable checkout audit input, checkout and failover tests. | Dev-store access and enabled test payment, products with usable images and measurements, plan and scene schemas. |

1. **Prove access.** Use the temporary Google account to create a Gemini key in AI Studio. Run the RoomSpec pipeline on a real photo, check Decisions and managed sandbox access, and join one Agora room on two devices. Run one read from each Shopify path and verify that the dev store can accept test payments. Keep credentials in server configuration. [Gemini API keys](https://aistudio.google.com/docs/api-key)
2. **Complete one vertical slice.** Build the 3D room from a fresh photo, correct a dimension, resolve the existing monitor, and generate the workflow. Place one real item, sync it, and put the selected variant into the development cart after approval. Use one shared sample schema for integration tests.
3. **Add the visible proof.** Replay one measured door swing, run the independent checker, show why a fit failed, and rerun just the affected layout steps. Exercise voice interruption, finish selection, undo, and reconnect.
4. **Rehearse and measure.** Run on the actual devices from two clean sessions. Exercise successful and declined test checkout, record at least one real media failure and recovery, and document the configurations and timing. Manage spend with per-run budgets and bounded retries. Record event-written code and third-party asset licenses in the submission README.

### Three-minute judging sequence

| Time | Action | What the judge sees |
| --- | --- | --- |
| 0:00–0:45 | Hand over the phone. Capture a clean room image, show the editable model, and confirm the desk-wall measurement. Ask the judge to point at the monitor and open the door. | Photo beside the procedural room, an accepted correction, and relevant live media on both devices. |
| 0:45–1:30 | Ask for the judge’s budget and style. Run the generated workflow and show the independent collision result as the planner repairs it. | The shortlisted Shopify products, the source frame, an overlap in 3D, and a revised placement. If evidence is missing, collect the requested input and resume. |
| 1:30–2:15 | Ask the judge where to move the drawer unit. Show the managed placement review, then let the judge choose a checked position and an available desk finish. | The 3D preview, whether the move fits their style, a highlighted route or collision when present, and the exact selected Shopify variant. |
| 2:15–3:00 | Confirm the review, verify the Shopify cart, show the Decisions API audit, and open hosted test checkout. | The activity panel shows the room selection and approved cart match. Any verified payment appears as a separate event. |

Choose the camera angle, measurement prompts, and supported controls for the stage setup. Let the judge supply the actual media and a meaningful preference. If the initial layout passes, let the judge drag the desk into the observed swing and test that real edit. Use the activity panel to show the result of the checkout audit. In rehearsal, change the approved finish before checkout and verify that recovery returns to review and reruns the audit.

## Inputs to this specification

The revised 33-page **NEXUS v2.0 PRD** supplies the gaming persona, photo-to-`RoomSpec` pipeline, editable procedural room, and hosted test payment flow. **NEXUS 3.0** supplies the live media, generated ADK 2 workflows, Managed Agent proposal and independent test, Agora collaboration, room profiles, and Shopify discovery. The Shopify Live View reference informs the activity panel. The hackathon builder guide supplies the API setup context and judging criteria. [Gemini 4 NYC Hackathon](https://events.cerebralvalley.ai/e/gemini-4-nyc)
