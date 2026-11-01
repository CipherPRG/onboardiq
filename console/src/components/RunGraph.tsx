// The run as a graph: device → MIB modules → model → each pick (re-reads in sequence) → the final verdict per metric.
// A status line above it says what the agent is doing right now. Colour is the measured verdict; a thicker line marks
// the path that ended in the final answer; a dashed line is the model being asked again after a rejected pick.
import { useEffect, useMemo, useState } from "react";
import { run, ui, useStore, type Attempt, type RunState } from "../lib/store";
import { FAMILY_LABEL, secs, verdictLabel, type Tone } from "../lib/format";
import { Dot } from "./ui";

const STROKE: Record<Tone, string> = { ok: "#059669", warn: "#D97706", crit: "#DC2626", muted: "#5d6672", info: "#4c8dff" };
const LEFT_W = 176, LEAD = 28, COL = 166, NODE_W = 148, NODE_H = 36, ROW = 44, TOP = 26, DEVICE_H = 50;
const label = (f: string) => FAMILY_LABEL[f] ?? f;

/** Split a long name over at most two lines of about `n` characters, at a space where possible. */
function wrap(text: string, n: number): string[] {
  if (text.length <= n) return [text];
  const cut = text.lastIndexOf(" ", n);
  const at = cut > n / 2 ? cut : n;
  const rest = text.slice(at).trim();
  return [text.slice(0, at).trim(), rest.length > n ? rest.slice(0, n - 1) + "…" : rest];
}

function Node({ x, y, title, sub, tone, w = NODE_W, h = NODE_H, active, busy, pending, onClick }: {
  x: number; y: number; title: string; sub?: string; tone: Tone; w?: number; h?: number; active?: boolean; busy?: boolean; pending?: boolean;
  onClick?: () => void;
}) {
  const n = Math.floor((w - 14) / 6.7);                    // characters that fit at 11px monospace
  const lines = h > NODE_H ? wrap(title, n) : [title.length > n ? title.slice(0, n - 1) + "…" : title];
  return (
    <g transform={`translate(${x},${y})`} onClick={onClick} className={onClick ? "cursor-pointer" : undefined}
      role={onClick ? "button" : undefined} aria-label={onClick ? `${title} ${sub ?? ""}` : undefined}>
      <title>{sub ? `${title}: ${sub}` : title}</title>
      <rect width={w} height={h} rx={3} fill={active ? "#1d222a" : pending ? "transparent" : "#111418"}
        stroke={STROKE[tone]} strokeWidth={active ? 1.75 : 1} strokeDasharray={pending ? "4 3" : undefined}
        className={busy ? "motion-safe:animate-pulse" : undefined} />
      {lines.map((l, i) => (
        <text key={i} x={8} y={14 + i * 13} fontSize={11} fill={pending ? "#8b95a3" : "#e3e7ec"} fontFamily="Geist Mono, monospace">{l}</text>
      ))}
      {sub && <text x={8} y={14 + lines.length * 13 + 1} fontSize={10} fill={STROKE[tone] === STROKE.muted ? "#8b95a3" : STROKE[tone]}
        fontFamily="Geist, sans-serif">{sub}</text>}
    </g>
  );
}

/** What the agent is doing now, in words, from the latest event. Same answer for a replay and a live run. */
function activity(s: RunState): { text: string; tone: Tone; busy: boolean } {
  const last = s.events[s.events.length - 1];
  const fam = last?.family ? label(String(last.family)) : "";
  if (s.status === "error") return { text: s.message || "The run stopped with an error.", tone: "crit", busy: false };
  if (s.status === "done" || last?.kind === "done" || last?.kind === "end") {
    const v = Object.values(s.finals).map((f) => String(f.verdict));
    const proven = v.filter((x) => x.startsWith("proven")).length;
    return { text: `Finished: ${proven} of ${v.length} metrics proven on the device.`, tone: proven ? "ok" : "warn", busy: false };
  }
  if (!last || last.kind === "start")
    return { text: `${s.model ?? "The model"} is reading the MIB text${s.objects ? ` (${s.objects} objects)` : ""} to pick one object per metric.`, tone: "info", busy: true };
  switch (last.kind) {
    case "read": return { text: "Picks made. Testing the first one on the device.", tone: "info", busy: true };
    case "verify-start": return { text: `${fam}: checking ${last.object ?? "the pick"} exists and has the right type and range.`, tone: "info", busy: true };
    case "sample": {
      const what = last.phase === "load" ? "load test running, watching the value move"
        : last.phase === "base" ? "measuring the quiet baseline"
        : last.phase === "witness" ? "comparing with a second object" : "watching the value";
      return { text: `${fam}: ${what}.`, tone: "info", busy: true };
    }
    case "verify": {
      const [v, tone] = verdictLabel(last.verdict);
      const next = last.verdict === "rejected" ? " Asking the model again with the evidence." : "";
      return { text: `${fam}: ${last.object ?? "pick"} ${v}.${next}`, tone, busy: !!next };
    }
    case "repick": return { text: `${fam}: the model is re-reading the MIB with what the test showed.`, tone: "warn", busy: true };
    case "no-pick": return { text: `${fam}: the device has no object for this metric.`, tone: "muted", busy: true };
    case "final": return { text: `${fam}: ${verdictLabel(last.verdict)[0]}.`, tone: verdictLabel(last.verdict)[1], busy: true };
    case "prompt": case "prompt-wait": return { text: "Waiting for you to start the load by hand.", tone: "warn", busy: true };
    default: return { text: "Working…", tone: "info", busy: true };
  }
}

function StatusLine() {
  const s = useStore(run, (r) => r);
  const [, tick] = useState(0);
  const live = s.origin === "live" && s.status === "running";
  useEffect(() => { if (!live) return; const id = setInterval(() => tick((n) => n + 1), 1000); return () => clearInterval(id); }, [live]);
  const a = activity(s);
  const lastT = s.events.length ? s.events[s.events.length - 1].t : undefined;
  const elapsed = live && s.started ? (Date.now() - s.started) / 1000 : s.status === "running" ? lastT : s.stats.seconds ?? lastT;
  return (
    <div className="flex items-center gap-2 px-3 h-8 border-b border-border/60 text-[13px]" aria-live="polite">
      <span className={a.busy ? "motion-safe:animate-pulse" : undefined}><Dot tone={a.tone} /></span>
      <span className="text-fg truncate">{a.text}</span>
      <span className="ml-auto text-2xs text-faint font-mono shrink-0">{secs(elapsed)}</span>
    </div>
  );
}

export function RunGraph() {
  const { device, modules, model, attempts, finals, noPick, kind, planned, status } = useStore(run, (s) => ({
    device: s.device, modules: s.modules, model: s.model, attempts: s.attempts, finals: s.finals, noPick: s.noPick,
    kind: s.kind, planned: s.planned, status: s.status,
  }));
  const selected = useStore(ui, (s) => s.selected);

  // Metrics in a stable order: the ones the run set out to find, then any others it reported.
  const families = useMemo(() => [...new Set([...planned, ...attempts.map((a) => a.family), ...Object.keys(finals)])],
    [planned, attempts, finals]);
  if (kind !== "onboarding") return null;

  // Left: what goes in (device, MIB text, model), stacked. Right: one row per metric, picks left to right, then the verdict.
  const maxAttempts = Math.max(1, ...attempts.map((a) => a.attempt));
  const MOD_H = 24, GAP = 10;
  const leftH = DEVICE_H + GAP + Math.max(1, modules.length) * (MOD_H + 4) - 4 + GAP + NODE_H;
  const rightH = Math.max(1, families.length) * ROW - (ROW - NODE_H);
  const height = TOP + Math.max(leftH, rightH) + 8;
  const xPick = (n: number) => LEFT_W + LEAD + (n - 1) * COL;
  const xFinal = xPick(maxAttempts + 1);
  const width = xFinal + NODE_W;
  const leftTop = TOP + (Math.max(leftH, rightH) - leftH) / 2;
  const rowY = (r: number) => TOP + (Math.max(leftH, rightH) - rightH) / 2 + r * ROW;
  const yDevice = leftTop, yMods = yDevice + DEVICE_H + GAP;
  const yModel = yMods + Math.max(1, modules.length) * (MOD_H + 4) - 4 + GAP;
  const cy = (y: number, h = NODE_H) => y + h / 2;
  const edge = (x1: number, y1: number, x2: number, y2: number, tone: Tone, bold = false, dashed = false) => (
    <path key={`${x1}-${y1}-${x2}-${y2}`} d={`M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`}
      fill="none" stroke={STROKE[tone]} strokeWidth={bold ? 2 : 1} strokeDasharray={dashed ? "4 3" : undefined} opacity={bold ? 1 : 0.7} />
  );
  const tone = (a: Attempt) => verdictLabel(a.verdict)[1];
  const running = status === "running";

  return (
    <div className="border-b border-border bg-bg shrink-0">
      <StatusLine />
      <div className="overflow-auto max-h-[300px]">
        <svg width={width + 16} height={height} className="block" role="img" aria-label="Run graph">
          <g transform="translate(8,0)">
            {[[0, "Device, MIB text, model"], [xPick(1), "First pick"],
              ...Array.from({ length: maxAttempts - 1 }, (_, i) => [xPick(i + 2), `Asked again (${i + 2})`]),
              [xFinal, "Verdict"]].map(([x, h]) => (
              <text key={String(h)} x={Number(x)} y={15} fontSize={11} fill="#5d6672" fontFamily="Geist, sans-serif">{h}</text>
            ))}
            {/* the inputs, joined top to bottom */}
            <line x1={LEFT_W / 2} y1={yDevice + DEVICE_H} x2={LEFT_W / 2} y2={yModel} stroke={STROKE.muted} opacity={0.7} />
            <Node x={0} y={yDevice} w={LEFT_W} h={DEVICE_H} title={device || "device"} sub="read-only SNMP" tone="info" />
            {modules.map((m, i) => (
              <g key={m} transform={`translate(12,${yMods + i * (MOD_H + 4)})`}>
                <title>{`${m}: MIB text the model reads`}</title>
                <rect width={LEFT_W - 24} height={MOD_H} rx={3} fill="#111418" stroke={STROKE.muted} />
                <text x={8} y={16} fontSize={10.5} fill="#c4cad2" fontFamily="Geist Mono, monospace">{m}</text>
              </g>
            ))}
            <Node x={0} y={yModel} w={LEFT_W} title={model ?? "model"} sub="reads the MIB, picks objects" tone="info"
              busy={running && attempts.length === 0} />
            {families.map((f, r) => {
              const list = attempts.filter((a) => a.family === f).sort((a, b) => a.attempt - b.attempt);
              const y = rowY(r);
              const fin = finals[f];
              const finTone = verdictLabel(fin?.verdict)[1];
              const last = list[list.length - 1];
              const from = last ? [xPick(last.attempt) + NODE_W, cy(y)] : [LEFT_W, cy(yModel)];
              return (
                <g key={f}>
                  {list.length > 0 && edge(LEFT_W, cy(yModel), xPick(list[0].attempt), cy(y), tone(list[0]))}
                  {list.slice(1).map((a, i) => edge(xPick(list[i].attempt) + NODE_W, cy(y), xPick(a.attempt), cy(y), "warn", false, true))}
                  {fin && edge(from[0], from[1], xFinal, cy(y), finTone, String(fin.verdict).startsWith("proven"))}
                  {list.map((a) => (
                    <Node key={a.key} x={xPick(a.attempt)} y={y} title={a.object} tone={tone(a)}
                      sub={a.verdict ? `${label(f)} · ${verdictLabel(a.verdict)[0]}` : `${label(f)} · testing now`}
                      busy={!a.verdict && running} active={selected === a.key} onClick={() => ui.set({ selected: a.key })} />
                  ))}
                  {fin
                    ? <Node x={xFinal} y={y} title={label(f)} tone={finTone}
                        sub={verdictLabel(fin.verdict)[0] + (noPick.includes(f) && fin.verdict !== "not-supported" ? " · no object found" : "")} />
                    : <Node x={xFinal} y={y} title={label(f)} tone="muted" pending sub={running ? "waiting" : "no verdict"} />}
                </g>
              );
            })}
          </g>
        </svg>
      </div>
    </div>
  );
}
