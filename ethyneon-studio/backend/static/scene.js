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
// A product only has a real 3D model when the generator has run. Anything
// else on screen is a box at the size the listing claims, and every place
// that shows one says so. An unlabelled grey box is a claim about the
// product that nobody made.
const STATE_TEXT = {
  cached: "3D model from the listing",
  missing: "Stand-in box at listing size",
  building: "Building a 3D model…",
  failed: "Stand-in box at listing size · 3D build failed",
};
const STAND_IN = new Set(["missing", "failed"]);
const FIT_WORDS = {
  fits: "Fits",
  tight: "Tight",
  does_not_fit: "Does not fit",
  unknown: "Unknown",
};
// Shown in words, not as a colour. Somebody reading a screenshot of this
// has to be able to tell that the size was never measured.
const TENTATIVE_BADGE = "Tentative: size read from the listing text, not measured";
let fitVerdicts = {};

const room = new SceneViewer(byId("room-viewer"), (item) => selectProduct(item), {
  onDragFeedback: (blocked) => {
    if (blocked === null) return;
    editMessage(blocked
      ? "That spot is blocked. Release to have the server check it anyway."
      : "Clear here. Release to have the server check it.", blocked);
  },
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
    await render(body.scene, {quiet: true, fit: body.fit});
    editMessage(done);
    showVerdict(change.product_id === "new" ? null : change.product_id);
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

// The deterministic engine decides, and its sentence is what the shopper
// reads. No badge at all is shown when no verdict came back, because an
// absent check must not look like a passed one.
function showVerdict(productId) {
  const line = byId("fit-line");
  const verdict = productId ? fitVerdicts[productId] : null;
  if (!verdict) {
    line.hidden = true;
    return;
  }
  line.hidden = false;
  const badge = byId("fit-badge");
  badge.textContent = FIT_WORDS[verdict.result] ?? verdict.result;
  badge.dataset.result = verdict.result;
  byId("fit-cause").textContent = verdict.cause;
  const tentative = byId("fit-tentative");
  tentative.hidden = !verdict.tentative;
  tentative.textContent = verdict.tentative ? TENTATIVE_BADGE : "";
}

function toast(text) {
  const element = byId("toast");
  element.textContent = text;
  element.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { element.hidden = true; }, 2800);
}

// Your list. Not a cart: nothing here can be bought in this app, so every
// row ends at the merchant's own page and there is no control that does
// anything else with it.
function renderCart() {
  const list = byId("cart-list");
  list.replaceChildren(...scene.placed_products.map((item) => {
    const li = document.createElement("li");
    li.dataset.cart = item.id;
    li.dataset.variant = item.variant_id ?? "";
    if (item.preview) li.dataset.preview = "true";
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

    const verdict = fitVerdicts[item.id];
    if (verdict) {
      const fit = document.createElement("span");
      fit.className = "row-fit";
      fit.dataset.result = verdict.result;
      fit.textContent = FIT_WORDS[verdict.result] ?? verdict.result;
      if (verdict.tentative) fit.textContent += " · tentative";
      fit.title = verdict.cause;
      li.append(fit);
    }

    if (item.preview) {
      const sold = document.createElement("p");
      sold.className = "row-preview";
      sold.textContent = `Preview only, sold by ${item.merchant ?? "another merchant"}`;
      li.append(sold);
    }
    const url = safeUrl(item.listing_url);
    if (url) {
      const buy = document.createElement("a");
      buy.className = "row-buy";
      buy.href = url.href;
      buy.target = "_blank";
      buy.rel = "noopener noreferrer";
      buy.textContent = `Buy at ${item.merchant ?? url.hostname} \u2197`;
      li.append(buy);
    }
    return li;
  }));
  const totals = {};
  for (const item of scene.placed_products) {
    if (item.price?.currency) totals[item.price.currency] = (totals[item.price.currency] ?? 0) + item.price.amount;
  }
  byId("subtotal").textContent = Object.entries(totals)
    .map(([currency, amount]) => priceLabel({currency, amount})).join(" + ") || "—";
  byId("list-count").hidden = !scene.placed_products.length;
  byId("list-count").textContent = `${scene.placed_products.length} item(s)`;
}

async function render(next, {quiet = false, fit = null} = {}) {
  fitVerdicts = fit ?? fitVerdicts;
  byId("room-form").hidden = true;
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
  if (selected && !fitVerdicts[selected.id]) showVerdict(null);
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
      await render(body.scene, {fit: body.fit ?? {}});
    } else if (response.status !== 304 && !scene) {
      const body = await response.json().catch(() => ({}));
      byId("stage-title").textContent = "No room yet";
      byId("stage-eyebrow").textContent = body.message ?? "Waiting for geometricconfig.json";
      byId("room-form").hidden = false;
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
// ==========================================================================
// Browse every merchant, and preview one in the room
// ==========================================================================
//
// Nothing found here is purchasable. The card says so, the placed item says
// so, Your list says so, and checkout.py refuses it. Four places agree
// because only the last one is a guard; the other three are manners.

let browsing = false;

function previewCard(offer) {
  const card = document.createElement("div");
  card.className = "browse-card";

  const open = document.createElement("button");
  open.type = "button";
  open.className = "browse-pick";
  const image = photoThumb(offer.image_url, offer.title);
  if (image) open.append(image);
  const title = document.createElement("span");
  title.className = "browse-title";
  title.textContent = offer.title;
  const meta = document.createElement("span");
  meta.className = "browse-meta";
  const size = offer.size_m
    ? `${Math.round(offer.size_m.width * 100)} × ${Math.round(offer.size_m.depth * 100)} cm`
    : "no size in the listing";
  meta.textContent = [priceLabel(offer.price), size].filter(Boolean).join(" · ");
  open.append(title, meta);
  open.addEventListener("click", () => placePreview(offer, open));
  card.append(open);

  const sold = document.createElement("p");
  sold.className = "browse-sold";
  sold.append("View only, sold by ");
  const url = safeUrl(offer.listing_url);
  if (url) {
    const link = document.createElement("a");
    link.href = url.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = offer.merchant;
    sold.append(link);
  } else {
    const name = document.createElement("strong");
    name.textContent = offer.merchant;
    sold.append(name);
  }
  card.append(sold);
  return card;
}

// Only Shopify's own CDN, only https, and only at card size. A merchant's
// product photography is theirs; showing it bigger than a thumbnail is not
// ours to do.
function photoThumb(value, alt) {
  const url = safeUrl(value, "cdn.shopify.com");
  if (!url) return null;
  url.searchParams.set("width", "220");
  const img = document.createElement("img");
  img.src = url.href;
  img.alt = alt ? `${alt}, merchant photo` : "";
  img.loading = "lazy";
  img.width = 110;
  return img;
}

function browseMessage(text, error = false) {
  const line = byId("browse-message");
  line.textContent = text;
  line.dataset.error = String(error);
}

byId("browse-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = byId("browse-input").value.trim();
  if (!query || browsing) return;
  browsing = true;
  byId("browse-go").disabled = true;
  browseMessage("Searching every merchant on Shopify…");
  try {
    const body = await post("/studio/scene/browse", {query, limit: 8});
    byId("browse-results").replaceChildren(...body.results.map(previewCard));
    byId("browse-count").hidden = !body.results.length;
    byId("browse-count").textContent = `${body.results.length} found`;
    browseMessage(body.results.length
      ? `${body.results.length} result(s) for “${body.query}”. Pick one to preview it in the room.`
      : `Nothing found for “${body.query}”.`);
  } catch (error) {
    browseMessage(error.message, true);
  } finally {
    browsing = false;
    byId("browse-go").disabled = false;
  }
});

async function placePreview(offer, button) {
  if (!scene || !etag) {
    browseMessage("Create the room first, then pick a product.", true);
    return;
  }
  button.disabled = true;
  try {
    await editScene({
      op: "add",
      product_id: "new",
      new_product: {
        variant_id: offer.variant_id,
        title: offer.title,
        price: offer.price ?? null,
        listing_url: offer.listing_url ?? null,
        // The three fields that make this a preview rather than a purchase.
        // The server does not take the client's word for any of it: it looks
        // the variant up on Shopify before anything is written.
        preview: true,
        merchant: offer.merchant ?? null,
        dimension_source: offer.dimension_source ?? "unknown",
        size: offer.size_m
          ? {width: offer.size_m.width, depth: offer.size_m.depth, height: offer.size_m.height}
          : null,
      },
    }, `Previewing ${offer.title.slice(0, 48)} in the room.`);
    const placed = scene.placed_products.find((item) => item.variant_id === offer.variant_id);
    if (placed) {
      room.select(placed.id);
      showVerdict(placed.id);
    }
  } finally {
    button.disabled = false;
  }
}

// ==========================================================================
// A room measured with a tape, for when no model is answering
// ==========================================================================

byId("room-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = byId("room-message");
  const number = (id) => Number(byId(id).value);
  byId("room-go").disabled = true;
  message.dataset.error = "false";
  message.textContent = "Writing the room…";
  try {
    const body = await post("/studio/scene/room", {
      width_m: number("room-width"),
      depth_m: number("room-depth"),
      height_m: number("room-height"),
      door_wall: byId("door-wall").value,
      door_offset_m: number("door-offset"),
      door_width_m: number("door-width"),
      door_hinge: byId("door-hinge").value,
      door_swing: byId("door-swing").value,
      replace: byId("room-form").dataset.replace === "true",
    });
    etag = body.etag;
    await render(body.scene, {quiet: true, fit: body.fit ?? {}});
    message.textContent = "";
    toast("Room created. Now find a product.");
  } catch (error) {
    message.dataset.error = "true";
    message.textContent = error.message;
    if (error.status === 409) {
      byId("room-form").dataset.replace = "true";
      byId("room-go").textContent = "Replace the room anyway";
    }
  } finally {
    byId("room-go").disabled = false;
  }
});

poll();
