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
  async function draw(instant = false) {
    for (let i = 0; i < pts.length; i++) {
      const [x, y] = pts[i];
      if (i === series.base.length) { band.style.opacity = "1"; bandLabel.style.opacity = "1"; }
      d += (i ? "L" : "M") + X(x).toFixed(1) + "," + Y(y).toFixed(1);
      line.setAttribute("d", d);
      dots.append(svg("circle", { cx: X(x).toFixed(1), cy: Y(y).toFixed(1), r: 2.6 }));
      if (!instant) await sleep(i === series.base.length ? 700 : 260);
    }
    if (instant && series.load.length) { band.style.opacity = "1"; bandLabel.style.opacity = "1"; }
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

async function showVerify(ev, slot, instant = false) {
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
    if (!instant) $("status").textContent = `Measuring ${ev.object}: baseline, then under load…`;
    await ch.draw(instant);
  } else if (!instant) {
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
  if (!instant) await sleep(1400);
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
        $("status").textContent = `${pb.model || "The model"} is reading ${pb.objects} MIB objects (${pb.modules})…`;
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

// ---------------------------------------------------------------- health replay (scripts/monitor.py --record)
const HFAM = { ...FAMILY, cpu_load: "CPU load", disk: "Disk" };
const LEVEL_TONE = { ok: "ok", warning: "warn", critical: "bad" };

function healthChart(iv) {
  const W = 960, H = 200, L = 40, R = 10, T = 12, B = 26;
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Health score over time" });
  const Y = (v) => T + ((100 - v) / 100) * (H - T - B);
  for (const v of [0, 40, 75, 100]) {
    s.append(svg("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: "grid" }));
    s.append(svg("text", { x: L - 6, y: Y(v) + 4, "text-anchor": "end" }, v));
  }
  const t0 = svg("text", { x: L, y: H - 6 }, "");
  const t1 = svg("text", { x: W - R, y: H - 6, "text-anchor": "end" }, "");
  const bands = svg("g"), line = svg("path", { class: "line", d: "" }), marks = svg("g"), dots = svg("g");
  s.append(t0, t1, bands, line, marks, dots);
  return {
    node: s,
    // list: the samples to draw (oldest first); slots: how many polls the x axis spans; offset: polls before list[0]
    render(list, slots, offset = 0) {
      const X = (i) => L + (i / Math.max(1, slots - 1)) * (W - L - R);
      let d = "";
      dots.replaceChildren(); marks.replaceChildren(); bands.replaceChildren();
      list.forEach((x, i) => {
        if (x.load) bands.append(svg("rect", { x: X(i), y: T, width: Math.max(2, X(i + 1) - X(i)), height: H - T - B, class: "band" }));
        if (x.score === null || x.score === undefined) return;
        d += (d ? "L" : "M") + X(i).toFixed(1) + "," + Y(x.score).toFixed(1);
        dots.append(svg("circle", { cx: X(i).toFixed(1), cy: Y(x.score).toFixed(1), r: 3, class: LEVEL_TONE[x.level] || "" }));
        if (x.explanation) marks.append(svg("line", { x1: X(i), x2: X(i), y1: T, y2: H - B, class: "mark-x" }));
      });
      line.setAttribute("d", d);
      t0.textContent = offset * iv + " s";
      t1.textContent = (offset + slots - 1) * iv + " s";
    },
  };
}

function healthView(pb) {
  const root = el("div", { class: "health appear" });
  const top = el("div", { class: "h-top" });
  const big = el("p", { class: "h-score" }, "–");
  const lvl = el("p", { class: "h-level" });
  const sum = el("p", { class: "h-sum" }, "Waiting for the first reading…");
  const meta = el("p", { class: "h-meta" }, `Thresholds: ${pb.policy || "policy file"}. Polled every ${pb.interval} s.`);
  const left = el("div");
  left.append(big, lvl);
  const right = el("div");
  right.append(sum, meta);
  top.append(left, right);
  const fig = el("figure", { class: "chart" });
  fig.style.margin = "0";
  const chart = healthChart(pb.interval);
  fig.append(chart.node, el("p", { class: "caption" },
    "Health score per poll. Dashed lines mark where the model explained what changed" +
    (pb.live ? "; shaded polls had CPU load applied from this page." : ".")));
  const fams = el("div", { class: "h-fams" });
  const anom = el("ul", { class: "h-anom" });
  const expl = el("div", { class: "h-expl" });
  expl.append(el("p", { class: "h-esrc" }, pb.explain === false ? "Explanations are off for this run."
    : "The model explains here when the level changes or something unusual appears."));
  const not = el("details", { class: "h-not" });
  root.append(top, fig, fams, anom, expl, not);
  $("families").append(root);

  function setNotScored(list) {
    not.replaceChildren(el("summary", {}, `Not scored (${list.length})`));
    const nl = el("ul");
    for (const x of list) nl.append(el("li", {}, (HFAM[x.family] || x.family) + (x.row ? " " + x.row : "") + ": " + x.reason));
    not.append(nl);
  }
  setNotScored(pb.not_scored || []);

  return {
    chart, setNotScored, meta,
    show(x) {
      big.textContent = x.score === null ? "–" : x.score;
      big.className = "h-score " + (LEVEL_TONE[x.level] || "");
      lvl.textContent = (x.level || "") + (x.capped_by ? ` (capped: a ${x.capped_by} reading)` : "");
      lvl.className = "h-level " + (LEVEL_TONE[x.level] || "");
      sum.textContent = `${(x.at || "").slice(11, 19)} UTC · ${x.summary || x.note || ""} · ${x.coverage || ""}`;
      fams.replaceChildren();
      for (const [k, f] of Object.entries(x.families || {})) {
        const tone = f.score >= 75 ? "ok" : (f.score >= 40 ? "warn" : "bad");
        const row = el("div", { class: "h-fam" });
        const bar = el("span", { class: "h-bar" });
        const fill = el("span", { class: "h-fill " + tone });
        fill.style.width = Math.max(2, f.score) + "%";
        bar.append(fill);
        row.append(el("span", { class: "h-name" }, HFAM[k] || k), bar,
                   el("span", { class: "h-num " + tone }, Math.round(f.score)),
                   el("span", { class: "h-why" }, (f.rows > 1 ? `worst of ${f.rows}: ${f.worst} · ` : "") + f.why +
                     (f.flagged ? ` · ${f.flagged} row${f.flagged > 1 ? "s" : ""} flagged` : "") +
                     (f.partial ? " · some rows skipped" : "")));
        fams.append(row);
      }
      anom.replaceChildren(...(x.anomalies || []).map((a) => el("li", {}, "Unusual: " + a)));
      if (x.explanation) {
        const e = x.explanation;
        const src = e.source === "template" ? "Template (the model's answer failed the check" +
          (e.note && e.note.includes("rejected: ") ? ": " + e.note.split("rejected: ")[1] : "") + ")"
          : `${e.source}, every number checked against the readings`;
        expl.replaceChildren(el("p", { class: "h-etext appear" }, e.text),
                             el("p", { class: "h-esrc" }, `${(x.at || "").slice(11, 19)} UTC · ${src}`));
      }
    },
  };
}

// A live monitor event carries the full score result; reduce it to the replay's sample shape.
function liveSample(res) {
  const fams = {};
  for (const [k, f] of Object.entries(res.families || {})) {
    const worst = f.rows.find((r) => r.name === f.worst) || f.rows[0] || {};
    fams[k] = { score: f.score, worst: f.worst, why: worst.why, rows: f.rows.length,
                flagged: f.rows.filter((r) => r.level !== "ok").length, partial: f.partial };
  }
  return { at: res.at, score: res.score, level: res.level, capped_by: res.capped_by, summary: res.summary,
           note: res.note, coverage: res.coverage, families: fams, anomalies: res.anomalies || [],
           explanation: res.explanation };
}

async function playHealth(pb) {
  play.token++;
  play.paused = false;
  $("play").textContent = "Pause";
  $("families").replaceChildren();
  $("device").textContent = pb.device;
  const v = healthView(pb);
  try {
    for (let i = 0; i < pb.samples.length; i++) {
      const x = pb.samples[i];
      v.chart.render(pb.samples.slice(0, i + 1), pb.samples.length);
      v.show(x);
      $("status").textContent = `Replaying poll ${i + 1} of ${pb.samples.length} (${pb.interval} s apart)`;
      await sleep(x.explanation ? 3200 : 700);
    }
    $("status").textContent = "Replay finished: " + pb.highlight + ".";
  } catch (e) {
    if (e.message !== "stopped") throw e;
  }
  $("play").textContent = "Play";
}

// ---------------------------------------------------------------- live health monitor (local server only)
const mon = { id: null, view: null, samples: [], loadUntil: 0, es: null };

async function loadOnboarded(select) {
  const list = await (await fetch("/api/onboarded", { cache: "no-store" })).json();
  const box = $("onboarded");
  box.replaceChildren(...list.map((o) => {
    const proven = Object.values(o.summary).filter((v) => String(v).startsWith("proven")).length;
    const opt = el("option", { value: o.name }, `${o.device || o.modules} · ${proven} of ${Object.keys(o.summary).length} proven · ${o.name.slice(8)}`);
    opt.dataset.host = o.host || "localhost";
    opt.dataset.port = o.port || "";
    return opt;
  }));
  if (!list.length) box.append(el("option", { value: "" }, "No onboarded devices yet: onboard one above first"));
  if (select) box.value = select;
  box.dispatchEvent(new Event("change"));
}

function monButtons(running, local) {
  $("mon-start").disabled = running;
  $("mon-stop").hidden = !running;
  $("mon-load").hidden = !(running && local);
  $("run").disabled = running;
}

function onMonitorEvent(ev) {
  if (ev.kind === "monitor-start") {
    $("families").replaceChildren();
    $("device").textContent = ev.device + " · live";
    mon.view = healthView({ policy: ev.policy, interval: ev.interval, live: true, explain: ev.explain });
    mon.interval = ev.interval;
    monButtons(true, ev.local);
    $("status").textContent = "Monitoring live. First reading in a moment…";
  } else if (ev.kind === "health") {
    const x = liveSample(ev);
    x.load = Date.now() < mon.loadUntil;
    mon.samples.push(x);
    const win = mon.samples.slice(-90);
    mon.view.chart.render(win, Math.max(30, win.length), mon.samples.length - win.length);
    mon.view.show(x);
    if (mon.samples.length === 1) mon.view.setNotScored(ev.excluded || []);
    $("status").textContent = `Live: ${mon.samples.length} reading${mon.samples.length > 1 ? "s" : ""}` +
      (x.load ? " · CPU load running" : "") + (x.explanation ? " · the model explained this change" : "");
  } else if (ev.kind === "load") {
    mon.loadUntil = Date.now() + ev.seconds * 1000;
    $("status").textContent = `CPU load started on this machine for ${ev.seconds} s. Watch the score.`;
  } else if (ev.kind === "poll-error") {
    $("status").textContent = "A poll failed: " + ev.message + " (still monitoring)";
  } else if (ev.kind === "end" || ev.kind === "error") {
    monButtons(false, false);
    mon.loadUntil = 0;
    $("status").textContent = ev.kind === "error" ? "Monitoring stopped: " + ev.message
      : `Monitoring ${ev.reason} after ${mon.samples.length} readings` + (ev.saved ? ". Saved to the session list as a replay." : ".");
    if (ev.saved) loadList();
  }
}

async function startMonitor(form) {
  const fd = new FormData(form);
  $("mon-error").textContent = "";
  play.token++;   // stop any replay
  try {
    monButtons(true, false);
    const { id } = await post("/api/monitor", {
      onboarded: fd.get("onboarded"), host: fd.get("host"), port: fd.get("port") ? Number(fd.get("port")) : null,
      community: fd.get("community"), interval: Number(fd.get("interval")) || 10,
      explain: fd.get("explain") === "on", record: fd.get("record") === "on",
    });
    mon.id = id; mon.samples = []; mon.loadUntil = 0;
    mon.es = new EventSource(`/api/runs/${id}/events`);
    mon.es.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      onMonitorEvent(ev);
      if (ev.kind === "end" || ev.kind === "error") mon.es.close();
    };
    mon.es.onerror = () => { mon.es.close(); monButtons(false, false); };
  } catch (e) {
    $("mon-error").textContent = e.message;
    monButtons(false, false);
  }
}

function wireMonitor() {
  $("monitor").hidden = false;
  const f = $("monitor-form");
  $("onboarded").addEventListener("change", (e) => {
    const o = e.target.selectedOptions[0];
    if (!o || !o.value) return;
    f.elements.host.value = o.dataset.host;
    f.elements.port.value = o.dataset.port;
  });
  f.addEventListener("submit", (e) => { e.preventDefault(); startMonitor(f); });
  $("mon-stop").addEventListener("click", async () => {
    $("status").textContent = "Stopping…";
    try { await post(`/api/runs/${mon.id}/stop`, {}); }
    catch (e) { $("mon-error").textContent = e.message; }
  });
  $("mon-load").addEventListener("click", async () => {
    try { await post(`/api/runs/${mon.id}/load`, { seconds: 120 }); }
    catch (e) { $("mon-error").textContent = e.message; }
  });
  loadOnboarded();
}

// ---------------------------------------------------------------- live runs (local server only)
// A live chart redrawn as each measurement arrives; replaced by the verified chart at the verdict.
function liveChart(ev) {
  const p = ev.pick || {};
  const scale = p.scale_to_canonical === null || p.scale_to_canonical === undefined ? 1 : p.scale_to_canonical;
  const counter = p.value_type === "counter";
  const samples = [];   // {t, phase, rows}
  const box = el("div");
  const note = el("p", { class: "waiting" }, "Measuring on the device…");
  box.append(note);
  function value(rows, prev) {
    if (counter) {
      if (!prev) return null;
      let tot = 0;
      for (const [r, v] of Object.entries(rows)) if (prev.rows[r] !== undefined && v >= prev.rows[r]) tot += v - prev.rows[r];
      return tot / Math.max(1e-6, rows.__t - prev.rows.__t);
    }
    // the row that has moved most so far (the one the verifier will judge)
    let best = null, spread = -1;
    for (const r of Object.keys(rows)) {
      if (r === "__t" || rows[r] === null) continue;
      const vs = samples.map((s) => s.rows[r]).filter((v) => typeof v === "number");
      const sp = vs.length ? Math.max(...vs) - Math.min(...vs) : 0;
      if (sp > spread) { spread = sp; best = r; }
    }
    return best === null ? null : rows[best] * scale;
  }
  return {
    box,
    add(sample) {
      const rows = { ...sample.rows, __t: sample.t };
      samples.push({ t: sample.t, phase: sample.phase, rows });
      const series = { base: [], load: [], counter, rows: Object.keys(sample.rows).length };
      const t0 = samples[0].t;
      samples.forEach((s, i) => {
        const v = value(s.rows, i ? samples[i - 1] : null);
        if (v === null || Number.isNaN(v)) return;
        (s.phase === "load" ? series.load : series.base).push([Math.round((s.t - t0) * 10) / 10, v]);
      });
      if (series.base.length + series.load.length < 1) return;
      const ch = makeChart(series, p.canonical_unit, "");
      ch.draw(true);
      box.replaceChildren(ch.fig, el("p", { class: "waiting" },
        sample.phase === "load" ? "Under load…" : (sample.phase === "base" ? "Baseline…" : "Observing…")));
    },
  };
}

const liveState = { slot: null, chart: null, run: null };

function onLiveEvent(ev, pb) {
  const k = ev.kind;
  if (k === "start") {
    $("device").textContent = ev.device;
    $("status").textContent = `${ev.model || "The model"} is reading ${ev.objects} MIB objects (${ev.modules})…`;
    Object.assign(pb, { objects: ev.objects, modules: ev.modules, model: ev.model });
  } else if (k === "read") {
    $("status").textContent = "Picks made. Testing them on the device…";
  } else if (k === "verify-start") {
    const f = familySection(ev.family);
    f.current = claimBlock(ev);
    f.sec.append(f.current.card);
    liveState.chart = liveChart(ev);
    f.current.waiting.replaceWith(liveState.chart.box);
    f.current.waiting = liveState.chart.box;
    $("status").textContent = `Testing ${FAMILY[ev.family] || ev.family}: ${ev.object}`;
  } else if (k === "sample") {
    if (liveState.chart) liveState.chart.add(ev);
  } else if (k === "verify") {
    liveState.chart = null;
    showVerify(ev, familySection(ev.family).current, true);
    pb.events.push(ev);
  } else if (k === "repick") {
    const f = familySection(ev.family);
    const box = el("div", { class: "reread appear" });
    const p = el("p");
    p.append(el("strong", {}, "Re-read the manual with this evidence. "), document.createTextNode(ev.reasoning || ""));
    box.append(p);
    f.sec.append(box);
    $("status").textContent = "The model is re-reading the manual with the evidence…";
  } else if (k === "no-pick") {
    familySection(ev.family).sec.append(el("p", { class: "waiting appear" }, "The model found no object for this in the manual."));
  } else if (k === "final") {
    const f = familySection(ev.family);
    const [title, sub, tone] = VERDICT[ev.verdict] || [ev.verdict, "", "muted"];
    f.final.className = "final appear " + tone;
    f.final.replaceChildren(document.createTextNode(title + " "), el("span", {}, sub +
      (ev.attempts > 1 ? ` after ${ev.attempts} attempts` : "")));
  } else if (k === "prompt") {
    $("prompt").hidden = false;
    if (/^STOP/i.test(ev.text)) { $("prompt-text").textContent = ev.text; $("prompt-cmd").textContent = ""; }
    else if (/^START/i.test(ev.text)) $("prompt-text").textContent = ev.text;
    else $("prompt-cmd").textContent = ev.text;
  } else if (k === "prompt-wait") {
    $("prompt-ok").hidden = false;
    $("prompt-ok").textContent = /^STOP/i.test($("prompt-text").textContent) ? "The load has stopped" : "The load is running";
  } else if (k === "end" || k === "error") {
    $("prompt").hidden = true;
    $("run").disabled = false;
    if (k === "error") { $("status").textContent = "The run stopped: " + ev.message; return; }
    const verdicts = Object.values(ev.summary);
    const proven = verdicts.filter((v) => v.startsWith("proven")).length;
    $("status").textContent = `Finished: ${proven} of ${verdicts.length} metrics proven, ${ev.seconds} s, ` +
      `${ev.snmp_calls} SNMP queries, model cost $${Number(ev.cost_usd).toFixed(3)}` + (ev.saved ? ". Saved to the session list." : ".");
    if (ev.saved) loadList();
    if (ev.onboarded) loadOnboarded(ev.onboarded);
  }
}

async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-OnboardIQ": "1" },
    body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `request failed (${r.status})`);
  return data;
}

async function startLive(form) {
  const fd = new FormData(form);
  const mode = fd.get("mode");
  const body = {
    device: fd.get("device"), modules: String(fd.get("modules") || "").split(",").map((m) => m.trim()).filter(Boolean),
    host: fd.get("host"), port: fd.get("port") ? Number(fd.get("port")) : null, community: fd.get("community"),
    families: fd.getAll("families"), lab: mode === "lab", load_by_hand: mode === "hand" ? fd.get("hand") : null,
    record: true,
  };
  $("live-error").textContent = "";
  play.token++;   // stop any replay
  $("families").replaceChildren();
  for (const k of Object.keys(families)) delete families[k];
  try {
    $("run").disabled = true;
    const { id } = await post("/api/runs", body);
    const pb = { events: [] };
    const es = new EventSource(`/api/runs/${id}/events`);
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      onLiveEvent(ev, pb);
      if (ev.kind === "end" || ev.kind === "error") es.close();
    };
    es.onerror = () => { es.close(); $("run").disabled = false; };
    liveState.run = id;
  } catch (e) {
    $("live-error").textContent = e.message;
    $("run").disabled = false;
  }
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
  start(current);
}

$("play").addEventListener("click", () => {
  if (!current) return;
  if ($("play").textContent === "Play") { start(current); return; }
  play.paused = !play.paused;
  $("play").textContent = play.paused ? "Resume" : "Pause";
  if (!play.paused && play.resume) { const r = play.resume; play.resume = null; r(); }
});
$("restart").addEventListener("click", () => current && start(current));
function start(pb) { return pb.kind === "health" ? playHealth(pb) : playSession(pb); }
$("speed").addEventListener("change", (e) => { play.speed = Number(e.target.value) || 1; });

async function loadList() {
  const list = await (await fetch("sessions/index.json", { cache: "no-store" })).json();
  $("session-list").replaceChildren();
  for (const s of list) {
    const verdicts = Object.values(s.summary || {});
    const proven = verdicts.filter((v) => v.startsWith("proven")).length;
    const b = el("button", { type: "button", "aria-pressed": "false" });
    b.append(el("span", { class: "name" }, s.device),
             el("span", { class: "tally" }, s.highlight || `${proven} of ${verdicts.length} proven`));
    b.addEventListener("click", () => load(s.id, b));
    const li = el("li");
    li.append(b);
    $("session-list").append(li);
  }
}

(async () => {
  await loadList();
  // Live mode only when this page is served by the local OnboardIQ server.
  try {
    const st = await (await fetch("/api/status", { cache: "no-store" })).json();
    if (st && st.live) {
      $("live").hidden = false;
      $("lede").textContent = "Replay recorded sessions, or onboard a device live from this machine. In a replay, " +
        "every reading and model answer comes from the recording; in a live run they come from the device and the model.";
      $("live-form").addEventListener("submit", (e) => { e.preventDefault(); startLive(e.target); });
      wireMonitor();
      $("prompt-ok").addEventListener("click", async () => {
        $("prompt-ok").hidden = true;
        try { await post(`/api/runs/${liveState.run}/confirm`, {}); } catch (e) { $("status").textContent = e.message; }
      });
      const input = $("live-form").elements.modules;
      let timer = null;
      input.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          const last = input.value.split(",").pop().trim();
          const before = input.value.includes(",") ? input.value.slice(0, input.value.lastIndexOf(",") + 1) : "";
          if (last.length < 2) return;
          const mods = await (await fetch("/api/modules?q=" + encodeURIComponent(last))).json();
          $("module-list").replaceChildren(...mods.map((m) => el("option", { value: before + m })));
        }, 200);
      });
    }
  } catch (e) { /* static hosting: replays only */ }
})();
