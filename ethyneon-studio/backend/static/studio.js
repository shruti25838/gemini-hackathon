const byId = (id) => document.getElementById(id);
const input = byId("command");
const results = byId("results");
const spin = byId("spin");
const reset = byId("reset");
const status = byId("status");
const exampleButtons = [...document.querySelectorAll("[data-command]")];
const modeInputs = [...document.querySelectorAll('input[name="mode"]')];
let offers = [];
let selected = null;
let busy = false;
let lastQuery = "";

// "browse" searches every merchant on Shopify and can buy nothing. "store"
// searches the connected development store. This only decides what is
// searched; the server decides, per variant, what can actually be bought.
const currentMode = () => modeInputs.find((input) => input.checked)?.value ?? "browse";

const viewer = import("./viewer.js")
  .then(({ProductViewer}) => new ProductViewer(byId("viewer"), (spinning) => {
    spin.setAttribute("aria-pressed", String(spinning));
  }))
  .catch(() => {
    byId("viewer-message").textContent = "3D rendering is unavailable in this browser.";
    return null;
  });

function notify(message, error = false) {
  status.textContent = message;
  status.dataset.error = String(error);
}

function https(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url : null;
  } catch {
    return null;
  }
}

function photo(value, alt, width) {
  const url = https(value);
  if (url?.hostname !== "cdn.shopify.com") return null;
  url.searchParams.set("width", String(width));
  const img = document.createElement("img");
  img.src = url.href;
  img.alt = alt;
  img.loading = "lazy";
  return img;
}

function priceLabel(price) {
  if (!price || !Number.isFinite(price.amount) || !/^[A-Z]{3}$/.test(price.currency)) return "";
  const formatter = new Intl.NumberFormat(undefined, {style: "currency", currency: price.currency});
  return formatter.format(price.amount / 10 ** formatter.resolvedOptions().maximumFractionDigits);
}

function controls() {
  byId("send").disabled = busy;
  for (const input of modeInputs) input.disabled = busy;
  for (const button of exampleButtons) {
    const unavailable = button === byId("choose-example") ? offers.length < 2 : "needsProduct" in button.dataset && !selected;
    button.disabled = busy || unavailable;
  }
  for (const card of results.querySelectorAll("button.card")) {
    card.disabled = busy;
    card.setAttribute("aria-pressed", String(card.dataset.variant === selected?.variant_id));
  }
}

function viewOnlyNote(offer) {
  const note = document.createElement("p");
  note.className = "card-view-only";
  note.append("View only, sold by ");
  const url = https(offer.listing_url);
  if (url) {
    const link = document.createElement("a");
    link.href = url.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = offer.merchant;
    note.append(link);
  } else {
    const name = document.createElement("strong");
    name.textContent = offer.merchant;
    note.append(name);
  }
  return note;
}

function renderOffers() {
  results.replaceChildren();
  for (const [index, offer] of offers.entries()) {
    const card = document.createElement("button");
    card.className = "card";
    card.type = "button";
    card.dataset.variant = offer.variant_id;
    card.addEventListener("click", () => run("Choose " + (index + 1)));
    const indexTag = document.createElement("span");
    indexTag.className = "slot";
    indexTag.textContent = String(index + 1);
    const img = photo(offer.image_url, "", 420);
    const fallback = document.createElement("span");
    fallback.className = "missing-image";
    fallback.textContent = "Photo unavailable";
    img?.addEventListener("error", () => img.replaceWith(fallback), {once: true});
    const info = document.createElement("span");
    info.className = "card-info";
    const title = document.createElement("span");
    title.className = "card-title";
    title.textContent = offer.title;
    const details = document.createElement("span");
    details.className = "card-meta";
    const finish = offer.options.find((option) => /colou?r/i.test(option.name));
    details.textContent = [priceLabel(offer.price), finish?.label].filter(Boolean).join(" · ");
    info.append(title, details);
    card.append(indexTag, img ?? fallback, info);
    // The card opens the product in the 3D viewer and does nothing else.
    // A view-only card names the merchant and links to their listing
    // beside it, and gets no add, review, checkout or cart control.
    if (offer.shoppable === false) {
      const slot = document.createElement("div");
      slot.className = "card-slot";
      slot.append(card, viewOnlyNote(offer));
      results.append(slot);
    } else {
      results.append(card);
    }
  }
  byId("catalog-empty").hidden = offers.length > 0;
  byId("result-count").hidden = !offers.length;
  byId("result-count").textContent = offers.length + " found";
}

function productDetails(product) {
  byId("product-title").textContent = product.title;
  byId("product-details").hidden = false;
  byId("finish").hidden = true;
  byId("attachment").hidden = true;
  byId("attachment").replaceChildren();
  const listing = byId("listing");
  const url = https(product.listing_url);
  listing.hidden = !url;
  if (url) listing.href = url.href;
  const facts = byId("facts");
  facts.replaceChildren();
  const dimensions = product.dimensions_m;
  const cm = (metres) => Number((metres * 100).toFixed(1));
  const height = product.height_range_m
    ? product.height_range_m.map(cm).join("–")
    : dimensions && cm(dimensions.height);
  const factsToShow = [
    ["Model", product.source === "shopify_model3d" ? "Merchant 3D asset" : "Photo-based preview"],
    ["Listed size · W × D × H", dimensions ? [cm(dimensions.width), cm(dimensions.depth), height].join(" × ") + " cm" : "Unavailable"],
    ["Surface materials", (product.materials ?? []).join(", ")],
    ["Photos used for 3D", product.reference_image_urls?.length],
  ];
  for (const [label, value] of factsToShow) {
    if (!value) continue;
    const fact = document.createElement("div");
    const name = document.createElement("dt");
    const definition = document.createElement("dd");
    name.textContent = label;
    definition.textContent = value;
    fact.append(name, definition);
    facts.append(fact);
  }
  const gallery = byId("gallery");
  gallery.replaceChildren();
  for (const url of product.image_urls ?? []) {
    const img = photo(url, "Open Shopify product photo " + (gallery.children.length + 1), 160);
    if (!img) continue;
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.append(img);
    gallery.append(link);
  }
  gallery.hidden = !gallery.children.length;
}

function showAttachment(product) {
  const attachment = byId("attachment");
  const url = https(product.listing_url);
  const title = document.createElement(url ? "a" : "strong");
  title.textContent = product.title;
  if (url) {
    title.href = url.href;
    title.target = "_blank";
    title.rel = "noopener noreferrer";
  }
  const note = document.createElement("span");
  note.textContent = "Headrest mounted in preview · Fit unverified";
  attachment.replaceChildren(title, note);
  attachment.hidden = false;
}

async function apply(result) {
  if (result.action === "show_results") {
    offers = result.results;
    lastQuery = result.query;
    renderOffers();
    const count = offers.length;
    const where = result.mode === "browse" ? "across Shopify" : "in our store";
    notify(count
      ? "Found " + count + " results " + where + " for “" + result.query + "”."
        + (result.shoppable ? "" : " These are view only.")
      : "No products found " + where + " for that search.");
    return;
  }
  if (result.action === "message") {
    notify(result.message);
    return;
  }
  const stage = await viewer;
  if (result.action === "select_product") {
    if (stage) await stage.show(result.product);
    selected = result.product;
    productDetails(selected);
    byId("viewer-message").hidden = Boolean(stage);
    byId("controls-hint").hidden = !stage;
    spin.disabled = !stage;
    reset.disabled = !stage;
    notify(stage ? "3D preview ready." : "Listing loaded. This browser could not start 3D rendering.", !stage);
    return;
  }
  if (result.variant_id !== selected?.variant_id) return;
  if (!stage) {
    notify("Open this preview in a WebGL-enabled browser to apply 3D edits.", true);
    return;
  }
  if (result.action === "rotate") {
    stage.rotate(result.degrees);
    notify("Rotated " + Math.abs(result.degrees) + "°.");
  } else if (result.action === "set_color") {
    stage.setColor(result.color);
    byId("swatch").style.backgroundColor = result.color;
    byId("finish-label").textContent = "Finish preview";
    byId("finish").hidden = false;
    notify("Updated the preview finish.");
  } else if (result.action === "attach_headrest") {
    await stage.attachHeadrest(result.product);
    showAttachment(result.product);
    notify("Added a Shopify headrest to the preview.");
  }
}

async function run(command) {
  command = command.trim();
  if (!command || busy) return;
  busy = true;
  controls();
  notify(/head\s*rest|neck pillow/i.test(command) ? "Checking Shopify for the headrest…" :
    /^(choose|select|pick|open)\b|^(top|upper|bottom|lower)\b/i.test(command) ? "Loading the Shopify model…" : "Working…");
  try {
    const response = await fetch("/studio/command", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        command,
        visible: offers.map(({variant_id, title}) => ({variant_id, title})),
        selected_variant_id: selected?.variant_id ?? null,
        mode: currentMode(),
        country: "US",
        currency: "USD",
      }),
    });
    const result = await response.json();
    if (!response.ok) {
      notify(result.message ?? "The studio could not complete that command.", true);
      if (!input.value) input.value = command;
      return;
    }
    await apply(result);
  } catch {
    notify("Could not load the preview. Try the command again.", true);
    if (!input.value) input.value = command;
  } finally {
    busy = false;
    controls();
  }
}

byId("composer-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!busy && input.value.trim()) {
    const command = input.value;
    input.value = "";
    run(command);
  }
});
for (const button of exampleButtons) {
  button.addEventListener("click", () => run(button.dataset.command));
}
for (const input of modeInputs) {
  input.addEventListener("change", () => {
    const browsing = currentMode() === "browse";
    byId("catalog-source").lastChild.textContent = browsing ? " Browsing all Shopify" : " Our store";
    // The grid came from the other source, so its labels no longer
    // answer this question. Clear it, and re-run the last search.
    offers = [];
    renderOffers();
    controls();
    if (lastQuery) run(lastQuery);
    else notify(browsing
      ? "Searching every merchant on Shopify. Results are view only."
      : "Searching the connected store. Anything it does not sell is refused.");
  });
}
spin.addEventListener("click", async () => (await viewer)?.setSpin(spin.getAttribute("aria-pressed") !== "true"));
reset.addEventListener("click", async () => {
  const stage = await viewer;
  stage?.setSpin(false);
  stage?.reset();
});
