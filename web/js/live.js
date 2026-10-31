// Live onboarding runs: the hosted demo (fixed target) and the local server (any device you point it at).
import { $, el } from "./util.js";
import { halt } from "./clock.js";
import { ui } from "./ui.js";
import { post, stream } from "./api.js";
import { onboardingRun } from "./onboarding.js";
import { snmpFields } from "./snmp-auth.js";
import { loadOnboarded } from "./monitor.js";
import { wireMibUpload } from "./mib-upload.js";

let runId = null;

// The agent sometimes needs a person to start or stop CPU load on a device it cannot load itself.
function showPrompt(ev) {
  const text = $("prompt-text"), cmd = $("prompt-cmd");
  $("prompt").hidden = false;
  if (/^STOP/i.test(ev.text)) { text.textContent = ev.text; cmd.textContent = ""; }
  else if (/^START/i.test(ev.text)) text.textContent = ev.text;
  else cmd.textContent = ev.text;
}

function waitForPerson() {
  const ok = $("prompt-ok");
  ok.hidden = false;
  ok.textContent = /^STOP/i.test($("prompt-text").textContent) ? "The load has stopped" : "The load is running";
}

/** Start a run and follow it. `button` is disabled while it runs; failures show in `errEl`. */
export async function startRun(body, button, errEl, onSaved) {
  errEl.textContent = "";
  halt();
  button.disabled = true;
  try {
    const { id } = await post("/api/runs", body);
    runId = id;
    const run = onboardingRun();
    stream(id, (ev) => {
      if (ev.kind === "prompt") return showPrompt(ev);
      if (ev.kind === "prompt-wait") return waitForPerson();
      if (ev.kind === "end" || ev.kind === "error") $("prompt").hidden = true;
      run.handle(ev, false);
      if (ev.kind === "end") {
        if (ev.saved) onSaved();
        if (ev.onboarded) loadOnboarded(ev.onboarded).catch(() => {});
      }
    }, () => { button.disabled = false; });
    $("device").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    // the hosted demo can be busy, cooling down, out of budget or down: the recordings always work
    errEl.textContent = `${e.message} You can still play a recorded session above.`;
    button.disabled = false;
  }
}

/** The hosted demo: the server ignores the request body and runs its fixed device. */
export function wireSandbox(status, onSaved) {
  $("sandbox").hidden = false;
  $("to-live").hidden = false;
  $("sb-model").textContent = status.model || "Muse Spark";
  $("sb-left").textContent = status.paused
    ? "The live demo is paused by its owner. The recorded sessions still play."
    : `Runs left on this server for now: ${status.runs_left}. Each visitor gets ${status.per_visitor} a day. Pause between runs: ${status.cooldown_s} s.`;
  $("sb-run").disabled = Boolean(status.paused);
  $("sb-run").addEventListener("click", () => startRun({}, $("sb-run"), $("sb-error"), onSaved));
}

function wireModuleSuggestions(input) {
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const last = input.value.split(",").pop().trim();
      const before = input.value.slice(0, input.value.lastIndexOf(",") + 1);
      if (last.length < 2) return;
      const mods = await (await fetch(`/api/modules?q=${encodeURIComponent(last)}`)).json();
      $("module-list").replaceChildren(...mods.map((m) => el("option", { value: before + m })));
    }, 200);
  });
}

/** The local server: onboard any device you point it at. */
export function wireLocal(onSaved) {
  $("live").hidden = false;
  const form = $("live-form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const mode = fd.get("mode");
    startRun({
      device: fd.get("device"), modules: String(fd.get("modules") || "").split(",").map((m) => m.trim()).filter(Boolean),
      host: fd.get("host"), port: fd.get("port") ? Number(fd.get("port")) : null, ...snmpFields(fd),
      families: fd.getAll("families"), lab: mode === "lab", load_by_hand: mode === "hand" ? fd.get("hand") : null, record: true,
    }, $("run"), $("live-error"), onSaved);
  });
  $("prompt-ok").addEventListener("click", async () => {
    $("prompt-ok").hidden = true;
    try { await post(`/api/runs/${runId}/confirm`, {}); } catch (e) { ui.status(e.message); }
  });
  wireModuleSuggestions(form.elements.modules);
  wireMibUpload(form);
}
