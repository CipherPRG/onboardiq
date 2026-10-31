// The recorded-session list and the playback controls.
import { $, el } from "./util.js";
import { clock, halt, togglePause } from "./clock.js";
import { playSession } from "./onboarding.js";
import { playHealth } from "./health.js";
import { isProven } from "./model.js";

const buttons = {};
let current = null;

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

async function begin(pb) {
  const run = pb.kind === "health" ? playHealth : playSession;
  $("play").textContent = "Pause";
  const token = clock.token + 1;   // the replay takes this token; a newer replay or live run moves it on
  await run(pb);
  if (clock.token === token) $("play").textContent = "Play";
}

async function select(id) {
  if (!/^[A-Za-z0-9_-]+$/.test(id)) return;
  for (const [key, b] of Object.entries(buttons)) b.setAttribute("aria-pressed", String(key === id));
  current = await (await fetch(`sessions/${id}.json`)).json();
  $("play").disabled = $("restart").disabled = false;
  begin(current);
}

export function wireControls() {
  $("play").addEventListener("click", () => {
    if (!current) return;
    if ($("play").textContent === "Play") return begin(current);
    $("play").textContent = togglePause() ? "Resume" : "Pause";
  });
  $("restart").addEventListener("click", () => current && begin(current));
  $("speed").addEventListener("change", (e) => { clock.speed = Number(e.target.value) || 1; });
}

/** Stop playback when a live run takes over the stage. */
export function leaveSession() {
  halt();
  current = null;
  $("play").textContent = "Play";
  $("play").disabled = $("restart").disabled = true;
  for (const b of Object.values(buttons)) b.setAttribute("aria-pressed", "false");
}

export async function loadList() {
  const list = await (await fetch("sessions/index.json", { cache: "no-store" })).json();
  $("session-list").replaceChildren(...list.map((s) => {
    const verdicts = Object.values(s.summary || {});
    const title = s.highlight || `${verdicts.filter(isProven).length} of ${verdicts.length} metrics proven`;
    const b = el("button", { type: "button", "aria-pressed": "false" },
      el("span", { class: "name" }, cap(title)), el("span", { class: "device" }, s.device),
      el("span", { class: "kind" }, s.kind === "health" ? "Health monitoring" : "Onboarding"));
    b.addEventListener("click", () => select(s.id));
    buttons[s.id] = b;
    return el("li", null, b);
  }));
  return buttons;
}

/** Play a session by id (hero buttons) and bring the stage into view. */
export function open(id) {
  if (!buttons[id]) return;
  select(id);
  $("stage").scrollIntoView({ behavior: "smooth" });
}

export const has = (id) => id in buttons;
