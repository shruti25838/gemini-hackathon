const byId = (id) => document.getElementById(id);
const url = new URL(location.href);
const endpoint = "/studio/room/chat/sessions";
const input = byId("chat-input");
const historyList = byId("chat-messages");
let state = null;
let busy = false;

async function request(path, body) {
  const response = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? {"Content-Type": "application/json"} : {},
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(result.message ?? "Could not load the room chat."), {status: response.status});
  return result;
}

function draw() {
  const confirm = byId("chat-confirm");
  const send = byId("chat-send");
  confirm.hidden = state?.phase !== "review";
  confirm.disabled = busy;
  send.disabled = busy || !state;
  send.textContent = state?.phase === "planning" ? "Check plan" : "Send";
  if (!state) return;
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
  if (state.phase !== "measurements") input.placeholder = "Describe what you want in the room";
}

async function poll() {
  for (let attempt = 0; state.phase === "planning" && attempt < 60; attempt++) {
    byId("chat-status").textContent = "Argon is planning and checking Shopify items…";
    await new Promise((resolve) => setTimeout(resolve, 2500));
    state = await request(endpoint + "/" + state.id);
  }
  byId("chat-status").textContent = state.phase === "planning" ? "The plan is still running. Select Check plan to continue." : "";
}

async function send(text) {
  if (!state || busy || (!text && state.phase !== "planning")) return;
  busy = true;
  draw();
  try {
    if (state.phase !== "planning") {
      state = await request(endpoint + "/" + state.id + "/message", {text, review_id: state.review_id ?? null});
      if (input.value.trim() === text) input.value = "";
      draw();
    }
    await poll();
  } catch (error) {
    byId("chat-status").textContent = error.message;
  } finally {
    busy = false;
    draw();
    input.focus();
  }
}

async function start() {
  try {
    const session = url.searchParams.get("session");
    if (session) {
      try {
        state = await request(endpoint + "/" + session);
      } catch (error) {
        if (error.status !== 404) throw error;
      }
    }
    if (!state) {
      state = await request(endpoint, {room_id: url.searchParams.get("room_id")});
      url.search = "";
      url.searchParams.set("session", state.id);
      window.history.replaceState(null, "", url);
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
start();
