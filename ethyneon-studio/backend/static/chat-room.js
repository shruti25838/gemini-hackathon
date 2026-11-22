// Live 3D room beside the room chat: Argon's suggestions appear in the room as they are proposed (amber tags),
// clicking a product opens the small rotating panel with Shopify alternatives, and a voice button talks to the
// same chat session through Gemini 3.8 Live.
import { ProductViewer } from "./viewer.js";
import { SceneViewer, modelDot, priceLabel, setDot } from "./scene-viewer.js";
import { createAlternativeSearch } from "./shopify-alternatives.js";

const byId = (id) => document.getElementById(id);
const STATE_TEXT = {cached: "3D cached", missing: "No 3D · click to build", building: "Building 3D…", failed: "3D failed · click to retry"};
const records = new Map();
const modelState = new Map();
let chat = null;
let scene = null;
let lastKey = "";
let selected = null;

const room = new SceneViewer(byId("room-viewer"), (item) => selectProduct(item), {
  onMoved: (id, point) => moveItem(id, {x: point.x, z: point.z}),
});
room.dragAny = true;  // plan phase: drag any product on the floor; a click still opens its panel

async function moveItem(id, change) {
  const result = await window.moveRoomChatItem?.(id, change);
  if (result !== "moved") loadRoom(true);  // guardrails refused (or busy): snap back to the saved layout
}
const productViewer = new ProductViewer(byId("product-viewer"), () => {});

function post(url, body) {
  return fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
    .then(async (response) => {
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw Object.assign(new Error(result.message ?? "Request failed."), {status: response.status});
      return result;
    });
}

function safeUrl(value, host) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && (!host || url.hostname === host) ? url : null;
  } catch {
    return null;
  }
}

// ---------- 3D model state (green cached / red missing / amber building) ----------

const stateOf = (item) => (item.variant_id ? modelState.get(item.variant_id) ?? "missing" : "failed");

function showState(variantId, value) {
  modelState.set(variantId, value);
  for (const dot of document.querySelectorAll(`[data-variant="${CSS.escape(variantId)}"] .model-dot`)) setDot(dot, value);
  const head = byId("product-state");
  if (head.dataset.variant === variantId) head.querySelector("small").textContent = STATE_TEXT[value];
}

async function refreshStates(ids) {
  const wanted = [...new Set(ids.filter(Boolean))].filter((id) => modelState.get(id) !== "building");
  if (!wanted.length) return;
  try {
    const states = await post("/studio/scene/models/status", {variant_ids: wanted.slice(0, 80)});
    for (const [id, value] of Object.entries(states)) showState(id, value);
  } catch {
    // Keep the last known dots.
  }
}

function productRecord(variantId) {
  if (!records.has(variantId)) {
    const pending = post("/studio/scene/product", {variant_id: variantId});
    pending.catch(() => records.delete(variantId));
    records.set(variantId, pending);
  }
  return records.get(variantId);
}

async function resolveModel(item) {
  if (stateOf(item) !== "cached") return null;
  try {
    return (await productRecord(item.variant_id)).model;
  } catch {
    return null;
  }
}

async function buildModel(variantId) {
  if (modelState.get(variantId) !== "cached") showState(variantId, "building");
  try {
    const record = await productRecord(variantId);
    showState(variantId, "cached");
    for (const item of scene?.placed_products ?? []) if (item.variant_id === variantId) room.attachModel(item.id, record.model);
    return record;
  } catch (error) {
    showState(variantId, "failed");
    throw error;
  }
}

// ---------- Live room from the chat ----------

async function loadRoom(force = false) {
  const id = chat?.id;
  if (!id) return;
  const key = `${id}:${chat.phase}:${chat.messages.length}:${chat.review_id ?? ""}`;
  if (!force && key === lastKey) return;
  lastKey = key;
  try {
    const result = await fetch(`/studio/scene/chat/${id}`).then((response) => response.json());
    if (lastKey !== key) return;
    if (!result.scene) {
      byId("stage-title").textContent = "Waiting for the room size";
      return;
    }
    await refreshStates(result.scene.placed_products.map((item) => item.variant_id));
    if (lastKey !== key) return;
    scene = result.scene;
    const {width, depth, height} = scene.dimensions;
    byId("stage-title").textContent = `${[width, depth, height].map((d) => d.value_m.toFixed(2)).join(" × ")} m`;
    byId("stage-eyebrow").textContent = result.phase === "review" ? "Live room · suggestions to confirm"
      : result.phase === "planning" ? "Live room · Argon is planning…" : "Live room";
    room.showScene(scene, resolveModel, stateOf);
    for (const item of scene.placed_products) {
      if (item.proposed) room.products.get(item.id)?.group.children.find((child) => child.isCSS2DObject)?.element.classList.add("proposed");
    }
    renderSuggested(result.phase);
    if (selected && !scene.placed_products.some((item) => item.id === selected.id)) closePanel();
  } catch {
    if (lastKey === key) lastKey = "";
  }
}

function renderSuggested(phase) {
  const items = scene.placed_products;
  byId("suggested-empty").textContent = items.length ? "" : "Room items will appear here.";
  byId("suggested-title").textContent = phase === "review" ? "Suggested items" : "Items in the room";
  const total = items.reduce((sum, item) => sum + (item.price?.amount ?? 0), 0);
  byId("suggested-total").textContent = total ? priceLabel({amount: total, currency: items.find((item) => item.price)?.price.currency ?? "USD"}) : "";
  byId("suggested-list").replaceChildren(...items.map((item) => {
    const li = document.createElement("li");
    li.dataset.variant = item.variant_id ?? "";
    li.dataset.placed = String(!item.proposed);
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(item.id === selected?.id));
    const name = document.createElement("span");
    name.textContent = item.label;
    const price = document.createElement("b");
    price.textContent = priceLabel(item.price) || "—";
    button.append(modelDot(stateOf(item)), name, price);
    button.addEventListener("click", () => room.select(item.id));
    li.append(button);
    return li;
  }));
}

window.addEventListener("roomchat:state", (event) => {
  chat = event.detail;
  loadRoom();
});

// ---------- Product panel (same as the Design view) ----------

function showFacts(title, price, rows, listing, variantId) {
  byId("product-title").textContent = title;
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
  const head = byId("product-state");
  head.dataset.variant = variantId ?? "";
  head.replaceChildren(modelDot(stateOf({variant_id: variantId})), document.createElement("small"));
  head.querySelector("small").textContent = variantId ? STATE_TEXT[stateOf({variant_id: variantId})] : "";
}

async function spin(pending, token) {
  byId("product-message").hidden = false;
  byId("product-message").textContent = "Loading the 3D model…";
  try {
    const product = await pending;
    if (token !== spin.token) return;
    await productViewer.show(product);
    byId("product-message").hidden = true;
  } catch (error) {
    if (token === spin.token) byId("product-message").textContent = "3D model unavailable: " + error.message;
  }
}

function closePanel() {
  selected = null;
  spin.token = null;
  byId("product-pop").hidden = true;
  room.selectedId = null;
  room.highlight();
}

function selectProduct(item) {
  selected = item;
  byId("product-pop").hidden = false;
  byId("rotate-tools").hidden = false;
  byId("product-eyebrow").textContent = item.proposed ? "Suggested by Argon" : "In your room";
  const cm = (m) => Math.round(m * 100);
  showFacts(item.label, item.price, [["Size", `${cm(item.size.width)} × ${cm(item.size.depth)} × ${cm(item.size.height)} cm`]],
    item.listing_url, item.variant_id);
  if (item.variant_id) spin(buildModel(item.variant_id), spin.token = Symbol(item.id));
  alternatives.load(item);
  for (const button of document.querySelectorAll("#suggested-list button")) button.setAttribute("aria-pressed", "false");
}

const alternatives = createAlternativeSearch({
  post, renderCard: alternativeCard, refreshStates, getSelected: () => selected,
});

function alternativeCard(offer) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = "suggestion";
  card.dataset.variant = offer.variant_id;
  const imageUrl = safeUrl(offer.image_url, "cdn.shopify.com");
  let image;
  if (imageUrl) {
    imageUrl.searchParams.set("width", "240");
    image = Object.assign(document.createElement("img"), {src: imageUrl.href, alt: "", loading: "lazy"});
  } else {
    image = Object.assign(document.createElement("div"), {className: "missing-image"});
  }
  const price = document.createElement("b");
  price.textContent = priceLabel(offer.price) || "—";
  card.title = offer.title;
  card.append(image, price, modelDot(stateOf({variant_id: offer.variant_id})));
  card.addEventListener("click", async () => {
    const productId = selected?.id;
    if (!productId) return;
    const outcome = await window.replaceRoomChatItem(productId, offer.variant_id);
    if (selected?.id !== productId) return;
    if (outcome === "replaced") {
      closePanel();
    } else if (outcome === "preview") {
      byId("product-eyebrow").textContent = "Shopify alternative · 3D preview";
      showFacts(offer.title, offer.price, [["Room fit", "Dimensions needed for placement"]], offer.listing_url, offer.variant_id);
      spin(buildModel(offer.variant_id), spin.token = Symbol(offer.variant_id));
    }
  });
  return card;
}

byId("close-product").addEventListener("click", closePanel);
for (const button of document.querySelectorAll("[data-view]")) button.addEventListener("click", () => room.view(button.dataset.view));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !byId("product-pop").hidden) closePanel();
});

// ---------- Voice in the same chat (Gemini 3.8 Live) ----------

const CAPTURE = `class Capture extends AudioWorkletProcessor {
  constructor() { super(); this.buffer = new Int16Array(1600); this.length = 0; }
  process(inputs) {
    const channel = inputs[0][0];
    if (channel) for (let i = 0; i < channel.length; i += 1) {
      const s = Math.max(-1, Math.min(1, channel[i]));
      this.buffer[this.length++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.length === this.buffer.length) { const chunk = this.buffer.slice(); this.port.postMessage(chunk.buffer, [chunk.buffer]); this.length = 0; }
    }
    return true;
  }
}
registerProcessor("capture", Capture);`;
let voice = null;

function toBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

function voiceStatus(text) {
  byId("chat-status").textContent = text;
}

async function startVoice() {
  const session = window.roomChatSession?.();
  if (!session) return;
  const button = byId("voice-toggle");
  button.disabled = true;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, channelCount: 1}});
    const capture = new AudioContext({sampleRate: 16000});
    const url = URL.createObjectURL(new Blob([CAPTURE], {type: "application/javascript"}));
    await capture.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    const node = new AudioWorkletNode(capture, "capture");
    capture.createMediaStreamSource(stream).connect(node);
    const playback = new AudioContext({sampleRate: 24000});
    const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/studio/voice/live?session=${session}`);
    voice = {stream, capture, playback, socket, next: 0, sources: new Set(), said: ""};
    node.port.onmessage = ({data}) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: "audio", data: toBase64(data)}));
    };
    socket.onmessage = ({data}) => onVoice(JSON.parse(data));
    socket.onclose = () => stopVoice();
    voiceStatus("Connecting voice to gemini-3.8-live…");
  } catch (error) {
    voiceStatus(error.name === "NotAllowedError" ? "Allow the microphone to use voice." : error.message);
    await stopVoice();
  } finally {
    button.disabled = false;
  }
}

function onVoice(message) {
  if (message.type === "ready") {
    byId("voice-toggle").dataset.live = "true";
    byId("voice-toggle").textContent = "■ Stop voice";
    voiceStatus("Voice on · gemini-3.8-live is listening. Speak naturally; it uses this same chat.");
  } else if (message.type === "audio") {
    const bytes = Uint8Array.from(atob(message.data), (char) => char.charCodeAt(0));
    const samples = new Int16Array(bytes.buffer, 0, Math.floor(bytes.length / 2));
    const buffer = voice.playback.createBuffer(1, samples.length, 24000);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i += 1) channel[i] = samples[i] / 0x8000;
    const source = voice.playback.createBufferSource();
    source.buffer = buffer;
    source.connect(voice.playback.destination);
    const start = Math.max(voice.playback.currentTime + 0.02, voice.next);
    source.start(start);
    voice.next = start + buffer.duration;
    voice.sources.add(source);
    source.onended = () => voice?.sources.delete(source);
  } else if (message.type === "interrupted") {
    for (const source of voice.sources) source.stop();
    voice.sources.clear();
    voice.next = 0;
  } else if (message.type === "caption") {
    voice.said = message.role === "gemini" ? voice.said + message.text : "";
    voiceStatus(message.role === "you" ? `You: ${message.text}` : `Gemini: ${voice.said}`);
  } else if (message.type === "chat") {
    window.dispatchEvent(new CustomEvent("roomchat:set", {detail: message.chat}));
  } else if (message.type === "tool") {
    voiceStatus(message.status === "running" ? "Gemini is asking the room planner…" : "Planner replied.");
  } else if (message.type === "error" || message.type === "notice") {
    voiceStatus(message.message);
  }
}

async function stopVoice() {
  const current = voice;
  voice = null;
  if (!current) return;
  if (current.socket.readyState <= WebSocket.OPEN) current.socket.close();
  for (const track of current.stream.getTracks()) track.stop();
  await current.capture.close().catch(() => {});
  await current.playback.close().catch(() => {});
  const button = byId("voice-toggle");
  button.dataset.live = "false";
  button.textContent = "🎙 Voice";
  voiceStatus("Voice off. Keep typing, or press Voice again.");
}

const voiceButton = Object.assign(document.createElement("button"), {type: "button", id: "voice-toggle", className: "voice-toggle", textContent: "🎙 Voice"});
voiceButton.title = "Talk to the room chat with Gemini 3.8 Live (microphone only)";
voiceButton.addEventListener("click", () => (voice ? stopVoice() : startVoice()));
byId("chat-form").append(voiceButton);
addEventListener("pagehide", () => stopVoice());
chat = window.roomChatState?.() ?? null;
if (chat) loadRoom();

for (const [id, delta] of [["rotate-left", -15], ["rotate-right", 15]]) {
  byId(id).addEventListener("click", () => {
    if (selected) moveItem(selected.id, {yaw: (selected.pose.rotation.yaw ?? 0) + delta});
  });
}
