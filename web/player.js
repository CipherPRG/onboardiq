// OnboardIQ replay player. Plays a recorded session's events: the model's pick, the measurements
// drawn as they were taken, the verdict, and the model's re-read when a pick fails.
// All text from sessions is inserted with textContent; nothing is parsed as HTML.
"use strict";

const FAMILY = { cpu: "CPU", memory: "Memory", temperature: "Temperature", fan: "Fan" };
const VERDICT = {
  "proven-experiment": ["Proven", "by experiment on the device", "ok"],
  "proven-witness": ["Proven", "by an independent second object", "ok"],
  "proven-static": ["Proven", "by static checks", "ok"],
  "unproven": ["Not proven", "checks passed, but nothing showed it under load", "warn"],
  "not-supported": ["Not on this device", "the device does not serve this object", "muted"],
  "rejected": ["Rejected", "failed on the device", "bad"],
};
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";

function el(tag, attrs = {}, text) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}
function svg(tag, attrs = {}, text) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = String(text);
  return n;
}
function fmt(v, unit) {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  if (unit === "bytes") {
    if (Math.abs(v) >= 1e9) return (v / 1e9).toFixed(2) + " GB";
    if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(1) + " MB";
    return Math.round(v).toLocaleString() + " B";
  }
  if (unit === "percent") return Math.round(v) + "%";
  if (Math.abs(v) >= 1000) return Math.round(v).toLocaleString();
  return Number(v.toPrecision(3)).toString();
}

// ---------------------------------------------------------------- playback clock
const play = { token: 0, paused: false, speed: 2, resume: null };
function sleep(ms) {
  const token = play.token;
  return new Promise((resolve, reject) => {
    const step = () => {
      if (token !== play.token) return reject(new Error("stopped"));
      if (play.paused) { play.resume = step; return; }
      resolve();
    };
    setTimeout(step, REDUCED ? 0 : ms / play.speed);
  });
}

// ---------------------------------------------------------------- chart, drawn point by point
function makeChart(series, unit, tone) {
  const pts = [...series.base, ...series.load];
  const W = 480, H = 180, L = 62, R = 10, T = 14, B = 28;
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || 1;
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi === lo) { const pad = Math.abs(hi) * 0.1 || 1; lo -= pad; hi += pad; }
  else { const pad = (hi - lo) * 0.15; lo -= pad; hi += pad; }
  if (Math.min(...ys) >= 0) lo = Math.max(lo, 0);
  if (unit === "percent") { lo = Math.max(lo, 0); hi = Math.min(Math.max(hi, 10), 105); }
  const X = (x) => L + ((x - x0) / ((x1 - x0) || 1)) * (W - L - R);
  const Y = (y) => T + ((hi - y) / ((hi - lo) || 1)) * (H - T - B);

  const fig = el("figure", { class: "chart " + tone });
  fig.style.margin = "0";
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": "Measured values at baseline and under load" });
  for (let k = 0; k < 4; k++) {
    const v = lo + ((hi - lo) * k) / 3;
    s.append(svg("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: "grid" }));
    s.append(svg("text", { x: L - 8, y: Y(v) + 4, "text-anchor": "end" }, fmt(v, unit)));
  }
  const baseEnd = series.base.length ? series.base[series.base.length - 1][0] : x0;
  const loadStart = series.load.length ? series.load[0][0] : x1;
  const bandX = series.base.length ? X((baseEnd + loadStart) / 2) : X(loadStart);
  const band = svg("rect", { x: bandX, y: T, width: W - R - bandX, height: H - T - B, class: "band" });
  band.style.opacity = "0";
  const bandLabel = svg("text", { x: bandX + 6, y: T + 14 }, "under load");
  bandLabel.style.opacity = "0";
  s.append(band, bandLabel, svg("text", { x: L + 4, y: T + 14 }, "baseline"));
  s.append(svg("text", { x: L, y: H - 8 }, fmt(x0) + " s"),
           svg("text", { x: W - R, y: H - 8, "text-anchor": "end" }, fmt(x1) + " s"));
  const line = svg("path", { class: "line", d: "" });
  const dots = svg("g");
  s.append(line, dots);
  fig.append(s);
  const what = series.counter ? "rate per second" : (series.rows > 1 ? "responding row" : "value");
  fig.append(el("p", { class: "caption" }, "Measured " + what));

  let d = "";
  async function draw() {
    for (let i = 0; i < pts.length; i++) {
      const [x, y] = pts[i];
      if (i === series.base.length) { band.style.opacity = "1"; bandLabel.style.opacity = "1"; }
      d += (i ? "L" : "M") + X(x).toFixed(1) + "," + Y(y).toFixed(1);
      line.setAttribute("d", d);
      dots.append(svg("circle", { cx: X(x).toFixed(1), cy: Y(y).toFixed(1), r: 2.6 }));
      await sleep(i === series.base.length ? 700 : 260);
    }
  }
  return { fig, draw };
}

// ---------------------------------------------------------------- one session
const families = {};

function familySection(fam) {
  if (families[fam]) return families[fam];
  const sec = el("section", { class: "family appear" });
  const head = el("header");
  head.append(el("h3", {}, FAMILY[fam] || fam));
  const final = el("p", { class: "final" });
  head.append(final);
  sec.append(head);
  $("families").append(sec);
  families[fam] = { sec, final, current: null };
  return families[fam];
}

function claimBlock(ev) {
  const p = ev.pick || {};
  const card = el("div", { class: "attempt appear" });
  const claim = el("div", { class: "claim" });
  claim.append(el("p", { class: "n" }, "Attempt " + ev.attempt));
  claim.append(el("h4", {}, p.object_name || ev.object));
  claim.append(el("p", { class: "oid" }, p.oid || ev.oid));
  const scale = p.scale_to_canonical;
  if (scale !== null && scale !== undefined && scale !== 1 && p.canonical_unit && p.canonical_unit !== "none") {
    claim.append(el("p", { class: "pred" }, `Scaled ×${Number(scale)} to ${p.canonical_unit}.`));
  }
  if ((ev.family === "cpu" || ev.family === "memory") && p.under_load && p.under_load !== "none") {
    claim.append(el("p", { class: "pred" }, `The model predicted it should ${p.under_load} under load.`));
  }
  if (p.evidence) claim.append(el("blockquote", {}, p.evidence));
  const test = el("div", { class: "test" });
  const waiting = el("p", { class: "waiting" }, "Testing on the device…");
  test.append(waiting);
  card.append(claim, test);
  return { card, test, waiting };
}

async function showVerify(ev, slot) {
  const p = ev.pick || {};
  let [title, sub, tone] = VERDICT[ev.verdict] || [ev.verdict, "", "muted"];
  const evidence = ev.evidence || [];
  if (ev.verdict === "rejected" && evidence.some((c) => (c.detail || "").includes("not served"))) {
    [title, sub, tone] = ["Not served", "this device does not have this object", "muted"];
  }
  slot.waiting.remove();
  if (ev.series && (ev.series.base.length || ev.series.load.length)) {
    const ch = makeChart(ev.series, p.canonical_unit, tone);
    slot.test.append(ch.fig);
    $("status").textContent = `Measuring ${ev.object}: baseline, then under load…`;
    await ch.draw();
  } else {
    await sleep(600);
  }
  const stamp = el("p", { class: "stamp appear " + tone });
  stamp.append(el("strong", {}, title), document.createTextNode(sub));
  slot.test.append(stamp);
  const ul = el("ul", { class: "checks" });
  for (const c of evidence) {
    const li = el("li", { class: c.ok ? "pass" : (c.ok === false ? "fail" : "info") });
    li.append(el("span", { class: "mark", "aria-hidden": "true" }, c.ok ? "✓" : (c.ok === false ? "✗" : "·")),
              el("span", { class: "what" }, c.check), el("span", {}, c.detail));
    ul.append(li);
  }
  slot.test.append(ul);
  await sleep(1400);
}

async function playSession(pb) {
  play.token++;
  play.paused = false;
  $("play").textContent = "Pause";
  $("families").replaceChildren();
  for (const k of Object.keys(families)) delete families[k];
  $("device").textContent = pb.device;
  try {
    for (const ev of pb.events) {
      if (ev.kind === "read") {
        $("status").textContent = `Muse Spark is reading ${pb.objects} MIB objects (${pb.modules})…`;
        await sleep(1800);
      } else if (ev.kind === "verify-start") {
        const f = familySection(ev.family);
        f.current = claimBlock(ev);
        f.sec.append(f.current.card);
        $("status").textContent = `Testing ${FAMILY[ev.family] || ev.family}: ${ev.object}`;
        await sleep(1500);
      } else if (ev.kind === "verify") {
        await showVerify(ev, familySection(ev.family).current);
      } else if (ev.kind === "repick") {
        const f = familySection(ev.family);
        const box = el("div", { class: "reread appear" });
        const p = el("p");
        p.append(el("strong", {}, "Re-read the manual with this evidence. "));
        const text = document.createTextNode("");
        p.append(text);
        box.append(p);
        f.sec.append(box);
        $("status").textContent = "The model is re-reading the manual with the evidence…";
        const words = String(ev.reasoning || "").split(/(\s+)/);
        const lead = words.slice(0, 120);
        for (let i = 0; i < lead.length; i += 6) {
          text.textContent = lead.slice(0, i + 6).join("");
          await sleep(90);
        }
        if (words.length > lead.length) text.textContent += " …";
        await sleep(900);
      } else if (ev.kind === "no-pick") {
        const f = familySection(ev.family);
        f.sec.append(el("p", { class: "waiting appear" }, "The model found no object for this in the manual."));
        await sleep(900);
      } else if (ev.kind === "final") {
        const f = familySection(ev.family);
        const [title, sub, tone] = VERDICT[ev.verdict] || [ev.verdict, "", "muted"];
        f.final.className = "final appear " + tone;
        f.final.replaceChildren(document.createTextNode(title + " "), el("span", {}, sub +
          (ev.attempts > 1 ? ` after ${ev.attempts} attempts` : "")));
        await sleep(600);
      } else if (ev.kind === "done") {
        const verdicts = Object.values(pb.summary);
        const proven = verdicts.filter((v) => v.startsWith("proven")).length;
        const corrected = Object.keys(pb.summary).filter((fam) => pb.summary[fam].startsWith("proven") &&
          pb.events.some((e) => e.kind === "verify" && e.family === fam && e.verdict === "rejected")).length;
        let msg = `${proven} of ${verdicts.length} metrics proven on the device`;
        if (corrected) msg += `; the agent caught and corrected ${corrected} wrong pick${corrected > 1 ? "s" : ""}`;
        $("status").textContent = "Replay finished.";
        $("families").append(el("p", { class: "done appear" }, msg + "."));
      }
    }
  } catch (e) {
    if (e.message !== "stopped") throw e;
  }
  $("play").textContent = "Play";
}

// ---------------------------------------------------------------- wiring
let current = null;
async function load(id, button) {
  document.querySelectorAll(".sessions button").forEach((b) => b.setAttribute("aria-pressed", "false"));
  button.setAttribute("aria-pressed", "true");
  if (!/^[A-Za-z0-9_-]+$/.test(id)) return;
  const r = await fetch(`sessions/${id}.json`);
  current = await r.json();
  $("play").disabled = false;
  $("restart").disabled = false;
  playSession(current);
}

$("play").addEventListener("click", () => {
  if (!current) return;
  if ($("play").textContent === "Play") { playSession(current); return; }
  play.paused = !play.paused;
  $("play").textContent = play.paused ? "Resume" : "Pause";
  if (!play.paused && play.resume) { const r = play.resume; play.resume = null; r(); }
});
$("restart").addEventListener("click", () => current && playSession(current));
$("speed").addEventListener("change", (e) => { play.speed = Number(e.target.value) || 1; });

(async () => {
  const list = await (await fetch("sessions/index.json")).json();
  for (const s of list) {
    const verdicts = Object.values(s.summary);
    const proven = verdicts.filter((v) => v.startsWith("proven")).length;
    const b = el("button", { type: "button", "aria-pressed": "false" });
    b.append(el("span", { class: "name" }, s.title || s.device),
             el("span", { class: "tally" }, `${proven} of ${verdicts.length} proven`));
    b.addEventListener("click", () => load(s.id, b));
    const li = el("li");
    li.append(b);
    $("session-list").append(li);
  }
})();
