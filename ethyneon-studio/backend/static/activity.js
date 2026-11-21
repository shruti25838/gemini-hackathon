// Small "which model is doing what" chip, shown while any app request is in flight.
const TASKS = [
  [/\/studio\/room\/capture\/check$/, "gemini-decision-1", "checking photo coverage"],
  [/\/studio\/room\/scan$/, "gemini-decision-1 → gemini-4-neon", "checking coverage, then reading the room"],
  [/\/studio\/room\/[0-9a-f]{32}\/revise$/, "gemini-4-hydrogen", "updating the room and preferences"],
  [/\/studio\/room\/chat\/sessions\/[0-9a-f]{32}\/message$/, "gemini-4-argon", "room planner is working on your request"],
  [/\/studio\/room\/chat\/sessions\/[0-9a-f]{32}$/, "gemini-4-argon", "checking the furniture plan"],
  [/\/studio\/scene\/product$/, "gemini-4-hydrogen", "building the 3D model from the Shopify listing"],
  [/\/studio\/scene\/similar$/, "Shopify catalog", "finding alternatives"],
  [/\/studio\/scene\/import$/, "gemini-4-hydrogen", "converting the Shopify link to a 3D model"],
  [/\/studio\/scene\/edit$/, "guardrails", "checking the placement"],
  [/\/studio\/scene\/checkout$/, "Shopify + gemini-decision-1", "verifying the cart and auditing checkout"],
  [/\/studio\/command$/, "gemini-4-hydrogen", "interpreting your command"],
];
const pending = new Map();
let chip = null;

function render() {
  if (!chip) {
    chip = document.createElement("div");
    chip.className = "activity-chip";
    chip.setAttribute("role", "status");
    chip.setAttribute("aria-live", "polite");
    document.body.append(chip);
  }
  const latest = [...pending.values()].at(-1);
  chip.hidden = !latest;
  if (!latest) return;
  const model = document.createElement("code");
  model.textContent = latest.model;
  const task = document.createElement("span");
  task.textContent = latest.task + (pending.size > 1 ? ` (+${pending.size - 1} more)` : "");
  chip.replaceChildren(Object.assign(document.createElement("i"), {className: "activity-dot"}), model, task);
}

const original = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const url = new URL(typeof input === "string" ? input : input.url, location.href);
  const match = TASKS.find(([pattern]) => pattern.test(url.pathname));
  // Polling the plan quietly would flash the chip every second; only show it while the plan is running.
  if (!match || (init.method ?? "GET") === "GET" && !/chat\/sessions/.test(url.pathname)) return original(input, init);
  const id = Symbol(url.pathname);
  pending.set(id, {model: match[1], task: match[2]});
  render();
  return original(input, init).finally(() => {
    pending.delete(id);
    render();
  });
};
