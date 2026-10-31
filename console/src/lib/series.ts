// Turning live measurement samples into the same series shape the verifier records, so a live chart and a
// recorded chart are drawn the same way. Counters become a rate per second; gauges follow the row that moves most.
import type { Pick, Series } from "./types";

export interface LiveSample { t: number; phase?: string; rows: Record<string, number | null> }

export function seriesFromSamples(samples: LiveSample[], pick: Pick): Series {
  const counter = pick.value_type === "counter";
  const scale = pick.scale_to_canonical ?? 1;
  const out: Series = { base: [], load: [], counter, rows: samples.length ? Object.keys(samples[0].rows).length : 0 };
  const t0 = samples[0]?.t ?? 0;
  let moving: string | null = null;
  if (!counter) {
    let spread = -1;
    for (const r of Object.keys(samples[0]?.rows ?? {})) {
      const vs = samples.map((s) => s.rows[r]).filter((v): v is number => typeof v === "number");
      const sp = vs.length ? Math.max(...vs) - Math.min(...vs) : 0;
      if (sp > spread) { spread = sp; moving = r; }
    }
  }
  samples.forEach((s, i) => {
    let v: number | null = null;
    if (counter) {
      if (!i) return;
      const prev = samples[i - 1];
      let total = 0;
      for (const [r, x] of Object.entries(s.rows)) {
        const p = prev.rows[r];
        if (typeof x === "number" && typeof p === "number" && x >= p) total += x - p;
      }
      v = total / Math.max(1e-6, s.t - prev.t);
    } else if (moving !== null) {
      const x = s.rows[moving];
      v = typeof x === "number" ? x * scale : null;
    }
    if (v === null || Number.isNaN(v)) return;
    (s.phase === "load" ? out.load : out.base).push([Math.round((s.t - t0) * 10) / 10, v]);
  });
  return out;
}

const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);

/** Baseline and under-load averages, and the change between them. */
export function summarize(s?: Series) {
  if (!s) return { base: null, load: null, change: null };
  const base = mean(s.base.map((p) => p[1]));
  const load = mean(s.load.map((p) => p[1]));
  const change = base !== null && load !== null && Math.abs(base) > 1e-9 ? (load - base) / Math.abs(base) : null;
  return { base, load, change };
}
