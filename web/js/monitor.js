// Live health monitor (local server only): poll an onboarded device and score it as it runs.
import { $, el, plural } from "./util.js";
import { halt } from "./clock.js";
import { ui } from "./ui.js";
import { post, stream } from "./api.js";
import { healthView, liveSample } from "./health.js";
import { snmpFields } from "./snmp-auth.js";

const WINDOW = 90;
const state = { id: null, view: null, samples: [], loadUntil: 0 };

export async function loadOnboarded(select) {
  const list = await (await fetch("/api/onboarded", { cache: "no-store" })).json();
  const box = $("onboarded");
  box.replaceChildren(...list.map((o) => {
    const proven = Object.values(o.summary).filter((v) => String(v).startsWith("proven")).length;
    const opt = el("option", { value: o.name, "data-host": o.host || "localhost", "data-port": o.port || "" },
      `${o.device || o.modules} · ${proven} of ${Object.keys(o.summary).length} proven · ${o.name.slice(8)}`);
    return opt;
  }));
  if (!list.length) box.append(el("option", { value: "" }, "No onboarded devices yet: onboard one above first"));
  if (select) box.value = select;
  box.dispatchEvent(new Event("change"));
}

function buttons(running, local) {
  $("mon-start").disabled = running;
  $("mon-stop").hidden = !running;
  $("mon-load").hidden = !(running && local);
  $("run").disabled = running;
}

function showAlert(a) {
  const box = $("alerts") || ui.host().appendChild(el("div", { id: "alerts", class: "h-alerts" }));
  const link = a.file && /^alert-\d{8}-\d{6}\.html$/.test(a.file)
    ? el("a", { href: `/api/outbox/${a.file}`, target: "_blank", rel: "noopener" }, "open the email") : null;
  box.prepend(el("p", { class: "h-alert appear" },
    el("strong", null, a.via === "smtp" ? "Email sent: " : "Alert saved to the outbox: "), `${a.subject} `, link));
}

function onEvent(ev, onSaved) {
  switch (ev.kind) {
    case "monitor-start":
      ui.device(`${ev.device} · live`);
      state.view = healthView({ policy: ev.policy, interval: ev.interval, live: true, explain: ev.explain });
      buttons(true, ev.local);
      ui.status("Monitoring live. First reading in a moment…");
      break;
    case "health": {
      const x = liveSample(ev);
      x.load = Date.now() < state.loadUntil;
      state.samples.push(x);
      const win = state.samples.slice(-WINDOW);
      state.view.chart.render(win, Math.max(30, win.length), state.samples.length - win.length);
      state.view.show(x);
      if (state.samples.length === 1) state.view.notScored(ev.excluded || []);
      ui.status(`Live: ${plural(state.samples.length, "reading")}${x.load ? " · CPU load running" : ""}${x.explanation ? " · the model explained this change" : ""}`);
      if (x.alert) showAlert(x.alert);
      break;
    }
    case "load":
      state.loadUntil = Date.now() + ev.seconds * 1000;
      ui.status(`CPU load started on this machine for ${ev.seconds} s. Watch the score.`);
      break;
    case "poll-error":
      ui.status(`A poll failed: ${ev.message} (still monitoring)`);
      break;
    case "end":
    case "error":
      buttons(false, false);
      state.loadUntil = 0;
      ui.status(ev.kind === "error" ? `Monitoring stopped: ${ev.message}`
        : `Monitoring ${ev.reason} after ${plural(state.samples.length, "reading")}${ev.saved ? ". Saved to the session list as a replay." : "."}`);
      if (ev.saved) onSaved();
      break;
  }
}

async function start(form, onSaved) {
  const fd = new FormData(form);
  const err = $("mon-error");
  err.textContent = "";
  halt();
  buttons(true, false);
  try {
    const { id } = await post("/api/monitor", {
      onboarded: fd.get("onboarded"), host: fd.get("host"), port: fd.get("port") ? Number(fd.get("port")) : null,
      ...snmpFields(fd), interval: Number(fd.get("interval")) || 10,
      explain: fd.get("explain") === "on", record: fd.get("record") === "on", alert: fd.get("alert") === "on",
    });
    Object.assign(state, { id, samples: [], loadUntil: 0 });
    stream(id, (ev) => onEvent(ev, onSaved), () => buttons(false, false));
  } catch (e) {
    err.textContent = e.message;
    buttons(false, false);
  }
}

export function wireMonitor(onSaved) {
  $("monitor").hidden = false;
  const form = $("monitor-form");
  const act = (path, body) => post(path, body).catch((e) => { $("mon-error").textContent = e.message; });
  $("onboarded").addEventListener("change", (e) => {
    const o = e.target.selectedOptions[0];
    if (!o || !o.value) return;
    form.elements.host.value = o.dataset.host;
    form.elements.port.value = o.dataset.port;
  });
  form.addEventListener("submit", (e) => { e.preventDefault(); start(form, onSaved); });
  $("mon-stop").addEventListener("click", () => { ui.status("Stopping…"); act(`/api/runs/${state.id}/stop`, {}); });
  $("mon-load").addEventListener("click", () => act(`/api/runs/${state.id}/load`, { seconds: 120 }));
  loadOnboarded();
}
