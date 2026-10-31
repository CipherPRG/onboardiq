// The landing page: the hero device and the scroll story. The console (console/) is the working tool.
import { $ } from "./util.js";
import { mountRig } from "./hero.js";
import { mountStory } from "./story.js";

const STORY = ["laptop_full", "laptop_stress"];   // the onboarding run and the health run the story is built from

const CTA = {
  sandbox: "The console here can run the real agent live on a demo device: Muse Spark reads the MIB files, the device " +
    "tests every pick under load. Recordings of other runs play there too.",
  local: "Connected to your local server: onboard any device you can reach with your own MIB files, monitor it live, " +
    "and export every event as JSON.",
};

const json = (id) => fetch(`sessions/${id}.json`).then((r) => r.json());

async function main() {
  mountRig();
  Promise.all(STORY.map(json)).then(([run, health]) => mountStory(run, health)).catch(() => { $("story").hidden = true; });
  try {
    const r = await fetch("/api/status", { cache: "no-store" });
    const status = r.ok ? await r.json() : null;
    if (status?.live) $("cta-text").textContent = CTA[status.sandbox ? "sandbox" : "local"];
  } catch { /* static hosting: recordings only */ }
}

main();
