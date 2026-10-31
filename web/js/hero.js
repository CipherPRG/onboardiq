// The first screen: one recorded correction, drawn as it happened. A wrong pick next to the pick that replaced it.
import { $, el, REDUCED } from "./util.js";
import { traceChart } from "./chart.js";
import { verdictFor } from "./model.js";

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, REDUCED ? 0 : ms));

/** The first verify that was rejected and the verify of the same metric that was proven after it. */
function correction(events) {
  const verifies = events.filter((e) => e.kind === "verify" && e.series);
  for (const bad of verifies.filter((e) => e.verdict === "rejected")) {
    const good = verifies.find((e) => e.family === bad.family && e.attempt > bad.attempt && e.verdict.startsWith("proven"));
    if (good) return [bad, good];
  }
  return null;
}

function proof(ev) {
  const [title, , tone] = verdictFor(ev.verdict, ev.evidence);
  const chart = traceChart(ev.series, (ev.pick || {}).canonical_unit, tone);
  const test = (ev.evidence || []).find((c) => c.check === "experiment");
  const node = el("article", { class: "proof" },
    el("header", null, el("h3", null, `Attempt ${ev.attempt}: ${ev.object}`),
      el("p", { class: `stamp ${tone}` }, el("strong", null, title))),
    chart.fig, test ? el("p", { class: "caption" }, test.detail) : null);
  return { node, chart };
}

// The panels lean toward the pointer, a little: depth without any library.
function tilt(node) {
  if (REDUCED) return;
  node.addEventListener("pointermove", (e) => {
    const r = node.getBoundingClientRect();
    node.style.setProperty("--ry", `${(((e.clientX - r.left) / r.width) - 0.5) * 5}deg`);
    node.style.setProperty("--rx", `${-(((e.clientY - r.top) / r.height) - 0.5) * 5}deg`);
  });
  node.addEventListener("pointerleave", () => { node.style.removeProperty("--rx"); node.style.removeProperty("--ry"); });
}

/** The five stages of one real recorded correction, as numbers from the session itself. */
function pipeline(session, [bad, good]) {
  const test = (ev) => ((ev.evidence || []).find((c) => c.check === "experiment") || {}).detail || "";
  const rate = /counter rate ([\d.]+\/s -> [\d.]+\/s)/;
  const steps = [
    ["Device", session.device.split(" running")[0], "answers SNMP queries", ""],
    ["Read", `${session.objects} objects`, `from ${session.modules.split(",").length} MIB files`, ""],
    ["Pick", bad.object, `${session.model} chooses it for ${bad.family.toUpperCase()}`, ""],
    ["Test", (test(bad).match(rate) || [, "failed"])[1].replace("->", "→"), "under real CPU load", "bad"],
    ["Correct", good.object, "the model tries again; proven", "ok"],
  ];
  $("pipe").replaceChildren(...steps.map(([step, title, detail, tone], i) => {
    const li = el("li", { class: tone }, el("span", { class: "step" }, step), el("strong", null, title), el("span", { class: "detail" }, detail));
    li.style.setProperty("--i", i);
    return li;
  }));
}

export async function renderEvidence(session) {
  const pair = correction(session.events);
  const root = $("evidence");
  if (!pair) return;
  pipeline(session, pair);
  const [bad, good] = pair.map(proof);
  root.append(bad.node, good.node,
    el("p", { class: "evidence-note" }, `Recorded on ${session.device}. The model's first CPU pick looked right in the manual. ` +
      "Under load it did not move the way the model predicted, so the agent sent it back."));
  [bad.node, good.node].forEach(tilt);
  await bad.chart.draw(false, wait);
  await good.chart.draw(false, wait);
}
