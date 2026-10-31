// Right pane for an onboarding run: the selected pick in full: measurements, the model's claim, every check.
import { run, ui, useStore } from "../lib/store";
import { FAMILY_LABEL, num, secs, verdictLabel } from "../lib/format";
import { seriesFromSamples } from "../lib/series";
import { TraceChart } from "./Charts";
import { PanelHeader, Pill, toneText } from "./ui";

export function Inspector() {
  const attempts = useStore(run, (s) => s.attempts);
  const selected = useStore(ui, (s) => s.selected);
  const a = attempts.find((x) => x.key === selected) ?? attempts[attempts.length - 1];

  if (!a) return (
    <aside className="flex flex-col min-h-0 h-full bg-panel" aria-label="Inspector">
      <PanelHeader title="Inspector" />
      <p className="p-3 text-xs text-muted">Select a pick in the graph or the table.</p>
    </aside>
  );

  const [label, tone] = verdictLabel(a.verdict);
  const series = a.series ?? (a.live.length > 1 ? seriesFromSamples(a.live, a.pick) : undefined);
  const scale = a.pick.scale_to_canonical;

  return (
    <aside className="flex flex-col min-h-0 h-full bg-panel" aria-label="Inspector">
      <PanelHeader title={`${FAMILY_LABEL[a.family] ?? a.family} · attempt ${a.attempt}`}><Pill tone={tone}>{label}</Pill></PanelHeader>
      <div className="overflow-auto p-3 space-y-3 text-xs">
        <div>
          <p className="font-mono text-base text-fg break-all">{a.object}</p>
          <p className="font-mono text-2xs text-muted break-all">{a.oid}</p>
        </div>

        <section>
          <h3 className="text-2xs text-muted mb-1">Measured on the device{series?.counter ? " · rate per second" : ""}</h3>
          {series && (series.base.length + series.load.length) > 0
            ? <div className="border border-border rounded-sm bg-bg pt-1"><TraceChart series={series} unit={a.pick.canonical_unit} tone={a.verdict ? tone : "info"} /></div>
            : <p className="text-muted">{a.verdict ? "No time series for this check (static checks only)." : "Measuring…"}</p>}
        </section>

        <section className="grid grid-cols-[96px_1fr] gap-x-2 gap-y-1">
          <span className="text-muted">Predicted</span><span>{a.pick.under_load && a.pick.under_load !== "none" ? `${a.pick.under_load} under load` : "–"}</span>
          <span className="text-muted">Type</span><span className="font-mono">{a.pick.value_type ?? "–"}{a.pick.raw_unit ? ` · ${a.pick.raw_unit}` : ""}</span>
          <span className="text-muted">Scale</span><span className="font-mono">{scale && scale !== 1 ? `×${scale} → ${a.pick.canonical_unit}` : "none"}</span>
          <span className="text-muted">Confidence</span><span className="font-mono">{a.pick.confidence !== undefined ? num(a.pick.confidence) : "–"}</span>
          <span className="text-muted">Test time</span><span className="font-mono">{secs(a.seconds)}</span>
        </section>

        {a.pick.evidence && (
          <section>
            <h3 className="text-2xs text-muted mb-1">The MIB line the model relied on</h3>
            <blockquote className="border-l border-strong pl-2 text-muted">{a.pick.evidence}</blockquote>
          </section>
        )}

        {a.evidence && (
          <section>
            <h3 className="text-2xs text-muted mb-1">Checks</h3>
            <ul className="divide-y divide-border/60 border border-border rounded-sm">
              {a.evidence.map((c, i) => (
                <li key={i} className={`grid grid-cols-[14px_84px_1fr] gap-2 px-2 py-1 ${/experiment|witness/.test(c.check) ? "bg-raise" : ""}`}>
                  <span className={toneText(c.ok ? "ok" : c.ok === false ? "crit" : "muted")} aria-label={c.ok ? "passed" : c.ok === false ? "failed" : "information"}>
                    {c.ok ? "✓" : c.ok === false ? "✗" : "·"}</span>
                  <span className="font-mono text-fg">{c.check}</span>
                  <span className="text-muted break-words">{c.detail}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {a.reasoning && (
          <section>
            <h3 className="text-2xs text-muted mb-1">Why the model picked this after the previous attempt failed</h3>
            <p className="border-l border-accent pl-2 text-muted whitespace-pre-wrap">{a.reasoning}</p>
          </section>
        )}
      </div>
    </aside>
  );
}
