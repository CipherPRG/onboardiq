// "One device, start to finish": six scenes scrubbed by scroll, built from two real recordings
// (an onboarding run with a corrected pick, and a health run under a stress test).
import { $, el } from "./util.js";
import { traceChart, scoreChart } from "./chart.js";
import { verdictFor, LEVEL_TONE } from "./model.js";

const ease = (t) => Math.min(1, Math.max(0, t));

/** Reveal a drawn trace chart up to fraction t of its points. */
function scrubber(series, unit, tone) {
  const chart = traceChart(series, unit, tone);
  chart.draw(true);
  const line = chart.fig.querySelector(".line");
  const dots = [...chart.fig.querySelectorAll("circle")];
  const bands = chart.fig.querySelectorAll(".band, .band-label");
  const baseShare = series.base.length / (series.base.length + series.load.length);
  let length = 0;
  return {
    fig: chart.fig,
    baseShare,
    show(t) {
      if (!length) { length = line.getTotalLength() || 1; line.style.strokeDasharray = `${length}`; }
      line.style.strokeDashoffset = `${length * (1 - t)}`;
      dots.forEach((d, i) => { d.style.opacity = i / Math.max(1, dots.length - 1) <= t ? "1" : "0"; });
      bands.forEach((b) => b.classList.toggle("on", t >= baseShare));
    },
  };
}

function rateOf(ev) {
  const detail = ((ev.evidence || []).find((c) => c.check === "experiment") || {}).detail || "";
  const m = detail.match(/([\d.]+\/s) -> ([\d.]+\/s)/);
  return m ? `${m[1]} → ${m[2]}` : "";
}

function testScene(ev, kicker, lead) {
  const [title, detail, tone] = verdictFor(ev.verdict, ev.evidence);
  const chart = scrubber(ev.series, (ev.pick || {}).canonical_unit, tone);
  const bar = el("span");
  const verdict = el("div", { class: "verdict" },
    el("p", { class: `stamp ${tone}` }, el("strong", null, title), el("span", null, detail)),
    el("p", { class: `rate tone-${tone}` }, rateOf(ev)));
  const node = el("div", { class: "sc" }, el("div", { class: "card" },
    el("p", { class: "kicker" }, kicker), lead,
    el("h3", { class: "mono" }, ev.object), chart.fig,
    el("div", { class: "meter" }, "CPU load", el("span", { class: "bar" }, bar), el("span", null, "all cores")),
    verdict));
  return {
    node,
    update(t) {
      chart.show(ease(t / 0.85));
      bar.parentElement.style.setProperty("--load", t / 0.85 >= chart.baseShare ? "1" : "0");
      verdict.classList.toggle("show", t >= 0.88);
    },
  };
}

function scenes(run, health) {
  const verifies = run.events.filter((e) => e.kind === "verify" && e.series);
  const bad = verifies.find((e) => e.verdict === "rejected");
  const good = verifies.find((e) => e.family === bad.family && e.attempt > bad.attempt && e.verdict.startsWith("proven"));
  const first = run.events.find((e) => e.kind === "verify-start" && e.family === bad.family);
  const repick = run.events.find((e) => e.kind === "repick" && e.family === bad.family);
  const modules = run.modules.split(",");

  // 0: ask the device
  const out = el("span");
  const meta = el("p", { class: "meta" }, "read-only · UDP 161 · nothing written to the device");
  const ask = el("div", { class: "sc" }, el("div", { class: "card term" },
    el("p", { class: "cmd" }, el("b", null, "$ "), "snmpget <device> sysDescr.0"), el("p", { class: "out" }, out), meta));

  // 1: read the manual
  const count = el("span");
  const pages = modules.map((m, i) => {
    const page = el("div", { class: "page" }, el("b", null, m),
      "OBJECT-TYPE", el("br"), "  SYNTAX  Counter32", el("br"), "  MAX-ACCESS  read-only", el("br"), "  DESCRIPTION", el("br"), "    \"…\"");
    page.style.setProperty("--i", i);
    return page;
  });
  const stack = el("div", { class: "stack" }, ...pages);
  const read = el("div", { class: "sc" }, stack,
    el("p", { class: "count" }, count, el("small", null, `objects in ${modules.length} MIB files`)));

  // 2: the pick
  const p = first.pick || {};
  const pick = el("div", { class: "sc" }, el("div", { class: "card pick" },
    el("p", { class: "kicker" }, `${run.model} · CPU`), el("p", { class: "obj" }, p.object_name || first.object),
    el("p", { class: "oid" }, p.oid || first.oid), p.evidence ? el("blockquote", null, p.evidence) : null,
    el("p", { class: "chip" }, "Prediction: ", el("b", null, `${p.under_load || "rise"} under load`))));

  // 3, 4: the test, then the correction
  const test = testScene(bad, "Attempt 1 · measured on the device");
  const why = String(repick?.reasoning || "").split(/(?<=\.)\s/).slice(0, 2).join(" ").replaceAll("->", "→");
  const fix = testScene(good, "Attempt 2 · after re-reading the manual", el("p", { class: "reason" }, why));

  // 5: the health score
  const samples = health.samples;
  const chart = scoreChart(health.interval);
  const num = el("span", { class: "score-num" });
  const head = el("p", { class: "score-head" });
  const score = el("div", { class: "sc" }, el("div", { class: "card" },
    el("p", { class: "kicker" }, `${health.device} · polled every ${health.interval} s`),
    el("div", { class: "score-top" }, num, head), el("figure", { class: "chart score-chart" }, chart.node)));

  return [
    { node: ask, update: (t) => { const s = run.device; out.textContent = s.slice(0, Math.round(s.length * ease(t * 1.6))); meta.style.opacity = t > 0.6 ? "1" : "0"; } },
    { node: read, update: (t) => { stack.style.setProperty("--t", ease(t * 1.3)); count.textContent = Math.round(run.objects * ease(t * 1.3)); } },
    { node: pick, update: () => {} },
    test, fix,
    { node: score, update: (t) => {
      const i = Math.round(ease(t / 0.9) * (samples.length - 1));
      const x = samples[i];
      chart.render(samples.slice(0, i + 1), samples.length);
      num.textContent = x.score;
      num.className = `score-num ${LEVEL_TONE[x.level] || ""}`;
      const seen = samples.slice(0, i + 1);
      const told = [...seen].reverse().find((s) => s.explanation?.headline);
      head.textContent = x.level !== "ok" && told ? told.explanation.headline
        : seen.some((s) => s.level !== "ok") ? "Recovered: every proven reading is healthy again" : "Every proven reading is healthy";
    } },
  ];
}

export function mountStory(run, health) {
  const story = $("story");
  const steps = [...$("steps").children];
  const copy = [...$("copy").children];
  const list = scenes(run, health);
  $("scene").append(...list.map((s) => s.node));
  let current = -1;
  let queued = false;

  function frame() {
    queued = false;
    const total = story.offsetHeight - innerHeight;
    const p = Math.min(0.99999, Math.max(0, -story.getBoundingClientRect().top / total));
    const idx = Math.floor(p * list.length);
    const t = p * list.length - idx;
    steps.forEach((li, i) => {
      li.classList.toggle("on", i === idx);
      li.style.setProperty("--p", i < idx ? 1 : i === idx ? t : 0);
    });
    if (idx !== current) {
      copy.forEach((c, i) => { c.hidden = i !== idx; });
      list.forEach((s, i) => s.node.classList.toggle("on", i === idx));
      current = idx;
    }
    list[idx].update(t);
  }
  const queue = () => { if (!queued) { queued = true; requestAnimationFrame(frame); } };
  addEventListener("scroll", queue, { passive: true });
  addEventListener("resize", queue);
  frame();
}
