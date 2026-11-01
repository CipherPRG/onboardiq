// Left pane: recorded sessions (everywhere), onboarded devices (local server) and what this deployment can do.
import { useState } from "react";
import { app, run, ui, useStore } from "../lib/store";
import { replay } from "../lib/control";
import { verdictLabel } from "../lib/format";
import { utcStamp } from "../lib/time";
import { Dot, PanelHeader, cn } from "./ui";
import type { Onboarded, SessionIndexEntry } from "../lib/types";

const OWN = /-(\d{8})-(\d{6})$/;            // recordings your own runs saved carry their start time in the name

/** The recording an onboarding saved: named in the result, or (older results) the one with the same start time. */
function recordingOf(o: Onboarded, ids: string[]) {
  if (o.recording && ids.includes(o.recording)) return o.recording;
  const stamp = o.name.replace(/^onboard_/, "").replace("_", "-");
  return ids.find((id) => id.endsWith(stamp)) ?? null;
}

export function Sidebar({ current, onPick }: { current: string | null; onPick: (id: string) => void }) {
  const { mode, index, onboarded, status } = useStore(app, (s) => ({ mode: s.mode, index: s.index, onboarded: s.onboarded, status: s.status }));
  const origin = useStore(run, (s) => s.origin);
  const [mineOpen, setMineOpen] = useState(false);
  const examples = index.filter((s) => !OWN.test(s.id));
  const mine = index.filter((s) => OWN.test(s.id));

  const item = (s: SessionIndexEntry) => {
    const verdicts = Object.values(s.summary ?? {});
    const own = OWN.test(s.id);
    const active = origin === "replay" && current === s.id;
    const when = own ? utcStamp(s.recorded) : s.recorded?.slice(0, 10);
    return (
      <li key={s.id}>
        <button onClick={() => { onPick(s.id); void replay(s.id); }} aria-current={active || undefined} title={`${s.highlight ?? s.id}: ${s.device}`}
          className={cn("w-full text-left px-2 py-1.5 border-b border-border/60 hover:bg-hover block",
            active && "bg-raise shadow-[inset_2px_0_0_#4c8dff]")}>
          <span className="block text-xs text-fg leading-4">{s.highlight ?? s.id}</span>
          <span className="flex items-center gap-1 mt-0.5 text-2xs text-faint min-w-0">
            {s.kind === "health" ? <span className="shrink-0">monitor ·</span> : verdicts.map((v, i) => <Dot key={i} tone={verdictLabel(v)[1]} />)}
            <span className="truncate ml-0.5">{own ? when : `${s.device} · ${when}`}</span>
          </span>
        </button>
      </li>
    );
  };

  return (
    <aside className="w-64 shrink-0 border-r border-border bg-panel flex flex-col min-h-0" aria-label="Sessions and devices">
      <div className="overflow-auto min-h-0 flex-1">
        <PanelHeader title="Example runs"><span className="text-2xs text-faint">{examples.length}</span></PanelHeader>
        <ul>{examples.map(item)}</ul>
        {mine.length > 0 && (
          <details open={mineOpen} onToggle={(e) => setMineOpen((e.target as HTMLDetailsElement).open)}>
            <summary className="flex items-center h-7 px-2 border-y border-border bg-raise text-xs font-semibold cursor-pointer select-none">
              Your runs<span className="ml-auto text-2xs text-faint font-normal">{mine.length}</span>
            </summary>
            <ul>{mine.map(item)}</ul>
          </details>
        )}
      </div>

      {mode === "local" && (
        <>
          <PanelHeader title="Onboarded devices"><span className="text-2xs text-faint">{onboarded.length}</span></PanelHeader>
          <ul className="overflow-auto max-h-48">
            {onboarded.length === 0 && <li className="px-2 py-2 text-2xs text-muted">None yet. Onboard a device to monitor it.</li>}
            {onboarded.map((o) => {
              const proven = Object.values(o.summary).filter((v) => String(v).startsWith("proven")).length;
              const rec = recordingOf(o, index.map((s) => s.id));
              return (
                <li key={o.name} className="px-2 py-1.5 border-b border-border/60">
                  <span className="block text-xs truncate">{o.device || o.modules}</span>
                  <span className="block text-2xs text-muted font-mono">{o.host ?? "localhost"} · {proven}/{Object.keys(o.summary).length} proven · {o.name.slice(8, 16)} {o.name.slice(17, 19)}:{o.name.slice(19, 21)}</span>
                  <span className="flex gap-1 mt-1">
                    {rec && <button className="h-5 px-1.5 text-2xs rounded-sm border border-border hover:bg-hover"
                      onClick={() => { onPick(rec); void replay(rec); }}>Replay onboarding</button>}
                    <button className="h-5 px-1.5 text-2xs rounded-sm border border-border hover:bg-hover"
                      onClick={() => ui.set({ dialog: "monitor" })}>Monitor</button>
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      )}

      <div className="border-t border-border p-2 text-2xs text-muted leading-4">
        {mode === "static" && "Static hosting: recordings of real runs only. Run the container to onboard your own device."}
        {mode === "sandbox" && (status?.paused ? "The live demo is paused by its owner. Recordings still play."
          : `Live runs left on this server: ${status?.runs_left ?? "–"}. ${status?.per_visitor ?? "–"} per visitor per day, ${status?.cooldown_s ?? "–"} s between runs. The target and MIB files are fixed. Monitor works after an onboarding: four minutes of live health with a CPU and memory load in the middle.`)}
        {mode === "local" && "Connected to your local server: any device you can reach, your MIB files, live monitoring."}
      </div>
    </aside>
  );
}
