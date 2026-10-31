// Charts drawn as SVG, point by point. A trace chart shows one object's readings at baseline and under load;
// the score chart shows a health score per poll.
import { el, svg, fmt } from "./util.js";
import { sleep } from "./clock.js";
import { LEVEL_TONE } from "./model.js";

const TRACE = { W: 480, H: 180, L: 62, R: 10, T: 14, B: 28 };
const SCORE = { W: 960, H: 200, L: 40, R: 10, T: 12, B: 26 };

/** The y range for a set of readings: padded, never negative for non-negative data, capped for percentages. */
function yRange(ys, unit) {
  let lo = Math.min(...ys), hi = Math.max(...ys);
  const pad = hi === lo ? (Math.abs(hi) * 0.1 || 1) : (hi - lo) * 0.15;
  lo -= pad; hi += pad;
  if (Math.min(...ys) >= 0) lo = Math.max(lo, 0);
  if (unit === "percent") hi = Math.min(Math.max(hi, 10), 105);
  return [lo, hi];
}

function gridLine(m, y, label, anchorX) {
  return [svg("line", { x1: m.L, x2: m.W - m.R, y1: y, y2: y, class: "grid" }),
          svg("text", { x: anchorX, y: y + 4, "text-anchor": "end" }, label)];
}

/** series: {base: [[t, v]...], load: [[t, v]...], counter, rows}. draw(instant, wait) plots the points in order. */
export function traceChart(series, unit, tone = "") {
  const m = TRACE;
  const pts = [...series.base, ...series.load];
  const xs = pts.map((p) => p[0]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || 1;
  const [lo, hi] = yRange(pts.map((p) => p[1]), unit);
  const X = (x) => m.L + ((x - x0) / ((x1 - x0) || 1)) * (m.W - m.L - m.R);
  const Y = (y) => m.T + ((hi - y) / ((hi - lo) || 1)) * (m.H - m.T - m.B);

  const s = svg("svg", { viewBox: `0 0 ${m.W} ${m.H}`, role: "img", "aria-label": "Measured values at baseline and under load" });
  for (let k = 0; k < 4; k++) {
    const v = lo + ((hi - lo) * k) / 3;
    s.append(...gridLine(m, Y(v), fmt(v, unit), m.L - 8));
  }
  const baseEnd = series.base.length ? series.base[series.base.length - 1][0] : x0;
  const loadStart = series.load.length ? series.load[0][0] : x1;
  const bandX = X(series.base.length ? (baseEnd + loadStart) / 2 : loadStart);
  const band = svg("rect", { x: bandX, y: m.T, width: m.W - m.R - bandX, height: m.H - m.T - m.B, class: "band" });
  const bandLabel = svg("text", { x: bandX + 6, y: m.T + 14, class: "band-label" }, "under load");
  const line = svg("path", { class: "line", d: "" });
  const dots = svg("g");
  s.append(band, bandLabel, svg("text", { x: m.L + 4, y: m.T + 14 }, "baseline"),
    svg("text", { x: m.L, y: m.H - 8 }, fmt(x0) + " s"),
    svg("text", { x: m.W - m.R, y: m.H - 8, "text-anchor": "end" }, fmt(x1) + " s"), line, dots);

  const what = series.counter ? "rate per second" : (series.rows > 1 ? "responding row" : "value");
  const fig = el("figure", { class: `chart ${tone}` }, s, el("p", { class: "caption" }, `Measured ${what}`));
  const showBand = () => { band.classList.add("on"); bandLabel.classList.add("on"); };

  let d = "";
  async function draw(instant = false, wait = sleep) {
    for (let i = 0; i < pts.length; i++) {
      const [x, y] = pts[i];
      if (i === series.base.length) showBand();
      d += `${i ? "L" : "M"}${X(x).toFixed(1)},${Y(y).toFixed(1)}`;
      line.setAttribute("d", d);
      dots.append(svg("circle", { cx: X(x).toFixed(1), cy: Y(y).toFixed(1), r: 2.6 }));
      if (!instant) await wait(i === series.base.length ? 700 : 260);
    }
    if (series.load.length) showBand();
  }
  return { fig, draw };
}

/** A health score (0 to 100) per poll. render(list, slots, offset) redraws the whole line. */
export function scoreChart(interval) {
  const m = SCORE;
  const Y = (v) => m.T + ((100 - v) / 100) * (m.H - m.T - m.B);
  const s = svg("svg", { viewBox: `0 0 ${m.W} ${m.H}`, role: "img", "aria-label": "Health score over time" });
  for (const v of [0, 40, 75, 100]) s.append(...gridLine(m, Y(v), v, m.L - 6));
  const t0 = svg("text", { x: m.L, y: m.H - 6 });
  const t1 = svg("text", { x: m.W - m.R, y: m.H - 6, "text-anchor": "end" });
  const bands = svg("g"), line = svg("path", { class: "line", d: "" }), marks = svg("g"), dots = svg("g");
  s.append(t0, t1, bands, line, marks, dots);
  return {
    node: s,
    // list: the samples to draw (oldest first); slots: how many polls the x axis spans; offset: polls before list[0]
    render(list, slots, offset = 0) {
      const X = (i) => m.L + (i / Math.max(1, slots - 1)) * (m.W - m.L - m.R);
      let d = "";
      for (const g of [dots, marks, bands]) g.replaceChildren();
      list.forEach((x, i) => {
        if (x.load) bands.append(svg("rect", { x: X(i), y: m.T, width: Math.max(2, X(i + 1) - X(i)), height: m.H - m.T - m.B, class: "band on" }));
        if (x.score === null || x.score === undefined) return;
        d += `${d ? "L" : "M"}${X(i).toFixed(1)},${Y(x.score).toFixed(1)}`;
        dots.append(svg("circle", { cx: X(i).toFixed(1), cy: Y(x.score).toFixed(1), r: 3, class: LEVEL_TONE[x.level] || "" }));
        if (x.explanation) marks.append(svg("line", { x1: X(i), x2: X(i), y1: m.T, y2: m.H - m.B, class: "mark-x" }));
      });
      line.setAttribute("d", d);
      t0.textContent = `${offset * interval} s`;
      t1.textContent = `${(offset + slots - 1) * interval} s`;
    },
  };
}


