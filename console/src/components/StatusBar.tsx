// Bottom status line: where the data comes from, connection, counts, cost.
import { useEffect, useState } from "react";
import { app, run, useStore } from "../lib/store";
import { secs } from "../lib/format";
import { Dot } from "./ui";

export function StatusBar() {
  const mode = useStore(app, (s) => s.mode);
  const r = useStore(run, (s) => ({ source: s.source, status: s.status, connected: s.connected, events: s.events.length,
    attempts: s.attempts.length, stats: s.stats, model: s.model, started: s.started, origin: s.origin, message: s.message, polls: s.health.length }));
  const [, tick] = useState(0);
  useEffect(() => { if (r.status !== "running") return; const id = setInterval(() => tick((n) => n + 1), 1000); return () => clearInterval(id); }, [r.status]);
  const tone = r.status === "error" ? "crit" : r.status === "running" ? (r.origin === "live" ? "ok" : "info") : "muted";
  const elapsed = r.started ? (r.status === "running" || !r.stats.seconds ? (Date.now() - r.started) / 1000 : r.stats.seconds) : undefined;

  return (
    <footer className="flex items-center gap-4 h-[22px] px-3 border-t border-border bg-panel text-2xs text-muted font-mono">
      <span className="flex items-center gap-1.5"><Dot tone={tone} />{r.status}</span>
      <span>{r.source || "nothing loaded"}</span>
      {r.origin === "live" && <span>{r.connected ? "stream open" : "stream closed"}</span>}
      {r.message && <span className="text-crit truncate">{r.message}</span>}
      <span className="ml-auto">{r.events} events</span>
      {r.attempts > 0 && <span>{r.attempts} attempts</span>}
      {r.polls > 0 && <span>{r.polls} polls</span>}
      <span>{secs(elapsed)}</span>
      {r.stats.snmp_calls !== undefined && <span>{r.stats.snmp_calls} SNMP queries</span>}
      {r.stats.cost_usd !== undefined && <span>${r.stats.cost_usd.toFixed(3)}</span>}
      <span>{r.model ?? (mode === "static" ? "recordings" : "")}</span>
    </footer>
  );
}
