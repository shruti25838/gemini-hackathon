const byId = (id) => document.getElementById(id);
const url = new URL(location.href);
const endpoint = "/studio/room/chat/sessions";
const input = byId("chat-input");
const historyList = byId("chat-messages");
let state = null;
let busy = false;
let submittingPlan = false;
let pollingFailed = false;

// Persist the session ID across navigation (e.g. chat → checkout → back to chat).
const CHAT_SESSION_KEY = "ethyneon.chat_session";
function saveSession(id) { try { sessionStorage.setItem(CHAT_SESSION_KEY, id); } catch {} }
function savedSession() { try { return sessionStorage.getItem(CHAT_SESSION_KEY); } catch { return null; } }
function clearSession() { try { sessionStorage.removeItem(CHAT_SESSION_KEY); } catch {} }

async function request(path, body) {
  const response = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? {"Content-Type": "application/json"} : {},
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
    signal: body ? undefined : AbortSignal.timeout(90_000),
  });
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(result.message ?? "Could not load the room chat."), {
    status: response.status, code: result.code,
  });
  return result;
}

function draw() {
  const confirm = byId("chat-confirm");
  const sendButton = byId("chat-send");
  confirm.hidden = state?.phase !== "review";
  confirm.disabled = busy;
  const planning = submittingPlan || (state?.phase === "planning" && !pollingFailed);
  byId("chat-planning").hidden = !planning;
  sendButton.disabled = busy || planning || !state;
  sendButton.textContent = planning ? "Planning…" : state?.phase === "planning" ? "Retry status" : "Send";
  if (!state) return;
  const choices = byId("room-types");
  byId("planning-hints").hidden = ["room_type", "measurements"].includes(state.phase);
  choices.hidden = busy || !state.room_types?.length;
  choices.replaceChildren(...(state.room_types ?? []).map((label) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.disabled = busy;
    button.addEventListener("click", () => send(label));
    return button;
  }));
  window.dispatchEvent(new CustomEvent("roomchat:state", {detail: state}));
  historyList.replaceChildren(...state.messages.map((message) => {
    const item = document.createElement("li");
    item.dataset.role = message.role;
    const name = document.createElement("strong");
    name.textContent = message.role === "user" ? "You" : "Room chat";
    const text = document.createElement("span");
    text.textContent = message.text;
    item.append(name, text);
    return item;
  }));
  historyList.scrollTop = historyList.scrollHeight;
  const {proposed_products, placed_products, ...geometry} = state.document;
  const view = state.phase === "review" ? {proposed_products, ...geometry, placed_products}
    : state.phase === "placed" ? {placed_products, ...geometry} : state.document;
  byId("json-heading").textContent = state.phase === "placed" ? "Placed room JSON" : "Room JSON";
  byId("plan-json-body").textContent = JSON.stringify(view, null, 2);
  input.placeholder = state.phase === "room_type" ? "Choose or type a room type"
    : state.phase === "measurements" ? "4 × 3.5 × 2.6 m" : "Describe what you want in the room";

  const checkout = byId("move-to-checkout");
  const placed = state.phase === "placed";
  const hasShopifyItems = (state.document.placed_products ?? []).some((item) => item.variant_id);
  checkout.disabled = busy || !placed || state.guardrails_ok !== true || !hasShopifyItems;
  const problems = state.guardrails_problems ?? [];
  byId("checkout-readiness").textContent = placed && problems.length ? problems.join(" ")
    : placed && state.guardrails_ok && hasShopifyItems ? "Checkout guardrails passed."
    : "Confirm placement to check the room.";
}

async function poll() {
  while (state.phase === "planning") {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    if (state.phase !== "planning") return;
    const previous = state;
    try {
      const result = await request(endpoint + "/" + previous.id);
      if (state === previous) state = result;
      byId("chat-status").textContent = "";
    } catch (error) {
      if (error.status && error.status < 500 && error.status !== 429) throw error;
      byId("chat-status").textContent = "Reconnecting to the planner…";
    }
  }
}

async function send(text) {
  if (!state || busy || (!text && state.phase !== "planning")) return;
  const choosingRoomType = state.phase === "room_type";
  submittingPlan = !["measurements", "planning"].includes(state.phase) && text !== "Confirm placement"
    && (!choosingRoomType || Boolean(state.source_room_id));
  pollingFailed = false;
  busy = true;
  byId("chat-status").textContent = "";
  if (choosingRoomType) byId("chat-status").textContent = `Selected: ${text}`;
  if (text === "Confirm placement") byId("chat-status").textContent = "Checking placement and Shopify listings…";
  draw();
  try {
    if (state.phase !== "planning") {
      state = await request(endpoint + "/" + state.id + "/message", {text, review_id: state.review_id ?? null});
      byId("chat-status").textContent = "";
      submittingPlan = false;
      if (input.value.trim() === text) input.value = "";
      draw();
    }
    await poll();
  } catch (error) {
    if (error.status === 404 && error.code === "room_chat_missing") {
      clearSession();
      url.searchParams.delete("session");
      state = null;
      await start();
      if (state) byId("chat-status").textContent = "Opened a new chat because the previous session was unavailable.";
      return;
    }
    pollingFailed = state.phase === "planning";
    byId("chat-status").textContent = error.message;
  } finally {
    submittingPlan = false;
    busy = false;
    draw();
    input.focus();
  }
}

async function start() {
  try {
    // 1. Try the session ID from the URL param first.
    // 2. Fall back to sessionStorage so returning from checkout restores the same session.
    const sessionFromUrl = url.searchParams.get("session");
    const requestedRoomId = url.searchParams.get("room_id");
    const sessionId = sessionFromUrl || savedSession();

    if (sessionId) {
      try {
        state = await request(endpoint + "/" + sessionId);
        if (requestedRoomId && state.source_room_id !== requestedRoomId) {
          // An explicit room links to its scan, even when a different chat was saved in this tab.
          state = null;
          clearSession();
        } else {
          url.searchParams.set("session", sessionId);
          window.history.replaceState(null, "", url);
          saveSession(state.id);
        }
      } catch (error) {
        if (error.status !== 404) throw error;
        // Session expired — fall through to create a new one.
        clearSession();
        state = null;
      }
    }
    if (!state) {
      try {
        state = await request(endpoint, {room_id: requestedRoomId});
      } catch (error) {
        if (error.status !== 404) throw error;
        state = await request(endpoint, {});  // the room is gone (server restarted): ask for measurements instead
        url.searchParams.delete("room_id");
        byId("chat-status").textContent = "The room scan is unavailable. Choose a room type to continue with measurements.";
      }
      const roomId = url.searchParams.get("room_id");
      url.search = "";
      url.searchParams.set("session", state.id);
      if (roomId) url.searchParams.set("room_id", roomId);
      window.history.replaceState(null, "", url);
      saveSession(state.id);
    }
    draw();
    if (state.phase === "planning") await send("");
  } catch (error) {
    byId("chat-status").textContent = error.message;
  }
}

byId("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  send(input.value.trim());
});
byId("chat-confirm").addEventListener("click", () => send("Confirm placement"));
byId("move-to-checkout").addEventListener("click", (event) => {
  if (!event.currentTarget.disabled) location.assign("/studio/scene");
});
for (const hint of document.querySelectorAll("[data-say]")) hint.addEventListener("click", () => send(hint.dataset.say));
// Voice mode updates the same chat session; show its state here.
window.addEventListener("roomchat:set", (event) => {
  state = event.detail;
  draw();
});
window.roomChatSession = () => state?.id;
window.roomChatState = () => state;
// Drag-and-drop or rotate an item in the plan; the server runs the guardrails before the draft changes.
window.moveRoomChatItem = async (productId, change) => {
  if (!state || busy || !["review", "placed"].includes(state.phase)) return "busy";
  busy = true;
  draw();
  try {
    state = await request(endpoint + "/" + state.id + "/move", {product_id: productId, review_id: state.review_id ?? null, ...change});
    byId("chat-status").textContent = "";
    return "moved";
  } catch (error) {
    byId("chat-status").textContent = "Not moved: " + error.message;
    return "blocked";
  } finally {
    busy = false;
    draw();
  }
};
window.replaceRoomChatItem = async (productId, variantId) => {
  if (!state || busy || !["review", "placed"].includes(state.phase)) return;
  busy = true;
  byId("chat-status").textContent = "Checking whether the Shopify alternative fits…";
  draw();
  try {
    state = await request(endpoint + "/" + state.id + "/replace", {
      product_id: productId, variant_id: variantId, review_id: state.review_id ?? null,
    });
    byId("chat-status").textContent = "";
    return "replaced";
  } catch (error) {
    byId("chat-status").textContent = error.message;
    if (error.code === "replacement_size_unknown") return "preview";
  } finally {
    busy = false;
    draw();
  }
};
start();
