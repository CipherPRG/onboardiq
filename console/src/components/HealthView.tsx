// A health run (recorded or live): the score per poll, each metric's sub-score, and the model's explanation.
import { run, useStore } from "../lib/store";
import { FAMILY_LABEL, levelTone, type Tone } from "../lib/format";
import { localClock } from "../lib/time";
import type { FamilyScore } from "../lib/types";
import { ScoreChart } from "./Charts";
import { PanelHeader, cn, toneText } from "./ui";

const BAR: Record<Tone, string> = { ok: "bg-ok", warn: "bg-warn", crit: "bg-crit", muted: "bg-faint", info: "bg-info" };
const ORDER = ["cpu", "cpu_load", "memory", "disk", "temperature", "fan"];
const toneOf = (score: number): Tone => (score >= 75 ? "ok" : score >= 40 ? "warn" : "crit");
const WORD: Record<string, string> = { ok: "Normal", warn: "High", crit: "Critical" };
const HEADLINE: Record<string, [string, string]> = {
  ok: ["Healthy", "Every measured reading is inside its normal range."],
  warning: ["Under strain", "At least one reading has left its normal range."],
  critical: ["In trouble", "At least one reading is at a critical level."],
};

/** "95% (full marks up to 70%, 0 at 100%)" -> value, unit and the limits the score uses. */
function readWhy(why = "") {
  const m = why.match(/^(-?[\d.]+)\s*(%|C|rpm)?/i);
  const ok = why.match(/full marks up to (-?[\d.]+)/);
  const zero = why.match(/0 at (-?[\d.]+)/);
  return {
    value: m ? Number(m[1]) : null, unit: m?.[2] === "C" ? "°C" : m?.[2] ?? "",
    ok: ok ? Number(ok[1]) : null, zero: zero ? Number(zero[1]) : null,
    text: m ? null : why,                       // a status or fan reading has no number scale: show its words
  };
}

function plainWorst(fam: string, f: FamilyScore) {
  const r = readWhy(f.why);
  const name = FAMILY_LABEL[fam] ?? fam;
  if (r.value === null) return `${name}: ${f.why ?? "reading not usable"}.`;
  const what = fam === "memory" || fam === "disk" ? "used" : fam === "temperature" ? "" : "busy";
  const limit = r.ok !== null ? `, normal up to ${r.ok}${r.unit}` : "";
  return `${name} ${f.rows > 1 ? "(highest of " + f.rows + ") " : ""}is at ${r.value}${r.unit}${what ? " " + what : ""}${limit}.`;
}

function MetricCard({ fam, f }: { fam: string; f: FamilyScore }) {
  const r = readWhy(f.why);
  const tone = toneOf(f.score);
  const top = Math.max(r.zero ?? 100, r.value ?? 0, 1);
  const pct = (v: number) => `${Math.min(100, Math.max(0, (v / top) * 100))}%`;
  return (
    <div className="border border-border rounded-sm bg-bg p-2.5 space-y-2">
      <div className="flex items-baseline gap-2">
        <span className="text-xs text-muted">{FAMILY_LABEL[fam] ?? fam}</span>
        <span className={cn("ml-auto text-2xs", toneText(tone))}>{WORD[tone]}</span>
      </div>
      <div className="flex items-baseline gap-1">
        <span className={cn("font-mono text-[22px] leading-none tabular-nums", toneText(tone))}>{r.value ?? "–"}{r.unit}</span>
        <span className="text-xs text-muted">{fam === "memory" || fam === "disk" ? " used" : fam === "cpu" || fam === "cpu_load" ? " busy" : ""}</span>
      </div>
      {r.value !== null && r.ok !== null ? (
        <div className="relative h-2 rounded-sm overflow-hidden bg-raise" aria-hidden="true">
          <div className="absolute inset-y-0 left-0 bg-ok/25" style={{ width: pct(r.ok) }} />
          <div className="absolute inset-y-0 bg-warn/25" style={{ left: pct(r.ok), width: `calc(${pct(top)} - ${pct(r.ok)})` }} />
          <div className={cn("absolute inset-y-0 w-[3px] -ml-px", BAR[tone])} style={{ left: pct(r.value) }} />
        </div>
      ) : <p className="text-2xs text-muted">{r.text}</p>}
      <p className="text-2xs text-faint leading-snug">
        {r.ok !== null ? `Normal up to ${r.ok}${r.unit}, worst at ${r.zero}${r.unit}. ` : ""}
        {f.rows > 1 ? `${f.rows} readings, the highest is shown.` : ""}
      </p>
    </div>
  );
}

export function HealthMain() {
  const { health, interval, device, notScored } = useStore(run, (s) => ({ health: s.health, interval: s.interval ?? 10, device: s.device, notScored: s.notScored }));
  const x = health[health.length - 1];
  const fams = Object.entries(x?.families ?? {}).sort(([a], [b]) => ORDER.indexOf(a) - ORDER.indexOf(b));
  const scored = x?.families ?? {};
  const missing = [...new Map(notScored
    .filter((n) => !n.row && !scored[n.family] && !(n.family === "cpu" && scored.cpu_load))   // CPU load covers CPU
    .map((n) => [n.family, n])).values()];
  const worst = [...fams].sort(([, a], [, b]) => a.score - b.score)[0];
  const [word, line] = HEADLINE[x?.level ?? ""] ?? ["Waiting", "The first reading arrives within one polling interval."];
  const loaded = health.some((s) => s.load);
  return (
    <section className="flex flex-col min-h-0 flex-1" aria-label="Health">
      <PanelHeader title="Device health"><span className="text-2xs text-faint truncate">{device} · a reading every {interval} s</span></PanelHeader>
      <div className="overflow-auto p-3 space-y-3">
        <div className="flex items-start gap-4">
          <div className="min-w-0">
            <p className={cn("text-[30px] leading-none font-semibold tracking-tight", toneText(levelTone(x?.level)))}>{word}</p>
            <p className="mt-1.5 text-sm text-fg/90">{x?.level && x.level !== "ok" && worst ? plainWorst(worst[0], worst[1]) : line}</p>
          </div>
          <div className="ml-auto text-right shrink-0">
            <p className="text-2xs text-muted">health score</p>
            <p className="font-mono text-sm tabular-nums"><span className={toneText(levelTone(x?.level))}>{x?.score ?? "–"}</span><span className="text-faint">/100</span></p>
            <p className="text-2xs text-faint font-mono">{localClock(x?.at)}</p>
          </div>
        </div>

        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))" }}>
          {fams.map(([k, f]) => <MetricCard key={k} fam={k} f={f} />)}
          {missing.map((n) => (
            <div key={n.family} className="border border-dashed border-border rounded-sm p-2.5 space-y-1 opacity-70">
              <span className="text-xs text-muted">{FAMILY_LABEL[n.family] ?? n.family}</span>
              <p className="text-xs text-faint">Not monitored: {n.reason}.</p>
            </div>
          ))}
        </div>

        <div className="border border-border rounded-sm bg-bg pt-1">
          <p className="px-2 pt-1 text-2xs text-muted">Health over time{loaded ? ". The shaded part is the load test." : "."}</p>
          <ScoreChart samples={health} interval={interval} />
        </div>

        <details className="border border-border rounded-sm px-2.5 py-2 text-xs">
          <summary className="cursor-pointer text-muted">How the score is worked out</summary>
          <ol className="mt-2 space-y-1 list-decimal pl-4 text-muted">
            <li>Every reading gets a score from 0 to 100. It keeps full marks while it is in the normal range shown on its card, then falls; at the worst value it reaches 0.</li>
            <li>A metric with several readings (one per CPU core, say) takes its worst one, so one overloaded core is not hidden by idle ones.</li>
            <li>The device score is the average of the metric scores. If any reading is critical the score is held at 40 or below; if one is high, at 70 or below. That is why one overloaded CPU can pull the whole device to "In trouble".</li>
            <li>75 and above reads as Healthy, 40 to 74 as Under strain, below 40 as In trouble.</li>
            <li>Only readings proven on this device during onboarding are scored. The limits come from a policy file you can edit (<span className="font-mono">health_policy.json</span>).</li>
          </ol>
        </details>
      </div>
    </section>
  );
}

export function HealthSide() {
  const { health, notScored, origin } = useStore(run, (s) => ({ health: s.health, notScored: s.notScored, origin: s.origin }));
  const told = [...health].reverse().find((s) => s.explanation);
  const x = health[health.length - 1];
  const alerts = health.filter((s) => s.alert);
  const e = told?.explanation;
  return (
    <aside className="flex flex-col min-h-0 h-full bg-panel" aria-label="Explanation">
      <PanelHeader title="What changed, in plain English">{told?.at && <span className="text-2xs text-faint font-mono">{localClock(told.at)}</span>}</PanelHeader>
      <div className="overflow-auto p-3 space-y-3 text-xs">
        {!e && <p className="text-muted">The model explains here when the level changes or something unusual appears.</p>}
        {e && (
          <div className="space-y-1.5">
            {e.headline && <p className={cn("text-sm font-medium", toneText(levelTone(told?.level)))}>{e.headline}</p>}
            {e.what_happened && <p>{e.what_happened}</p>}
            {e.why && <p className="text-muted">{e.why}</p>}
            {!e.headline && e.text && <p>{e.text}</p>}
            {e.check && <p className="border-l border-accent pl-2"><span className="text-muted">What to check: </span>{e.check}</p>}
            <p className="text-2xs text-faint">{localClock(told?.at)} · {e.source === "template" ? "written from the measured readings (template)" : `written by ${e.source ?? "the model"}; every number is checked against the readings`}</p>
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
        {alerts.length > 0 && (
          <section>
            <h3 className="text-2xs text-muted mb-1">Alerts ({alerts.length})</h3>
            <ul className="space-y-1.5">
              {alerts.map((a, i) => (
                <li key={i} className="border border-border rounded-sm bg-bg px-2 py-1.5">
                  <p className="text-2xs text-muted">{a.at ? `${localClock(a.at)} · ` : ""}{a.alert!.via === "smtp" ? "emailed"
                    : a.alert!.smtp_error ? `sending failed (${a.alert!.smtp_error}); saved as a file instead` : "not emailed: no mail account set up"}</p>
                  <p>{a.alert!.subject}</p>
                  {a.alert!.file && origin === "live" && <a className="text-2xs" href={`/api/outbox/${a.alert!.file}`} target="_blank" rel="noreferrer">Open the email as it would be sent</a>}
                </li>
              ))}
            </ul>
          </section>
        )}
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
