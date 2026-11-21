import { ProductViewer } from "./viewer.js";
import { SceneViewer, modelDot, priceLabel, setDot } from "./scene-viewer.js";

const byId = (id) => document.getElementById(id);
const POLL_MS = 1500;
const CATEGORIES = {
  bedroom: "Bedroom", living_room: "Living room", office: "Office", gaming_room: "Gaming room", kitchen: "Kitchen",
  dining_room: "Dining room", library: "Library", studio_apartment: "Studio apartment", other: "Room",
};

let scene = null;
let etag = null;
let selected = null;
let previewing = null;
const records = new Map();
const modelState = new Map();  // variant_id -> cached | missing | building | failed
const STATE_TEXT = {cached: "3D cached", missing: "No 3D · click to build", building: "Building 3D…", failed: "3D failed · click to retry"};

const room = new SceneViewer(byId("room-viewer"), (item) => selectProduct(item), {
  onMoved: (id, point) => editScene({op: "move", product_id: id, position: point}, "Moved."),
  onSwap: (from, to) => editScene({op: "swap", product_id: from, other_id: to}, "Swapped the two positions."),
});
const productViewer = new ProductViewer(byId("product-viewer"), () => {});

function post(url, body) {
  return fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
    .then(async (response) => {
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw Object.assign(new Error(result.message ?? "Request failed."), {status: response.status, result});
      return result;
    });
}

function editMessage(text, error = false) {
  const line = byId("edit-message");
  line.textContent = text;
  line.dataset.error = String(error);
}

function setModes({move = null, swap = null} = {}) {
  room.setMoveMode(move);
  room.setSwapMode(swap);
  byId("edit-move").setAttribute("aria-pressed", String(Boolean(move)));
  byId("edit-swap").setAttribute("aria-pressed", String(Boolean(swap)));
}

// Every edit is checked by the server's guardrails against the current file; a blocked edit snaps back.
let editing = false;
async function editScene(change, done) {
  if (!scene || editing) return;
  editing = true;
  editMessage("Checking the placement…");
  try {
    const body = await post("/studio/scene/edit", {base_etag: etag, action_id: crypto.randomUUID(), ...change});
    etag = body.etag;
    await render(body.scene, {quiet: true});
    editMessage(done);
    toast(`${done} · revision ${body.scene.revision}`);
  } catch (error) {
    const reason = error.message.startsWith("Not possible") ? error.message : `Not placed: ${error.message}`;
    editMessage(error.status === 422 ? reason : error.message, true);
    if (error.status === 409) etag = null;
    else room.showScene(scene, resolveModel, stateOf);
    room.highlight();
  } finally {
    editing = false;
  }
}

// One request per exact Shopify variant: the server caches models on disk, the page caches the promise.
function productRecord(variantId) {
  if (!records.has(variantId)) {
    const pending = post("/studio/scene/product", {variant_id: variantId});
    pending.catch(() => records.delete(variantId));
    records.set(variantId, pending);
  }
  return records.get(variantId);
}

function stateOf(item) {
  if (item.model) return "cached";
  return item.variant_id ? modelState.get(item.variant_id) ?? "missing" : "failed";
}

// Every dot, tag, and row that shows this variant follows its model state.
function showState(variantId, state) {
  modelState.set(variantId, state);
  for (const dot of document.querySelectorAll(`[data-variant="${CSS.escape(variantId)}"] .model-dot`)) setDot(dot, state);
  for (const note of document.querySelectorAll(`[data-variant="${CSS.escape(variantId)}"] small`)) note.textContent = STATE_TEXT[state];
}

async function refreshStates(variantIds) {
  const ids = [...new Set(variantIds.filter(Boolean))].filter((id) => modelState.get(id) !== "building");
  if (!ids.length) return;
  try {
    const states = await post("/studio/scene/models/status", {variant_ids: ids.slice(0, 80)});
    for (const [id, state] of Object.entries(states)) {
      if (modelState.get(id) !== "building") showState(id, state);
    }
  } catch {
    // Dots keep their last known state.
  }
}

// Cached models load straight away; a missing one stays a sized box until the shopper asks for it.
async function resolveModel(item) {
  if (item.model) return item.model;
  if (stateOf(item) !== "cached") return null;
  try {
    return (await productRecord(item.variant_id)).model;
  } catch {
    showState(item.variant_id, "failed");
    return null;
  }
}

// Builds the model from the Shopify listing (merchant 3D media or the Gemini photo-to-GLB recipe) and caches it.
async function buildModel(variantId) {
  if (stateOf({variant_id: variantId}) !== "cached") showState(variantId, "building");
  try {
    const record = await productRecord(variantId);
    showState(variantId, "cached");
    for (const item of scene?.placed_products ?? []) {
      if (item.variant_id === variantId && !item.model) room.attachModel(item.id, record.model);
    }
    return record;
  } catch (error) {
    showState(variantId, "failed");
    throw error;
  }
}

function safeUrl(value, host) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && (!host || url.hostname === host) ? url : null;
  } catch {
    return null;
  }
}

function toast(text) {
  const element = byId("toast");
  element.textContent = text;
  element.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { element.hidden = true; }, 2800);
}

function renderCart() {
  const list = byId("cart-list");
  list.replaceChildren(...scene.placed_products.map((item) => {
    const li = document.createElement("li");
    li.dataset.cart = item.id;
    li.dataset.variant = item.variant_id ?? "";
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(item.id === selected?.id));
    const name = document.createElement("span");
    name.className = "cart-name";
    name.append(modelDot(stateOf(item)), document.createTextNode(item.label));
    const note = document.createElement("small");
    note.textContent = STATE_TEXT[stateOf(item)];
    const price = document.createElement("b");
    price.textContent = priceLabel(item.price) || "—";
    button.append(name, note, price);
    button.addEventListener("click", () => room.select(item.id));
    li.append(button);
    return li;
  }));
  const totals = {};
  for (const item of scene.placed_products) {
    if (item.price?.currency) totals[item.price.currency] = (totals[item.price.currency] ?? 0) + item.price.amount;
  }
  byId("subtotal").textContent = Object.entries(totals)
    .map(([currency, amount]) => priceLabel({currency, amount})).join(" + ") || "—";
}

async function render(next, {quiet = false} = {}) {
  if (scene && next.revision !== scene.revision) {
    byId("audit").hidden = true;
    checkoutLine(null, "");
  }
  await refreshStates(next.placed_products.map((item) => item.variant_id));
  const previous = scene;
  scene = next;
  const {width, depth, height} = scene.dimensions;
  byId("stage-title").textContent = `${CATEGORIES[scene.category] ?? "Room"} · ${[width, depth, height].map((d) => d.value_m.toFixed(2)).join(" × ")} m`;
  const brief = scene.design_brief;
  byId("stage-eyebrow").textContent = brief?.purpose ? `${brief.purpose} · ${(brief.styles ?? []).join(", ")}` : "Room · metres";
  byId("source").lastChild.textContent = ` Live from geometricconfig.json · revision ${scene.revision}`;
  room.showScene(scene, resolveModel, stateOf);
  renderCart();
  if (previous && !quiet) toast(`Room updated · revision ${scene.revision}`);
  if (selected) {
    const still = scene.placed_products.find((item) => item.id === selected.id);
    if (still) {
      if (still.variant_id !== selected.variant_id) selectProduct(still);
      else selected = still;
      room.selectedId = still.id;
      room.highlight();
    } else {
      clearSelection();
    }
  }
}

async function poll() {
  try {
    const response = await fetch("/studio/scene/config", {headers: etag ? {"If-None-Match": `"${etag}"`} : {}});
    if (response.status === 200) {
      const body = await response.json();
      etag = body.etag;
      await render(body.scene);
    } else if (response.status !== 304 && !scene) {
      const body = await response.json().catch(() => ({}));
      byId("stage-title").textContent = body.message ?? "Waiting for geometricconfig.json";
    }
  } catch {
    // Keep the last scene on screen; the next poll retries.
  } finally {
    setTimeout(poll, POLL_MS);
  }
}

function clearSelection() {
  selected = previewing = null;
  setModes();
  editMessage("");
  spin.token = null;
  byId("product-pop").hidden = true;
  byId("similar").replaceChildren();
  room.selectedId = null;
  room.highlight();
  renderCart();
}

function showFacts(title, price, rows, listing, variantId) {
  byId("product-title").textContent = title;
  const head = byId("product-state");
  head.dataset.variant = variantId ?? "";
  head.replaceChildren(modelDot(variantId ? stateOf({variant_id: variantId}) : "failed"), document.createElement("small"));
  head.querySelector("small").textContent = variantId ? STATE_TEXT[stateOf({variant_id: variantId})] : "No Shopify variant";
  byId("product-price").textContent = priceLabel(price) || "Price unavailable";
  byId("facts").replaceChildren(...rows.filter(([, value]) => value).flatMap(([name, value]) => {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = name;
    dd.textContent = value;
    return [dt, dd];
  }));
  const url = safeUrl(listing);
  byId("listing").hidden = !url;
  if (url) byId("listing").href = url.href;
  byId("product-facts").hidden = false;
}

async function spin(record, token) {
  byId("product-message").hidden = false;
  byId("product-message").textContent = modelState.get(previewing?.variant_id ?? selected?.variant_id) === "building"
    ? "Building the 3D model from the Shopify listing…" : "Loading the 3D model…";
  try {
    const product = await record;
    if (token !== spin.token) return;
    await productViewer.show(product);
    byId("product-message").hidden = true;
    return product;
  } catch (error) {
    if (token === spin.token) byId("product-message").textContent = "3D model unavailable: " + error.message;
  }
}

async function selectProduct(item) {
  selected = item;
  previewing = null;
  byId("product-pop").hidden = false;
  renderCart();
  byId("product-eyebrow").textContent = "Selected product";
  byId("back-to-placed").hidden = true;
  byId("edit-tools").hidden = false;
  byId("use-product").hidden = true;
  if (room.moveId !== item.id) setModes();
  editMessage("");
  const cm = (m) => Math.round(m * 100);
  showFacts(item.label, item.price, [
    ["Size", `${cm(item.size.width)} × ${cm(item.size.depth)} × ${cm(item.size.height)} cm`],
  ], item.listing_url, item.variant_id);
  const token = spin.token = Symbol(item.id);
  const record = item.variant_id ? buildModel(item.variant_id) : Promise.reject(new Error("No Shopify variant."));
  if (item.variant_id && stateOf(item) !== "cached") {
    byId("product-message").hidden = false;
    byId("product-message").textContent = "Building the 3D model from the Shopify listing…";
  }
  spin(item.model ? Promise.resolve({...item, title: item.label, model: item.model}) : record, token).then((product) => {
    if (!product || token !== spin.token) return;
    byId("product-title").textContent = product.title ?? item.label;
    const url = safeUrl(item.listing_url ?? product.listing_url);
    byId("listing").hidden = !url;
    if (url) byId("listing").href = url.href;
  });
  loadSimilar(item);
}

async function loadSimilar(item) {
  const list = byId("similar");
  list.replaceChildren();
  if (!item.variant_id) {
    byId("similar-status").textContent = "This product has no Shopify variant to compare.";
    return;
  }
  byId("similar-status").textContent = "Searching Shopify…";
  try {
    const result = await post("/studio/scene/similar", {variant_id: item.variant_id, query: item.label, limit: 9});
    if (selected?.id !== item.id) return;
    byId("similar-status").textContent = result.results.length ? "" : "Shopify returned no alternatives.";
    list.replaceChildren(...result.results.map((offer) => suggestionCard(offer)));
    refreshStates(result.results.map((offer) => offer.variant_id));
  } catch (error) {
    if (selected?.id === item.id) byId("similar-status").textContent = "Could not load suggestions: " + error.message;
  }
}

function suggestionCard(offer) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = "suggestion";
  card.dataset.variant = offer.variant_id;
  const imageUrl = safeUrl(offer.image_url, "cdn.shopify.com");
  let image;
  if (imageUrl) {
    imageUrl.searchParams.set("width", "240");
    image = document.createElement("img");
    image.src = imageUrl.href;
    image.alt = "";
    image.loading = "lazy";
  } else {
    image = document.createElement("div");
    image.className = "missing-image";
  }
  const price = document.createElement("b");
  price.textContent = priceLabel(offer.price) || "—";
  card.title = offer.title;
  card.setAttribute("aria-label", `${offer.title}, ${price.textContent}`);
  card.append(image, price, modelDot(stateOf({variant_id: offer.variant_id})));
  card.addEventListener("click", () => previewSuggestion(offer, card));
  return card;
}

function previewSuggestion(offer, card) {
  previewing = offer;
  for (const other of document.querySelectorAll(".suggestion")) other.setAttribute("aria-pressed", String(other === card));
  byId("product-eyebrow").textContent = "Shopify suggestion";
  byId("back-to-placed").hidden = false;
  byId("edit-tools").hidden = true;
  byId("use-product").hidden = false;
  setModes();
  editMessage("");
  const option = offer.options?.find((entry) => /colou?r|finish/i.test(entry.name));
  showFacts(offer.title, offer.price, [["Finish", option?.label]], offer.listing_url, offer.variant_id);
  spin(buildModel(offer.variant_id), spin.token = Symbol(offer.variant_id));
}

function checkoutLine(state, text) {
  const line = byId("checkout-line");
  line.hidden = !state;
  line.dataset.state = state ?? "";
  const parts = [];
  if (state === "working") {
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    parts.push(spinner);
  }
  const words = document.createElement("span");
  words.textContent = text;
  const model = document.createElement("code");
  model.textContent = "gemini-decision-1";
  line.replaceChildren(...parts, words, model);
}

function listItems(target, rows) {
  byId(target).replaceChildren(...rows.map((row) => {
    const li = document.createElement("li");
    if (row.ok !== undefined) li.dataset.ok = String(row.ok);
    li.append(...row.parts.map((part) => {
      const span = document.createElement(part.strong ? "b" : "span");
      span.textContent = part.text;
      return span;
    }));
    return li;
  }));
}

function renderAudit(audit) {
  byId("audit").hidden = false;
  const status = byId("audit-status");
  status.dataset.status = audit.status === "ready" ? "pass" : "needs_more";
  status.textContent = audit.status === "ready" ? "Ready" : "Needs review";
  byId("audit-message").textContent = audit.message;
  listItems("audit-checks", audit.checks.map((check) => ({
    ok: check.ok, parts: [{text: check.ok ? "✓" : "✗", strong: true}, {text: check.name, strong: true}, {text: check.detail}],
  })));
  const decision = audit.decision;
  const pct = (value) => `${Math.round(value * 100)}%`;
  byId("audit-decision").replaceChildren(...[
    ["Cart matches the room", pct(decision.purchase_scope_matches)],
    ["Requirements resolved", pct(decision.required_constraints_resolved)],
    ["Fits the brief", pct(decision.fits_brief)],
    ["Next action", `${decision.next_action.replaceAll("_", " ")} (${pct(decision.next_action_confidence)})`],
  ].flatMap(([name, value]) => {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = name;
    dd.textContent = value;
    return [dt, dd];
  }));
  listItems("audit-lines", audit.lines.map((line) => ({
    ok: line.verified && line.available,
    parts: [
      {text: line.title.slice(0, 70)},
      {text: line.price_changed ? `${priceLabel(line.price_was)} → ${priceLabel(line.price_now)}` : priceLabel(line.price_now) || "—", strong: true},
    ],
  })));
  // Demo checkout: show what each store's order would be, and stop before placing it.
  const titles = new Map(audit.lines.map((line) => [line.product_id, line.title]));
  const stores = byId("audit-stores");
  stores.replaceChildren();
  if (audit.status === "ready") {
    const proceed = document.createElement("button");
    proceed.type = "button";
    proceed.className = "primary store-proceed";
    proceed.textContent = "Continue to checkout";
    proceed.addEventListener("click", () => {
      const summary = audit.stores.map((store) => {
        const block = document.createElement("div");
        block.className = "store-order";
        const head = document.createElement("p");
        const name = document.createElement("b");
        const amount = document.createElement("span");
        name.textContent = store.merchant;
        amount.textContent = priceLabel({amount: store.subtotal_minor, currency: audit.subtotal.currency});
        head.append(name, amount);
        const items = document.createElement("ul");
        items.append(...store.lines.map((id) => Object.assign(document.createElement("li"), {textContent: titles.get(id)?.slice(0, 70) ?? id})));
        block.append(head, items);
        return block;
      });
      const totals = document.createElement("p");
      totals.className = "store-total";
      const label = document.createElement("span");
      const total = document.createElement("b");
      label.textContent = "Total before shipping and tax";
      total.textContent = priceLabel(audit.subtotal);
      totals.append(label, total);
      // Shopify checkout cannot run in an iframe, so the cart opens in its own window.
      // Pressing this is the approval: it is the only request that sends confirm,
      // and the purchase server mints no link until it arrives. The review above
      // asked for none, so audit.stores is empty until this call comes back.
      const open = document.createElement("button");
      open.type = "button";
      open.className = "primary store-open";
      open.textContent = "Open Shopify checkout";
      open.addEventListener("click", async () => {
        open.disabled = true;
        try {
          const confirmed = await post("/studio/scene/checkout", {base_etag: etag, confirm: true});
          const store = (confirmed.stores ?? []).find((candidate) => safeUrl(candidate.cart_url));
          if (!store) {
            checkoutLine("blocked", confirmed.message || "No checkout link was issued.");
            return;
          }
          const left = Math.max(0, window.screenX + window.outerWidth - 560);
          const popup = window.open(store.cart_url, `shopify-${store.merchant}`, `popup,width=540,height=820,left=${left},top=${window.screenY + 40}`);
          if (!popup) location.assign(store.cart_url);
          checkoutLine("ok", `Shopify checkout opened for ${store.merchant} · enter card details there ·`);
        } catch (error) {
          checkoutLine("blocked", error.message);
          if (error.status === 409) etag = null;
        } finally {
          open.disabled = false;
        }
      });
      const opens = [open];
      const note = document.createElement("p");
      note.className = "store-note";
      note.textContent = "Card details go into Shopify's secure checkout window, never this app. For the demo, stop before pressing Pay: no order is placed until you do.";
      stores.replaceChildren(...summary, totals, ...opens, note);
      checkoutLine("ok", "Checkout ready ·");
    });
    stores.append(proceed);
  }
  byId("audit-note").textContent = `${audit.order.note} Audit ${audit.audit_id.slice(0, 8)} · revision ${audit.scene_revision} · ${audit.checked_at}. Any edit needs a fresh review.`;
}

byId("checkout").addEventListener("click", async () => {
  if (!scene || !etag) return;
  const button = byId("checkout");
  button.disabled = true;
  byId("audit").hidden = true;
  checkoutLine("working", "Re-checking Shopify prices and stock, then auditing with");
  try {
    const audit = await post("/studio/scene/checkout", {base_etag: etag});
    checkoutLine(audit.status === "ready" ? "ok" : "blocked", audit.status === "ready" ? "Audit passed ·" : "Audit needs review ·");
    renderAudit(audit);
  } catch (error) {
    checkoutLine("blocked", error.message);
    if (error.status === 409) etag = null;
  } finally {
    button.disabled = false;
  }
});

// Shopify link → 3D: the server converts the listing to a cached model; the result can be added to the room.
let imported = null;
byId("link-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = byId("link-input").value.trim();
  if (!url) return;
  byId("link-go").disabled = true;
  byId("link-message").dataset.error = "false";
  byId("link-message").textContent = "Converting the Shopify listing to a 3D model…";
  try {
    const record = await post("/studio/scene/import", {url});
    imported = record;
    records.set(record.variant_id, Promise.resolve(record));
    showState(record.variant_id, "cached");
    selected = null;
    room.selectedId = null;
    room.highlight();
    byId("product-pop").hidden = false;
    byId("product-eyebrow").textContent = "From your Shopify link";
    byId("back-to-placed").hidden = true;
    byId("edit-tools").hidden = true;
    byId("use-product").hidden = true;
    byId("add-product").hidden = false;
    byId("similar").replaceChildren();
    byId("similar-status").textContent = "";
    const size = record.dimensions_m;
    const cm = (m) => Math.round(m * 100);
    showFacts(record.title, record.price, [["Size", size ? `${cm(size.width)} × ${cm(size.depth)} × ${cm(size.height)} cm` : "From photos"]],
      record.listing_url, record.variant_id);
    spin(Promise.resolve(record), spin.token = Symbol(record.variant_id));
    editMessage(record.conversion_note ?? "");
    byId("link-message").textContent = "3D model ready and cached.";
  } catch (error) {
    byId("link-message").dataset.error = "true";
    byId("link-message").textContent = error.message;
  } finally {
    byId("link-go").disabled = false;
  }
});
byId("add-product").addEventListener("click", () => {
  if (!imported) return;
  const record = imported;
  editScene({op: "add", product_id: "new", new_product: {
    variant_id: record.variant_id, title: record.title, price: record.price ?? null, listing_url: record.listing_url ?? null,
    size: record.dimensions_m ?? null,
  }}, "Added to the room at the first spot that fits.").then(() => {
    byId("add-product").hidden = true;
  });
});

byId("back-to-placed").addEventListener("click", () => selected && selectProduct(selected));
byId("edit-move").addEventListener("click", () => {
  const on = room.moveId !== selected?.id;
  setModes({move: on ? selected.id : null});
  editMessage(on ? "Drag the product across the floor, then release." : "");
});
for (const [id, delta] of [["edit-left", -15], ["edit-right", 15]]) {
  byId(id).addEventListener("click", () => selected && editScene(
    {op: "rotate", product_id: selected.id, yaw: selected.pose.rotation.yaw + delta}, `Rotated ${Math.abs(delta)}°.`,
  ));
}
byId("edit-swap").addEventListener("click", () => {
  const on = room.swapFrom !== selected?.id;
  setModes({swap: on ? selected.id : null});
  editMessage(on ? "Click the product to swap positions with." : "");
});
byId("edit-remove").addEventListener("click", async () => {
  if (!selected) return;
  const id = selected.id;
  clearSelection();
  await editScene({op: "remove", product_id: id}, "Removed from the room.");
});
byId("use-product").addEventListener("click", () => {
  if (!selected || !previewing) return;
  const offer = previewing;
  editScene({op: "replace", product_id: selected.id, replacement: {
    variant_id: offer.variant_id, title: offer.title, price: offer.price ?? null, listing_url: offer.listing_url ?? null,
  }}, "Replaced with the Shopify alternative.").then(() => {
    const item = scene.placed_products.find((entry) => entry.id === selected?.id);
    if (item?.variant_id === offer.variant_id) {
      selectProduct(item);
      editMessage(`Replaced with ${offer.title.slice(0, 60)}.`);
    }
  });
});
byId("close-product").addEventListener("click", clearSelection);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !byId("product-pop").hidden) clearSelection();
});
for (const button of document.querySelectorAll("[data-view]")) button.addEventListener("click", () => room.view(button.dataset.view));
byId("toggle-labels").addEventListener("click", (event) => {
  const on = event.currentTarget.getAttribute("aria-pressed") !== "true";
  event.currentTarget.setAttribute("aria-pressed", String(on));
  room.setLabels(on);
});
poll();
