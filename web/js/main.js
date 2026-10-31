// Page start-up: which mode are we in (static replay, hosted demo, or local server), then wire the matching parts.
import { $ } from "./util.js";
import { mountSnmpAuth } from "./snmp-auth.js";
import { loadList, wireControls, open, has } from "./sessions.js";
import { mountRig } from "./hero.js";
import { mountStory } from "./story.js";
import { wireSandbox, wireLocal } from "./live.js";
import { wireMonitor } from "./monitor.js";

const STORY = ["laptop_full", "laptop_stress"];   // the onboarding run and the health run the story is built from

const LEDE = {
  sandbox: "Muse Spark reads a device's MIB files and picks what to monitor. Every pick is tested on the device under " +
    "real load, and a wrong one goes back to the model with the evidence. Watch a recording, or run it live below.",
  local: "Muse Spark reads a device's MIB files and picks what to monitor. Every pick is tested on the device under real " +
    "load. This page is connected to your local server, so you can onboard and monitor a device from here.",
};

const json = (id) => fetch(`sessions/${id}.json`).then((r) => r.json());

async function serverStatus() {
  try {
    const r = await fetch("/api/status", { cache: "no-store" });
    return r.ok ? await r.json() : null;
  } catch { return null; }   // static hosting: replays only
}

async function main() {
  mountRig();
  document.querySelectorAll("[data-snmp-auth]").forEach(mountSnmpAuth);
  wireControls();
  const refresh = () => loadList().catch(() => {});
  Promise.all(STORY.map(json)).then(([run, health]) => mountStory(run, health)).catch(() => { $("story").hidden = true; });
  await loadList();
  document.querySelectorAll("[data-start]").forEach((b) => {
    const id = b.dataset.start;
    if (has(id)) b.addEventListener("click", () => open(id)); else b.hidden = true;
  });

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
