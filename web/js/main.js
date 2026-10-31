// Page start-up: which mode are we in (static replay, hosted demo, or local server), then wire the matching parts.
import { $ } from "./util.js";
import { mountSnmpAuth } from "./snmp-auth.js";
import { loadList, wireControls, open, has } from "./sessions.js";
import { renderEvidence } from "./hero.js";
import { wireSandbox, wireLocal } from "./live.js";
import { wireMonitor } from "./monitor.js";

const LEDE = {
  sandbox: "Play recorded sessions, or run the real agent live on a demo device. In a replay, every reading and model answer " +
    "comes from the recording; in a live run they come from the device and the model.",
  local: "Replay recorded sessions, or onboard a device live from this machine. In a replay, every reading and model answer " +
    "comes from the recording; in a live run they come from the device and the model.",
};

async function serverStatus() {
  try {
    const r = await fetch("/api/status", { cache: "no-store" });
    return r.ok ? await r.json() : null;
  } catch { return null; }   // static hosting: replays only
}

async function main() {
  document.querySelectorAll("[data-snmp-auth]").forEach(mountSnmpAuth);
  wireControls();
  const refresh = () => loadList().catch(() => {});
  await loadList();

  document.querySelectorAll("[data-start]").forEach((b) => {
    const id = b.dataset.start;
    if (has(id)) b.addEventListener("click", () => open(id)); else b.hidden = true;
  });

  const hero = document.querySelector("[data-hero]").dataset.hero;
  fetch(`sessions/${hero}.json`).then((r) => r.json()).then(renderEvidence).catch(() => {});

  const status = await serverStatus();
  if (!status?.live) return;
  if (status.sandbox) {
    wireSandbox(status, refresh);
    $("lede").textContent = LEDE.sandbox;
  } else {
    wireLocal(refresh);
    wireMonitor(refresh);
    $("lede").textContent = LEDE.local;
  }
}

main();
