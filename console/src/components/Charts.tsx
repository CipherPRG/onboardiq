// Time series as raw 1px lines: no area fills, no animation. Under-load periods are a flat band.
import { CartesianGrid, Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { HealthSample, Series } from "../lib/types";
import { num, type Tone } from "../lib/format";

const COLOR: Record<Tone, string> = { ok: "#059669", warn: "#D97706", crit: "#DC2626", muted: "#8b95a3", info: "#4c8dff" };
const AXIS = { stroke: "#38414d", tick: { fill: "#8b95a3", fontSize: 10, fontFamily: "Geist Mono, monospace" }, tickLine: false };
const TIP = {
  contentStyle: { background: "#111418", border: "1px solid #38414d", borderRadius: 2, fontSize: 11, padding: "4px 6px" },
  labelStyle: { color: "#8b95a3" }, itemStyle: { color: "#e3e7ec", padding: 0 }, cursor: { stroke: "#38414d" },
};

export function TraceChart({ series, unit, tone, height = 170 }: { series: Series; unit?: string; tone: Tone; height?: number }) {
  const data = [...series.base.map(([t, v]) => ({ t, v })), ...series.load.map(([t, v]) => ({ t, v }))];
  const loadStart = series.load[0]?.[0];
  const end = data[data.length - 1]?.t;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="#262c35" vertical={false} />
        {loadStart !== undefined && end !== undefined && (
          <ReferenceArea x1={loadStart} x2={end} fill="#D97706" fillOpacity={0.07} stroke="none"
            label={{ value: "under load", position: "insideTopLeft", fill: "#D97706", fontSize: 10 }} />
        )}
        <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} unit="s" {...AXIS} />
        <YAxis width={58} tickFormatter={(v: number) => num(v, unit)} {...AXIS} />
        <Tooltip {...TIP} formatter={(v) => [num(Number(v), unit), series.counter ? "rate/s" : "value"]} labelFormatter={(t) => `${t} s`} />
        <Line dataKey="v" stroke={COLOR[tone]} strokeWidth={1} dot={{ r: 1.5, strokeWidth: 0, fill: COLOR[tone] }} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function ScoreChart({ samples, interval, height = 200 }: { samples: HealthSample[]; interval: number; height?: number }) {
  const data = samples.map((x, i) => ({ t: i * interval, score: x.score, load: x.load }));
  const bands: [number, number][] = [];
  data.forEach((d, i) => { if (d.load) { const last = bands[bands.length - 1]; if (last && last[1] === data[i - 1]?.t) last[1] = d.t; else bands.push([d.t, d.t]); } });
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="#262c35" vertical={false} />
        {bands.map(([a, b]) => <ReferenceArea key={a} x1={a} x2={b} fill="#D97706" fillOpacity={0.08} stroke="none" />)}
        <ReferenceArea y1={75} y2={100} fill="#059669" fillOpacity={0.06} stroke="none" />
        <ReferenceArea y1={40} y2={75} fill="#D97706" fillOpacity={0.06} stroke="none" />
        <ReferenceArea y1={0} y2={40} fill="#DC2626" fillOpacity={0.06} stroke="none" />
        <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} unit="s" {...AXIS} />
        <YAxis width={82} domain={[0, 100]} ticks={[20, 57, 87]} {...AXIS}
          tickFormatter={(v: number) => (v > 75 ? "Healthy" : v > 40 ? "Under strain" : "In trouble")} />
        <Tooltip {...TIP} formatter={(v) => [String(v), "score"]} labelFormatter={(t) => `${t} s`} />
        <Line dataKey="score" stroke="#e3e7ec" strokeWidth={1} dot={false} isAnimationActive={false} connectNulls={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
