import "./activity.js";
// Shared top navigation for the three steps of the app. The last built room carries into the chat link.
const ROOM_KEY = "ethyneon.room_id";
const STEPS = [
  {path: "/studio/room", label: "1 · Room photos"},
  {path: "/studio/room/chat", label: "2 · Plan (chat or voice)"},
  {path: "/studio/scene", label: "3 · Design & checkout"},
];

export function savedRoom() {
  try {
    return sessionStorage.getItem(ROOM_KEY);
  } catch {
    return null;
  }
}

export function rememberRoom(roomId) {
  try {
    sessionStorage.setItem(ROOM_KEY, roomId);
  } catch {
    // Without storage the chat simply asks for the measurements.
  }
    render();
}

export function chatUrl(roomId = savedRoom()) {
  return roomId && /^[0-9a-f]{32}$/.test(roomId) ? `/studio/room/chat?room_id=${roomId}` : "/studio/room/chat";
}

function render() {
  let nav = document.querySelector(".app-nav");
  if (!nav) {
    nav = document.createElement("nav");
    nav.className = "app-nav";
    nav.setAttribute("aria-label", "App steps");
    document.body.prepend(nav);
  }
  const here = location.pathname.replace(/\/$/, "") === "/studio/voice" ? "/studio/room/chat" : location.pathname.replace(/\/$/, "");
  nav.replaceChildren(...STEPS.map((step) => {
    const link = document.createElement("a");
    link.href = step.path === "/studio/room/chat" ? chatUrl()
      : step.path === "/studio/voice" ? chatUrl().replace("/studio/room/chat", "/studio/voice") : step.path;
    link.textContent = step.label;
    if (here === step.path) link.setAttribute("aria-current", "page");
    return link;
  }));
}

// Chat and voice are two modes of the planning step; chat is the default.
function modeSwitch() {
  const here = location.pathname.replace(/\/$/, "");
  if (here !== "/studio/room/chat" && here !== "/studio/voice") return;
  const bar = document.createElement("div");
  bar.className = "mode-switch";
  bar.setAttribute("role", "group");
  bar.setAttribute("aria-label", "Planning mode");
  for (const [path, label] of [["/studio/room/chat", "Chat mode"], ["/studio/voice", "Voice mode"]]) {
    const link = document.createElement("a");
    link.href = chatUrl().replace("/studio/room/chat", path);
    link.textContent = label;
    if (here === path) link.setAttribute("aria-current", "page");
    bar.append(link);
  }
  document.querySelector(".app-nav")?.after(bar);
}

render();
modeSwitch();
