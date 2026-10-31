// Health monitoring: a score per poll, per-metric bars, and the model's plain-English explanation.
// Used for recorded health sessions and for the live monitor.
import { $, el } from "./util.js";
import { sleep, halt, guarded } from "./clock.js";
import { ui } from "./ui.js";
import { scoreChart } from "./chart.js";
import { HEALTH_FAMILY as NAMES, LEVEL_TONE } from "./model.js";

const clockOf = (x) => (x.at || "").slice(11, 19);
const barTone = (score) => (score >= 75 ? "ok" : score >= 40 ? "warn" : "bad");
const rows = (n) => `${n} row${n > 1 ? "s" : ""}`;

function familyRow(key, f) {
  const tone = barTone(f.score);
  const fill = el("span", { class: `h-fill ${tone}` });
  fill.style.width = `${Math.max(2, f.score)}%`;
  const why = (f.rows > 1 ? `worst of ${f.rows}: ${f.worst} · ` : "") + f.why +
    (f.flagged ? ` · ${rows(f.flagged)} flagged` : "") + (f.partial ? " · some rows skipped" : "");
  return el("div", { class: "h-fam" }, el("span", { class: "h-name" }, NAMES[key] || key), el("span", { class: "h-bar" }, fill),
    el("span", { class: `h-num ${tone}` }, Math.round(f.score)), el("span", { class: "h-why" }, why));
}

function explanation(x) {
  const e = x.explanation;
  const rejected = e.note && e.note.includes("rejected: ") ? e.note.split("rejected: ")[1] : null;
  const source = e.source === "template"
    ? `Written from the measured readings (template)${rejected ? `: the model's answer failed the check (${rejected})` : ""}`
    : `Written by ${e.source}. Every number is checked against the readings.`;
  const foot = el("p", { class: "h-src" }, `${clockOf(x)} UTC · ${source}`);
  if (!e.headline) return [el("p", { class: "h-etext appear" }, e.text), foot];
  return [el("div", { class: "h-card appear" },
    el("p", { class: `h-head ${LEVEL_TONE[x.level] || ""}` }, e.headline),
    el("p", { class: "h-line" }, e.what_happened), el("p", { class: "h-line" }, e.why),
    e.check ? el("p", { class: "h-check" }, el("strong", null, "What to check: "), e.check) : null), foot];
}

function alertLine(a) {
  const link = a.file && /^alert-\d{8}-\d{6}\.html$/.test(a.file)
    ? el("a", { href: `/api/outbox/${a.file}`, target: "_blank", rel: "noopener" }, a.subject) : a.subject;
  return el("p", { class: "h-alert appear" }, a.via === "smtp" ? "Alert emailed: " : "Alert written to the outbox: ", link);
}

/** Builds the health view in #run-host. Returns {chart, show(sample), notScored(list)}. */
export function healthView(pb) {
  ui.clear();
  $("strip").hidden = true;
  const score = el("p", { class: "h-score" }, "–");
  const level = el("p", { class: "h-level" });
  const summary = el("p", { class: "h-sum" }, "Waiting for the first reading…");
  const chart = scoreChart(pb.interval);
  const fams = el("div", { class: "h-fams" });
  const anomalies = el("ul", { class: "h-anom" });
  const expl = el("div", { class: "h-expl" }, el("p", { class: "h-src" }, pb.explain === false
    ? "Explanations are off for this run." : "The model explains here when the level changes or something unusual appears."));
  const skipped = el("details", { class: "h-not" });
  const recomputed = pb.recomputed
    ? ` Recomputed from ${pb.recomputed.snmp_responses} raw SNMP responses (${pb.recomputed.polls} polls): the score is calculated now, not stored.` : "";
  ui.host().append(el("div", { class: "health appear" },
    el("div", { class: "h-top" }, el("div", null, score, level),
      el("div", null, summary, el("p", { class: "h-meta" }, `Thresholds: ${pb.policy || "policy file"}. Polled every ${pb.interval} s.${recomputed}`))),
    el("figure", { class: "chart" }, chart.node, el("p", { class: "caption" },
      "Health score per poll. Dashed lines mark where the model explained what changed" +
      (pb.live ? "; shaded polls had CPU load applied from this page." : "."))),
    fams, anomalies, expl, skipped));

  const notScored = (list) => skipped.replaceChildren(el("summary", null, `Not scored (${list.length})`),
    el("ul", null, ...list.map((x) => el("li", null, `${NAMES[x.family] || x.family}${x.row ? ` ${x.row}` : ""}: ${x.reason}`))));
  notScored(pb.not_scored || []);

  function show(x) {
    const tone = LEVEL_TONE[x.level] || "";
    score.textContent = x.score === null ? "–" : x.score;
    score.className = `h-score ${tone}`;
    level.textContent = (x.level || "") + (x.capped_by ? ` (capped: a ${x.capped_by} reading)` : "");
    level.className = `h-level ${tone}`;
    summary.textContent = `${clockOf(x)} UTC · ${x.summary || x.note || ""} · ${x.coverage || ""}`;
    fams.replaceChildren(...Object.entries(x.families || {}).map(([k, f]) => familyRow(k, f)));
    anomalies.replaceChildren(...(x.events || []).map((a) => el("li", null, `Event: ${a}`)),
      ...(x.anomalies || []).map((a) => el("li", null, `Unusual: ${a}`)));
    if (x.explanation) expl.replaceChildren(...explanation(x));
    if (x.alert) expl.append(alertLine(x.alert));
  }
  return { chart, show, notScored };
}

export function playHealth(pb) {
  halt();
  ui.device(pb.device);
  const view = healthView(pb);
  return guarded(async () => {
    for (let i = 0; i < pb.samples.length; i++) {
      view.chart.render(pb.samples.slice(0, i + 1), pb.samples.length);
      view.show(pb.samples[i]);
      ui.status(`Replaying poll ${i + 1} of ${pb.samples.length} (${pb.interval} s apart)`);
      await sleep(pb.samples[i].explanation ? 3200 : 700);
    }
    ui.status(`Replay finished: ${pb.highlight}.`);
  });
}

/** A live monitor event carries the full score result; reduce it to the replay's sample shape. */
export function liveSample(res) {
  const families = Object.fromEntries(Object.entries(res.families || {}).map(([k, f]) => {
    const worst = f.rows.find((r) => r.name === f.worst) || f.rows[0] || {};
    return [k, { score: f.score, worst: f.worst, why: worst.why, rows: f.rows.length,
      flagged: f.rows.filter((r) => r.level !== "ok").length, partial: f.partial }];
  }));
  const { at, alert, score, level, capped_by, summary, note, coverage, explanation: e } = res;
  return { at, alert, score, level, capped_by, summary, note, coverage, families, explanation: e,
    anomalies: res.anomalies || [], events: res.events || [] };
}
