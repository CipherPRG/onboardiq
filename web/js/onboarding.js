// An onboarding run, replayed or live: the model's pick, the measurements as they were taken, the verdict,
// and the re-read when a pick fails. One handler serves both; `animate` only decides whether to pace it.
import { $, el } from "./util.js";
import { sleep, halt, guarded } from "./clock.js";
import { ui } from "./ui.js";
import { traceChart } from "./chart.js";
import { FAMILY, verdictFor, outcome } from "./model.js";

const KEY_CHECK = /experiment|witness/;

function claim(ev) {
  const p = ev.pick || {};
  const scale = p.scale_to_canonical;
  const unit = p.canonical_unit;
  const predicts = (ev.family === "cpu" || ev.family === "memory") && p.under_load && p.under_load !== "none";
  const waiting = el("p", { class: "waiting" }, "Testing on the device…");
  const test = el("div", { class: "test" }, waiting);
  const card = el("div", { class: "attempt appear" },
    el("div", { class: "claim" },
      el("p", { class: "n" }, `Attempt ${ev.attempt}`),
      el("h4", null, p.object_name || ev.object),
      el("p", { class: "mono" }, p.oid || ev.oid),
      scale !== null && scale !== undefined && scale !== 1 && unit && unit !== "none"
        ? el("p", null, `Scaled ×${Number(scale)} to ${unit}.`) : null,
      predicts ? el("p", null, `The model predicted it should ${p.under_load} under load.`) : null,
      p.evidence ? el("blockquote", null, p.evidence) : null),
    test);
  return { card, test, waiting };
}

function checkList(evidence) {
  return el("ul", { class: "checks" }, ...evidence.map((c) => {
    const state = c.ok ? "pass" : (c.ok === false ? "fail" : "info");
    return el("li", { class: `${state}${KEY_CHECK.test(c.check) ? " key" : ""}` },
      el("span", { class: "mark", "aria-hidden": "true" }, c.ok ? "✓" : (c.ok === false ? "✗" : "·")),
      el("span", { class: "what" }, c.check), el("span", null, c.detail));
  }));
}

/** Builds the run view in #run-host and returns its event handler. */
export function onboardingRun() {
  ui.clear();
  const meta = {};
  const events = [];
  const fams = {};
  const strip = $("strip");
  strip.replaceChildren();
  strip.hidden = false;
  const host = el("div", { class: "families" });
  ui.host().append(host);

  const slots = {};
  function slot(id, label, href) {
    if (slots[id]) return slots[id];
    const text = el("span");
    const a = el("a", { class: "slot", href }, el("strong", null, label), text);
    strip.append(el("li", null, a));
    return (slots[id] = { set: (state, words) => { a.dataset.state = state; text.textContent = words; } });
  }
  slot("read", "Read", "#stage").set("active", "Reading");

  function family(id) {
    if (fams[id]) return fams[id];
    const final = el("p", { class: "verdict-line" });
    const sec = el("section", { class: "family appear", id: `fam-${id}` }, el("header", null, el("h3", null, FAMILY[id] || id), final));
    host.append(sec);
    return (fams[id] = { sec, final, current: null, slot: slot(id, FAMILY[id] || id, `#fam-${id}`) });
  }

  // A chart redrawn as each live measurement arrives; replaced by the verified chart at the verdict.
  function liveTrace(pick) {
    const scale = pick.scale_to_canonical ?? 1;
    const counter = pick.value_type === "counter";
    const samples = [];
    const box = el("div");
    const rate = (row, prev, t) => {
      let total = 0;
      for (const [r, v] of Object.entries(row)) if (prev.rows[r] !== undefined && v >= prev.rows[r]) total += v - prev.rows[r];
      return total / Math.max(1e-6, t - prev.t);
    };
    // gauges: follow the row that has moved most so far (the one the verifier will judge)
    const movingRow = (row) => {
      let best = null, spread = -1;
      for (const r of Object.keys(row)) {
        if (row[r] === null) continue;
        const vs = samples.map((s) => s.rows[r]).filter((v) => typeof v === "number");
        const sp = vs.length ? Math.max(...vs) - Math.min(...vs) : 0;
        if (sp > spread) { spread = sp; best = r; }
      }
      return best;
    };
    return {
      box,
      add(sample) {
        samples.push({ t: sample.t, phase: sample.phase, rows: sample.rows });
        const series = { base: [], load: [], counter, rows: Object.keys(sample.rows).length };
        samples.forEach((s, i) => {
          let v = null;
          if (counter) v = i ? rate(s.rows, samples[i - 1], s.t) : null;
          else { const r = movingRow(s.rows); v = r === null ? null : s.rows[r] * scale; }
          if (v === null || Number.isNaN(v)) return;
          (s.phase === "load" ? series.load : series.base).push([Math.round((s.t - samples[0].t) * 10) / 10, v]);
        });
        if (!series.base.length && !series.load.length) return;
        const chart = traceChart(series, pick.canonical_unit);
        chart.draw(true);
        box.replaceChildren(chart.fig, el("p", { class: "waiting" },
          sample.phase === "load" ? "Under load…" : (sample.phase === "base" ? "Baseline…" : "Observing…")));
      },
    };
  }

  async function verified(ev, f, animate) {
    const [title, detail, tone] = verdictFor(ev.verdict, ev.evidence);
    const { test, waiting } = f.current;
    waiting.remove();
    test.replaceChildren();
    if (ev.series && (ev.series.base.length || ev.series.load.length)) {
      const chart = traceChart(ev.series, (ev.pick || {}).canonical_unit, tone);
      test.append(chart.fig);
      if (animate) ui.status(`Measuring ${ev.object}: baseline, then under load…`);
      await chart.draw(!animate);
    } else if (animate) {
      await sleep(600);
    }
    test.append(el("p", { class: `stamp appear ${tone}` }, el("strong", null, title), el("span", null, detail)),
      checkList(ev.evidence || []));
    f.slot.set(tone, title);
    if (animate) await sleep(1400);
  }

  async function handle(ev, animate) {
    const pace = animate ? sleep : () => {};
    switch (ev.kind) {
      case "start":
        Object.assign(meta, { model: ev.model, objects: ev.objects, modules: ev.modules });
        ui.device(ev.device);
        ui.status(`${ev.model || "The model"} is reading ${ev.objects} MIB objects (${ev.modules})…`);
        break;
      case "read":
        slot("read").set("ok", "Picks made");
        if (animate) ui.status(`${meta.model || "The model"} is reading ${meta.objects} MIB objects (${meta.modules})…`);
        else ui.status("Picks made. Testing them on the device…");
        await pace(1800);
        break;
      case "verify-start": {
        const f = family(ev.family);
        f.current = claim(ev);
        f.sec.append(f.current.card);
        f.slot.set("active", "Testing");
        if (!animate) {
          const trace = liveTrace(ev.pick || {});
          f.current.waiting.replaceWith(trace.box);
          f.current.waiting = trace.box;
          f.trace = trace;
        }
        ui.status(`Testing ${FAMILY[ev.family] || ev.family}: ${ev.object}`);
        await pace(1500);
        break;
      }
      case "sample":
        fams[ev.family]?.trace?.add(ev);
        break;
      case "verify":
        events.push(ev);
        await verified(ev, family(ev.family), animate);
        break;
      case "repick": {
        const f = family(ev.family);
        const text = document.createTextNode("");
        f.sec.append(el("div", { class: "reread appear" }, el("p", null, el("strong", null, "Re-read the manual with this evidence. "), text)));
        ui.status("The model is re-reading the manual with the evidence…");
        const words = String(ev.reasoning || "").split(/(\s+)/);
        if (!animate) { text.textContent = words.join(""); break; }
        const lead = words.slice(0, 120);
        for (let i = 0; i < lead.length; i += 6) { text.textContent = lead.slice(0, i + 6).join(""); await sleep(90); }
        if (words.length > lead.length) text.textContent += " …";
        await sleep(900);
        break;
      }
      case "no-pick":
        family(ev.family).sec.append(el("p", { class: "waiting appear" }, "The model found no object for this in the manual."));
        await pace(900);
        break;
      case "final": {
        const f = family(ev.family);
        const [title, detail, tone] = verdictFor(ev.verdict);
        f.final.className = `verdict-line appear tone-${tone}`;
        f.final.replaceChildren(`${title} `, el("span", null, detail + (ev.attempts > 1 ? ` after ${ev.attempts} attempts` : "")));
        f.slot.set(tone, title);
        await pace(600);
        break;
      }
      case "done":
        ui.status("Replay finished.");
        host.append(el("p", { class: "done appear" }, outcome(ev.summary || meta.summary, events)));
        break;
      case "end":
        ui.status(`Finished: ${outcome(ev.summary, events)} ${ev.seconds} s, ${ev.snmp_calls} SNMP queries, ` +
          `model cost $${Number(ev.cost_usd).toFixed(3)}${ev.saved ? ". Saved to the session list." : "."}`);
        host.append(el("p", { class: "done appear" }, outcome(ev.summary, events)));
        break;
      case "error":
        ui.status(`The run stopped: ${ev.message}`);
        break;
    }
  }
  return { handle, meta };
}

export function playSession(pb) {
  halt();
  ui.device(pb.device);
  const run = onboardingRun();
  Object.assign(run.meta, { model: pb.model, objects: pb.objects, modules: pb.modules, summary: pb.summary });
  return guarded(async () => {
    for (const ev of pb.events) await run.handle(ev, true);
  });
}
