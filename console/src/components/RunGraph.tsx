// The run as a graph: device → MIB modules → model → each pick (re-reads in sequence) → the final verdict per metric.
// Colour is the measured verdict; a thicker line marks the path that ended in the final answer.
import { useMemo } from "react";
import { run, ui, useStore, type Attempt } from "../lib/store";
import { FAMILY_LABEL, verdictLabel, type Tone } from "../lib/format";

const STROKE: Record<Tone, string> = { ok: "#059669", warn: "#D97706", crit: "#DC2626", muted: "#5d6672", info: "#4c8dff" };
const COL = 168, NODE_W = 148, NODE_H = 26, ROW = 34, TOP = 22;

function Node({ x, y, label, sub, tone, active, onClick }: {
  x: number; y: number; label: string; sub?: string; tone: Tone; active?: boolean; onClick?: () => void;
}) {
  return (
    <g transform={`translate(${x},${y})`} onClick={onClick} className={onClick ? "cursor-pointer" : undefined}
      role={onClick ? "button" : undefined} aria-label={onClick ? `${label} ${sub ?? ""}` : undefined}>
      <rect width={NODE_W} height={NODE_H} rx={2} fill={active ? "#1d222a" : "#111418"} stroke={STROKE[tone]} strokeWidth={active ? 1.5 : 1} />
      <text x={6} y={11} fontSize={10} fill="#e3e7ec" fontFamily="Geist Mono, monospace">{label.length > 22 ? label.slice(0, 21) + "…" : label}</text>
      {sub && <text x={6} y={21} fontSize={9} fill="#8b95a3" fontFamily="Geist, sans-serif">{sub}</text>}
    </g>
  );
}

export function RunGraph() {
  const { device, modules, model, attempts, finals, noPick, kind } = useStore(run, (s) => ({
    device: s.device, modules: s.modules, model: s.model, attempts: s.attempts, finals: s.finals, noPick: s.noPick, kind: s.kind,
  }));
  const selected = useStore(ui, (s) => s.selected);

  const families = useMemo(() => [...new Set([...attempts.map((a) => a.family), ...Object.keys(finals)])], [attempts, finals]);
  const maxAttempts = Math.max(1, ...attempts.map((a) => a.attempt));
  const rows = Math.max(families.length, modules.length, 1);
  const height = TOP + rows * ROW + 8;
  const width = COL * (4 + maxAttempts) - (COL - NODE_W);
  const mid = (i: number, n: number) => TOP + ((rows - n) * ROW) / 2 + i * ROW;
  const xModel = COL * 2, xFinal = COL * (3 + maxAttempts);

  if (kind !== "onboarding") return null;
  const edge = (x1: number, y1: number, x2: number, y2: number, tone: Tone, bold = false, dashed = false) => (
    <path key={`${x1}-${y1}-${x2}-${y2}`} d={`M${x1},${y1 + NODE_H / 2} C${(x1 + x2) / 2},${y1 + NODE_H / 2} ${(x1 + x2) / 2},${y2 + NODE_H / 2} ${x2},${y2 + NODE_H / 2}`}
      fill="none" stroke={STROKE[tone]} strokeWidth={bold ? 2 : 1} strokeDasharray={dashed ? "3 3" : undefined} opacity={bold ? 1 : 0.7} />
  );
  const yModel = mid(0, 1);
  const tone = (a: Attempt) => verdictLabel(a.verdict)[1];

  return (
    <div className="overflow-auto border-b border-border bg-bg shrink-0 max-h-[260px]">
      <svg width={width + 16} height={height} className="block" role="img" aria-label="Run graph">
        <g transform="translate(8,0)">
          {["Device", "MIB modules", "Model", ...Array.from({ length: maxAttempts }, (_, i) => `Attempt ${i + 1}`), "Verdict"].map((h, i) => (
            <text key={h} x={i * COL} y={13} fontSize={10} fill="#5d6672" fontFamily="Geist, sans-serif">{h}</text>
          ))}
          {modules.map((_, i) => edge(0 + NODE_W, mid(0, 1), COL, mid(i, modules.length), "muted"))}
          {modules.map((_, i) => edge(COL + NODE_W, mid(i, modules.length), xModel, yModel, "muted"))}
          {families.map((f, r) => {
            const list = attempts.filter((a) => a.family === f).sort((a, b) => a.attempt - b.attempt);
            const y = mid(r, families.length);
            const fin = finals[f];
            const finTone = verdictLabel(fin?.verdict)[1];
            const lastX = list.length ? COL * (2 + list[list.length - 1].attempt) : xModel;
            return (
              <g key={f}>
                {list.length > 0 && edge(xModel + NODE_W, yModel, COL * 3, y, tone(list[0]), false)}
                {list.slice(1).map((a, i) => edge(COL * (2 + list[i].attempt) + NODE_W, y, COL * (2 + a.attempt), y, tone(a), false, true))}
                {fin && edge(lastX + NODE_W, y, xFinal, y, finTone, String(fin.verdict).startsWith("proven"))}
                {list.map((a) => (
                  <Node key={a.key} x={COL * (2 + a.attempt)} y={y} label={a.object} tone={tone(a)}
                    sub={`${FAMILY_LABEL[f] ?? f} · ${verdictLabel(a.verdict)[0]}`} active={selected === a.key}
                    onClick={() => ui.set({ selected: a.key })} />
                ))}
                {fin && <Node x={xFinal} y={y} label={FAMILY_LABEL[f] ?? f} tone={finTone}
                  sub={verdictLabel(fin.verdict)[0] + (noPick.includes(f) ? " · no pick left" : "")} />}
              </g>
            );
          })}
          <Node x={0} y={mid(0, 1)} label={device || "device"} sub="read-only SNMP" tone="info" />
          {modules.map((m, i) => <Node key={m} x={COL} y={mid(i, modules.length)} label={m} tone="muted" />)}
          <Node x={xModel} y={yModel} label={model ?? "model"} sub="reads the MIB text" tone="info" />
        </g>
      </svg>
    </div>
  );
}
