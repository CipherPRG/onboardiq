// A health run (recorded or live): the score per poll, each metric's sub-score, and the model's explanation.
import { run, useStore } from "../lib/store";
import { FAMILY_LABEL, levelTone, type Tone } from "../lib/format";
import { ScoreChart } from "./Charts";
import { PanelHeader, Pill, cn, toneText } from "./ui";

const barTone = (s: number): Tone => (s >= 75 ? "ok" : s >= 40 ? "warn" : "crit");
const BAR: Record<Tone, string> = { ok: "bg-ok", warn: "bg-warn", crit: "bg-crit", muted: "bg-faint", info: "bg-info" };

export function HealthMain() {
  const { health, interval, policy, device } = useStore(run, (s) => ({ health: s.health, interval: s.interval ?? 10, policy: s.policy, device: s.device }));
  const x = health[health.length - 1];
  return (
    <section className="flex flex-col min-h-0 flex-1" aria-label="Health">
      <PanelHeader title="Health score"><span className="text-2xs text-faint">{policy ?? "policy file"} · every {interval}s</span></PanelHeader>
      <div className="overflow-auto p-3 space-y-3">
        <div className="flex items-baseline gap-3">
          <span className={cn("font-mono text-[28px] leading-none tabular-nums", toneText(levelTone(x?.level)))}>{x?.score ?? "–"}</span>
          <span className="text-2xs text-faint font-mono">/100</span>
          {x?.level && <Pill tone={levelTone(x.level)}>{x.level}{x.capped_by ? ` · capped by ${x.capped_by}` : ""}</Pill>}
          <span className="text-2xs text-muted font-mono truncate">{x?.at ? `${x.at.slice(11, 19)} UTC` : "waiting for the first poll"} · {x?.coverage ?? ""}</span>
          <span className="ml-auto text-2xs text-muted truncate">{device}</span>
        </div>
        <div className="border border-border rounded-sm bg-bg pt-1"><ScoreChart samples={health} interval={interval} /></div>
        <table className="w-full text-xs border border-border">
          <thead className="bg-raise text-2xs text-muted">
            <tr><th className="text-left px-2 h-6 w-28">Metric</th><th className="text-left px-2 w-48">Score</th><th className="text-right px-2 w-12" /><th className="text-left px-2">Worst row and reason</th></tr>
          </thead>
          <tbody>
            {Object.entries(x?.families ?? {}).map(([k, f]) => (
              <tr key={k} className="border-t border-border/60">
                <td className="px-2 h-6">{FAMILY_LABEL[k] ?? k}</td>
                <td className="px-2"><div className="h-1.5 bg-raise rounded-sm overflow-hidden"><div className={cn("h-full", BAR[barTone(f.score)])} style={{ width: `${Math.max(2, f.score)}%` }} /></div></td>
                <td className={cn("px-2 text-right font-mono tabular-nums", toneText(barTone(f.score)))}>{Math.round(f.score)}</td>
                <td className="px-2 text-muted truncate max-w-0">{f.rows > 1 ? `worst of ${f.rows}: ${f.worst} · ` : ""}{f.why}{f.flagged ? ` · ${f.flagged} flagged` : ""}{f.partial ? " · some rows skipped" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function HealthSide() {
  const { health, notScored } = useStore(run, (s) => ({ health: s.health, notScored: s.notScored }));
  const told = [...health].reverse().find((s) => s.explanation);
  const x = health[health.length - 1];
  const e = told?.explanation;
  return (
    <aside className="flex flex-col min-h-0 h-full bg-panel" aria-label="Explanation">
      <PanelHeader title="Latest explanation">{told?.at && <span className="text-2xs text-faint font-mono">{told.at.slice(11, 19)} UTC</span>}</PanelHeader>
      <div className="overflow-auto p-3 space-y-3 text-xs">
        {!e && <p className="text-muted">The model explains here when the level changes or something unusual appears.</p>}
        {e && (
          <div className="space-y-1.5">
            {e.headline && <p className={cn("text-sm font-medium", toneText(levelTone(told?.level)))}>{e.headline}</p>}
            {e.what_happened && <p>{e.what_happened}</p>}
            {e.why && <p className="text-muted">{e.why}</p>}
            {!e.headline && e.text && <p>{e.text}</p>}
            {e.check && <p className="border-l border-accent pl-2"><span className="text-muted">What to check: </span>{e.check}</p>}
            <p className="text-2xs text-faint">{told?.at?.slice(11, 19)} UTC · {e.source === "template" ? "written from the measured readings (template)" : `written by ${e.source ?? "the model"}; every number is checked against the readings`}</p>
          </div>
        )}
        {x && ((x.anomalies?.length ?? 0) + (x.events?.length ?? 0)) > 0 && (
          <section>
            <h3 className="text-2xs text-muted mb-1">Unusual now</h3>
            <ul className="space-y-1 text-warn">
              {x.events?.map((a, i) => <li key={`e${i}`}>Event: {a}</li>)}
              {x.anomalies?.map((a, i) => <li key={`a${i}`}>{a}</li>)}
            </ul>
          </section>
        )}
        {x?.alert && <p className="text-2xs text-muted">{x.alert.via === "smtp" ? "Alert emailed" : "Alert written to the outbox"}: {x.alert.subject}</p>}
        {notScored.length > 0 && (
          <details>
            <summary className="text-2xs text-muted cursor-pointer">Not scored ({notScored.length})</summary>
            <ul className="mt-1 space-y-0.5 text-muted">{notScored.map((n, i) => <li key={i}>{FAMILY_LABEL[n.family] ?? n.family}{n.row ? ` ${n.row}` : ""}: {n.reason}</li>)}</ul>
          </details>
        )}
      </div>
    </aside>
  );
}
