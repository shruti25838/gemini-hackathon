import { chatUrl, rememberRoom } from "./app-nav.js";

const byId = (id) => document.getElementById(id);
const MAX_PHOTOS = 5;
const MAX_EDGE = 1600;
const CATEGORIES = {
  bedroom: "Bedroom", living_room: "Living room", office: "Office", gaming_room: "Gaming room", kitchen: "Kitchen",
  dining_room: "Dining room", library: "Library", studio_apartment: "Studio apartment", other: "Other",
};
const SOURCE = {decision_1: "Detected by Decision 1", neon: "Detected by Gemini 4 Neon", user: "Set by you"};
const WALL_NAMES = {wall_n: "North", wall_e: "East", wall_s: "South", wall_w: "West"};
const FEATURES = {door: "Door", window: "Window", cupboard: "Wardrobe / cupboard", pillar: "Pillar"};
const FEATURE_STATUS = {captured: "Captured", partial: "Partly visible", not_visible: "Missing", uncertain: "Unclear"};
const DIRECTION_ICON = {back: "⤢", other_corner: "⟲", up: "↑", down: "↓", left: "←", right: "→", same_view: "◎", none: "▣"};

let photos = [];
let room = null;
let busy = false;
let capture = null;
let checkSequence = 0;
const DECISION_MODEL = {model: "gemini-decision-1"};
const ROOM_MODEL = {model: "gemini-4-neon"};

// The line under the build button names the model doing the work. Strings are text; {model} entries render as tags.
function modelLine(state, ...segments) {
  const line = byId("model-line");
  line.hidden = !state;
  line.dataset.state = state || "";
  const parts = [];
  if (state === "working") {
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    spinner.setAttribute("aria-hidden", "true");
    parts.push(spinner);
  }
  for (const segment of segments) {
    const node = document.createElement(typeof segment === "string" ? "span" : "code");
    node.textContent = typeof segment === "string" ? segment : segment.model;
    parts.push(node);
  }
  line.replaceChildren(...parts);
}

const viewer = import("./room-viewer.js")
  .then(({RoomViewer}) => new RoomViewer(byId("room-viewer")))
  .catch(() => {
    notify("3D rendering is unavailable in this browser.", true);
    return null;
  });

function notify(message, error = false) {
  const status = byId("status");
  status.textContent = message;
  status.dataset.error = String(error);
}

function setStep(step) {
  const order = ["photos", "coverage", "room", "furnish", "checkout"];
  for (const item of document.querySelectorAll(".steps li")) {
    const index = order.indexOf(item.dataset.step);
    if (item.dataset.step === step) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
    item.dataset.done = String(index < order.indexOf(step));
  }
}

function setBusy(text) {
  busy = Boolean(text);
  byId("busy").hidden = !busy;
  byId("busy-text").textContent = text || "";
  controls();
}

function controls() {
  // Building is offered only after the Decision model confirms the photos cover the room.
  const build = byId("build");
  build.hidden = !photos.length || capture?.status !== "pass";
  build.disabled = busy;
  byId("photo-input").disabled = busy || photos.length >= MAX_PHOTOS;
  for (const button of document.querySelectorAll(".dim-row button, .thumbs button")) button.disabled = busy;
  for (const button of document.querySelectorAll("[data-view], #toggle-labels, #toggle-items")) button.disabled = busy || !room;
  for (const button of document.querySelectorAll(".scene-form button")) button.disabled = busy || !room;
  byId("save-pose").disabled = busy || !byId("move-object").value;
  byId("open-plan-chat").href = room ? "/studio/room/chat?room_id=" + room.room_id : "/studio/room/chat";
}

// Decode, re-encode as JPEG (drops EXIF and GPS), and bound the size before upload.
async function preparePhoto(file) {
  const bitmap = await createImageBitmap(file, {imageOrientation: "from-image"});
  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.86));
  const data = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
  return {url: URL.createObjectURL(blob), data, content_type: "image/jpeg"};
}

async function addFiles(files) {
  const room_left = MAX_PHOTOS - photos.length;
  const chosen = [...files].filter((file) => file.type.startsWith("image/") || /\.heic$/i.test(file.name)).slice(0, room_left);
  if (files.length > room_left) notify(`Only ${MAX_PHOTOS} photos are used. Extra photos were skipped.`);
  for (const file of chosen) {
    try {
      photos.push(await preparePhoto(file));
    } catch {
      notify(`${file.name} could not be read here. Use a JPEG or PNG, or a browser that opens HEIC.`, true);
    }
  }
  renderThumbs();
  checkCapture();
}

function requiredFeatures() {
  return [...document.querySelectorAll(".required input:checked")].map((input) => input.value);
}

// Decision 1 reviews the current photo set after every change, so the shopper knows what to shoot next.
async function checkCapture() {
  const sequence = ++checkSequence;
  capture = null;
  if (!photos.length) {
    byId("gate-panel").hidden = true;
    modelLine(null);
    setStep("photos");
    controls();
    return;
  }
  byId("gate-panel").hidden = false;
  const status = byId("gate-status");
  status.dataset.status = "checking";
  status.textContent = "Checking";
  notify(`Checking ${photos.length === 1 ? "the photo" : `${photos.length} photos`}…`);
  modelLine("working", "Checking coverage with Decision model", DECISION_MODEL);
  controls();
  try {
    const result = await request("/studio/room/capture/check", {
      photos: photos.map(({data, content_type}) => ({data, content_type})),
      required_features: requiredFeatures(),
    });
    if (sequence !== checkSequence) return;
    capture = result;
    renderGate(result);
    setStep("coverage");
    modelLine(result.status === "pass" ? "ok" : "blocked",
      result.status === "pass" ? "Coverage confirmed by" : `Not ready: ${result.follow_up.message}. Checked by`,
      DECISION_MODEL);
    notify(result.status === "pass"
      ? "Coverage looks good. Build the 3D room."
      : result.status === "rejected" ? result.message : "Next photo: " + result.follow_up.message + ".",
    result.status === "rejected");
  } catch (error) {
    if (sequence !== checkSequence) return;
    status.dataset.status = "needs_more";
    status.textContent = "Not checked";
    modelLine("blocked", "Coverage check failed. Remove and re-add a photo to retry.", DECISION_MODEL);
    notify("The coverage check failed: " + error.message, true);
  } finally {
    if (sequence === checkSequence) controls();
  }
}

function renderThumbs() {
  const list = byId("thumbs");
  list.replaceChildren();
  const views = new Map((room?.views ?? []).map((view) => [view.photo, view.facing_wall]));
  photos.forEach((photo, index) => {
    const item = document.createElement("li");
    const img = document.createElement("img");
    img.src = photo.url;
    img.alt = `Room photo ${index + 1}`;
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = index === 0 ? "Photo 1 · main wall" : `Photo ${index + 1}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.ariaLabel = `Remove photo ${index + 1}`;
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      URL.revokeObjectURL(photo.url);
      photos.splice(index, 1);
      renderThumbs();
      checkCapture();
    });
    item.append(img, tag, remove);
    if (views.has(index + 1)) {
      const facing = document.createElement("span");
      facing.className = "facing";
      facing.textContent = "Faces " + WALL_NAMES[views.get(index + 1)] + " wall";
      item.append(facing);
    }
    list.append(item);
  });
  byId("photo-count").textContent = `${photos.length} / ${MAX_PHOTOS}`;
  controls();
}

function meter(prefix, result) {
  byId(prefix + "-label").textContent = result.label;
  byId(prefix + "-bar").style.width = `${(result.score / result.max) * 100}%`;
  byId(prefix + "-pass").style.left = `${(result.pass_at / result.max) * 100}%`;
}

function renderGate(gate) {
  byId("gate-panel").hidden = false;
  const status = byId("gate-status");
  status.dataset.status = gate.status;
  status.textContent = {pass: "Covered", needs_more: "Needs more", rejected: "Not a room"}[gate.status];
  byId("gate-message").textContent = gate.status === "pass" ? gate.message : "Decision 1 needs another view before the room is reliable.";
  meter("coverage", gate.coverage);
  meter("quality", gate.quality);

  const features = byId("gate-features");
  // Absent features are left out; a feature the shopper ticked but the photos lack shows as Missing.
  const shownFeatures = Object.entries(gate.features ?? {}).filter(([, feature]) => feature.status !== "not_visible" || feature.required);
  features.hidden = shownFeatures.length === 0;
  features.replaceChildren(...shownFeatures.map(([name, feature]) => {
    const li = document.createElement("li");
    li.dataset.status = feature.status;
    li.dataset.missing = String(feature.required && feature.status !== "captured");
    const label = document.createElement("span");
    label.textContent = FEATURES[name] ?? name;
    const value = document.createElement("b");
    value.textContent = FEATURE_STATUS[feature.status] ?? feature.status;
    li.title = `${Math.round(feature.confidence * 100)}% confidence${feature.required ? " · required" : ""}`;
    li.append(label, value);
    return li;
  }));

  const guidance = byId("gate-guidance");
  guidance.replaceChildren(...(gate.guidance ?? []).map((step) => {
    const li = document.createElement("li");
    const icon = document.createElement("i");
    icon.textContent = DIRECTION_ICON[step.direction] ?? "•";
    icon.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    text.textContent = step.message + ".";
    li.append(icon, text);
    return li;
  }));

  const notes = byId("gate-notes");
  notes.replaceChildren();
  const shown = new Set((gate.guidance ?? []).map((step) => step.message + "."));
  const lines = [
    `Looks like a ${CATEGORIES[gate.category.choice]?.toLowerCase() ?? "room"} (${Math.round(gate.category.confidence * 100)}% confidence).`,
    ...gate.notes.filter((note) => !shown.has(note)),
  ];
  for (const line of lines) {
    const li = document.createElement("li");
    li.textContent = line;
    notes.append(li);
  }
}

function listItem(name, detail) {
  const li = document.createElement("li");
  const left = document.createElement("span");
  const right = document.createElement("span");
  left.textContent = name;
  right.textContent = detail;
  li.append(left, right);
  return li;
}

function sceneItems() {
  return [...room.existing_items, ...room.placed_products];
}

function poseFields(prefix) {
  const [x, y, z, yaw] = ["x", "y", "z", "yaw"].map((axis) => byId(prefix + "-" + axis).valueAsNumber);
  return {position: {x, y, z}, yaw};
}

function loadPose() {
  const item = sceneItems().find(({id}) => id === byId("move-object").value);
  for (const axis of ["x", "y", "z", "yaw"]) {
    byId("move-" + axis).value = item ? (axis === "yaw" ? item.pose.rotation.yaw : item.pose.position[axis]) : "";
  }
  controls();
}

function coordinates(position) {
  return ["x", "y", "z"].map((axis) => position[axis].toFixed(2)).join(", ") + " m";
}

function sceneItemRow(item) {
  const size = [item.size.width, item.size.depth, item.size.height].map((m) => m.toFixed(2)).join(" × ");
  const detail = [size + " m", coordinates(item.pose.position), item.pose.rotation.yaw.toFixed(1) + "° yaw"];
  if (Number.isFinite(item.price?.amount) && /^[A-Z]{3}$/.test(item.price.currency)) {
    const money = new Intl.NumberFormat(undefined, {style: "currency", currency: item.price.currency});
    detail.unshift(money.format(item.price.amount / 10 ** money.resolvedOptions().maximumFractionDigits));
  }
  const row = listItem(item.label + (item.fixed ? " · Fixed" : ""), detail.join(" · "));
  if (item.listing_url) {
    try {
      const url = new URL(item.listing_url);
      if (url.protocol === "https:") {
        const link = document.createElement("a");
        link.href = url.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = item.label;
        row.firstElementChild.replaceChildren(link);
      }
    } catch {
      // Keep the title visible when the merchant has no usable listing link.
    }
  }
  return row;
}

function renderRoom() {
  byId("room-panel").hidden = false;
  byId("revision").textContent = "Revision " + room.revision;
  byId("stage-title").textContent = `${CATEGORIES[room.category]} · ${["width", "depth", "height"].map((axis) => room.dimensions[axis].value_m.toFixed(2)).join(" × ")} m`;

  const category = byId("category");
  category.replaceChildren(...Object.entries(CATEGORIES).map(([value, text]) => new Option(text, value, false, value === room.category)));
  byId("category-source").textContent = SOURCE[room.category_source];
  byId("preference").value = room.preference;
  const brief = room.design_brief;
  const summary = byId("preference-summary");
  summary.hidden = !brief;
  summary.textContent = brief ? [brief.purpose, ...brief.styles, ...brief.colors, ...brief.priorities].filter(Boolean).join(" · ") : "";

  for (const row of document.querySelectorAll(".dim-row")) {
    const dimension = room.dimensions[row.dataset.axis];
    row.querySelector("input").value = dimension.value_m.toFixed(2);
    const status = row.querySelector("em");
    status.textContent = dimension.status === "confirmed" ? "Confirmed" : "Estimate";
    status.dataset.status = dimension.status;
  }

  const surfaces = byId("surfaces");
  surfaces.replaceChildren();
  for (const surface of [...room.walls, {...room.floor, id: "floor"}]) {
    const wrap = document.createElement("label");
    wrap.className = "surface";
    const input = document.createElement("input");
    input.type = "color";
    input.value = surface.color;
    input.addEventListener("change", () => {
      revise(surface.id === "floor" ? {floor_color: input.value} : {wall_colors: {[surface.id]: input.value}},
        `${surface.id === "floor" ? "Floor" : WALL_NAMES[surface.id] + " wall"} colour updated.`);
    });
    const caption = document.createElement("span");
    caption.textContent = surface.id === "floor" ? "Floor" : WALL_NAMES[surface.id];
    caption.title = surface.color_source.replaceAll("_", " ");
    wrap.append(input, caption);
    surfaces.append(wrap);
  }

  byId("openings").replaceChildren(...room.openings.map((opening) => listItem(
    `${opening.id.replace("_", " ")} · ${WALL_NAMES[opening.wall]} wall`,
    `${opening.width_m.toFixed(2)} × ${opening.height_m.toFixed(2)} m` +
      " · " + coordinates(opening.position) +
      (opening.type === "door" && opening.swing !== "none" ? ` · ${opening.hinge} hinge, ${opening.swing}` : "") +
      (opening.sill_m ? ` · sill ${opening.sill_m.toFixed(2)}` : ""),
  )));
  byId("items").replaceChildren(...sceneItems().map(sceneItemRow));
  const select = byId("move-object");
  const selectedItem = select.value;
  select.replaceChildren(...sceneItems().filter((item) => !item.fixed).map(
    (item) => new Option(item.label, item.id, false, item.id === selectedItem),
  ));
  loadPose();
  const {lighting} = room;
  byId("lighting-summary").textContent = lighting.status === "estimated"
    ? "Estimated from " + lighting.windows.length + " window(s) · " + lighting.window_area_m2.toFixed(2) + " m² of glazing"
    : "Daylight is unknown. Add a view of the windows to estimate it.";
  byId("painting-spots").replaceChildren(...lighting.painting_spots.map((spot) => listItem(
    WALL_NAMES[spot.wall] + " wall",
    coordinates(spot.position) + (spot.relative_exposure === null ? "" : " · relative exposure " + spot.relative_exposure),
  )));
  const missing = [...room.missing_evidence, ...room.warnings.map((warning) => warning.replace(/^geometry: /, ""))];
  byId("missing").replaceChildren(...missing.map((text) => {
    const li = document.createElement("li");
    li.textContent = text;
    return li;
  }));
  byId("missing-title").hidden = missing.length === 0;
  renderThumbs();
}

async function showRoom() {
  const stage = await viewer;
  byId("empty-stage").hidden = true;
  if (stage) {
    stage.show(room);
    byId("room-hint").hidden = false;
  }
  renderRoom();
  setStep("room");
  controls();
}

async function request(url, body) {
  const response = await fetch(url, {
    method: body ? "POST" : "GET",
    headers: body ? {"Content-Type": "application/json"} : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = await response.json().catch(() => ({message: "The server returned an unreadable response."}));
  if (!response.ok) throw Object.assign(new Error(result.message ?? "Request failed."), {status: response.status, result});
  return result;
}

async function build() {
  if (busy || !photos.length || capture?.status !== "pass") return;
  setStep("coverage");
  setBusy(`Building the room with ${ROOM_MODEL.model}…`);
  modelLine("working", "Re-checking with", DECISION_MODEL, "then building with", ROOM_MODEL);
  notify("Analysing " + photos.length + (photos.length === 1 ? " photo…" : " photos…"));
  const value = parseFloat(byId("measure-value").value);
  try {
    const result = await request("/studio/room/scan", {
      photos: photos.map(({data, content_type}) => ({data, content_type})),
      required_features: requiredFeatures(),
      measurement: Number.isFinite(value) && value > 0 ? {axis: byId("measure-axis").value, value_m: value} : null,
    });
    capture = result.gate;
    renderGate(result.gate);
    if (!result.room) {
      setStep("coverage");
      modelLine("blocked", `Not ready: ${result.gate.follow_up.message}. Checked by`, DECISION_MODEL);
      notify(result.gate.message, true);
      return;
    }
    room = result.room;
    await showRoom();
    modelLine("ok", "Built by", ROOM_MODEL, "· verified by", DECISION_MODEL);
    rememberRoom(room.room_id);
    byId("plan-furniture").href = chatUrl(room.room_id);
    byId("plan-furniture").hidden = false;
    notify("Room ready. Confirm one real measurement to lock the scale.");
  } catch (error) {
    setStep(room ? "room" : "coverage");
    modelLine("blocked", "Build failed. Try again.", ROOM_MODEL);
    notify(error.message || "Could not build the room. Try again.", true);
  } finally {
    setBusy("");
  }
}

async function revise(change, done) {
  if (!room || busy) return;
  setBusy("Updating the room…");
  try {
    const result = await request(`/studio/room/${room.room_id}/revise`, {
      base_revision: room.revision, action_id: crypto.randomUUID(), ...change,
    });
    room = result.room;
    await showRoom();
    if (change.move_item && room.existing_items.some((item) => item.id === change.move_item.item_id)) {
      byId("toggle-items").setAttribute("aria-pressed", "true");
      (await viewer)?.setItems(true);
    }
    notify(done);
  } catch (error) {
    if (error.status === 409) {
      try {
        room = (await request(`/studio/room/${room.room_id}`)).room;
        await showRoom();
      } catch (refreshError) {
        notify(refreshError.message, true);
        return;
      }
    }
    notify(error.message, true);
    renderRoom();
  } finally {
    setBusy("");
  }
}

const dropzone = byId("dropzone");
byId("photo-input").addEventListener("change", (event) => {
  addFiles(event.target.files);
  event.target.value = "";
});
for (const type of ["dragenter", "dragover"]) {
  dropzone.addEventListener(type, (event) => {
    event.preventDefault();
    dropzone.dataset.over = "true";
  });
}
for (const type of ["dragleave", "drop"]) {
  dropzone.addEventListener(type, () => { dropzone.dataset.over = "false"; });
}
dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  if (!busy) addFiles(event.dataTransfer.files);
});
byId("build").addEventListener("click", build);
byId("preference-form").addEventListener("submit", (event) => {
  event.preventDefault();
  revise({preference: byId("preference").value}, "Design preferences updated.");
});
byId("move-object").addEventListener("change", loadPose);
byId("placement-form").addEventListener("submit", (event) => {
  event.preventDefault();
  revise({move_item: {item_id: byId("move-object").value, ...poseFields("move")}}, "Object position updated.");
});
byId("product-form").addEventListener("submit", (event) => {
  event.preventDefault();
  revise({add_product: {variant_id: byId("product-variant").value.trim(), ...poseFields("product")}}, "Shopify product placed.");
});
byId("export-scene").addEventListener("click", () => {
  if (!room) return;
  const link = document.createElement("a");
  const url = URL.createObjectURL(new Blob([JSON.stringify(room, null, 2)], {type: "application/json"}));
  link.href = url;
  link.download = "ethyneon-room-" + room.revision + ".json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
for (const input of document.querySelectorAll(".required input")) input.addEventListener("change", checkCapture);
byId("category").addEventListener("change", (event) => {
  revise({category: event.target.value}, "Room category set to " + CATEGORIES[event.target.value] + ".");
});
for (const row of document.querySelectorAll(".dim-row")) {
  const confirm = () => {
    const value = parseFloat(row.querySelector("input").value);
    if (!Number.isFinite(value)) return notify("Enter the measurement in metres.", true);
    revise({measurement: {axis: row.dataset.axis, value_m: value}}, `${row.dataset.axis[0].toUpperCase() + row.dataset.axis.slice(1)} confirmed at ${value.toFixed(2)} m.`);
  };
  row.querySelector("button").addEventListener("click", confirm);
  row.querySelector("input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") confirm();
  });
}
for (const button of document.querySelectorAll("[data-view]")) {
  button.addEventListener("click", async () => (await viewer)?.view(button.dataset.view));
}
byId("toggle-labels").addEventListener("click", async (event) => {
  const on = event.currentTarget.getAttribute("aria-pressed") !== "true";
  event.currentTarget.setAttribute("aria-pressed", String(on));
  (await viewer)?.setLabels(on);
});
byId("toggle-items").addEventListener("click", async (event) => {
  const on = event.currentTarget.getAttribute("aria-pressed") !== "true";
  event.currentTarget.setAttribute("aria-pressed", String(on));
  (await viewer)?.setItems(on);
});
controls();
