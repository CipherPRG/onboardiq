// Left pane: recorded sessions (everywhere), onboarded devices (local server) and what this deployment can do.
import { app, run, ui, useStore } from "../lib/store";
import { replay } from "../lib/control";
import { verdictLabel } from "../lib/format";
import { Dot, PanelHeader, cn } from "./ui";

export function Sidebar({ current, onPick }: { current: string | null; onPick: (id: string) => void }) {
  const { mode, index, onboarded, status } = useStore(app, (s) => ({ mode: s.mode, index: s.index, onboarded: s.onboarded, status: s.status }));
  const origin = useStore(run, (s) => s.origin);

  return (
    <aside className="w-64 shrink-0 border-r border-border bg-panel flex flex-col min-h-0" aria-label="Sessions and devices">
      <PanelHeader title="Recordings"><span className="text-2xs text-faint">{index.length}</span></PanelHeader>
      <ul className="overflow-auto min-h-0 flex-1">
        {index.map((s) => {
          const verdicts = Object.values(s.summary ?? {});
          const active = origin === "replay" && current === s.id;
          return (
            <li key={s.id}>
              <button onClick={() => { onPick(s.id); void replay(s.id); }} aria-current={active || undefined}
                className={cn("w-full text-left px-2 py-1.5 border-b border-border/60 hover:bg-hover block",
                  active && "bg-raise shadow-[inset_2px_0_0_#4c8dff]")}>
                <span className="block text-xs text-fg leading-4">{s.highlight ?? s.id}</span>
                <span className="block text-2xs text-muted truncate">{s.device}</span>
                <span className="flex items-center gap-1 mt-1">
                  {s.kind === "health"
                    ? <span className="text-2xs text-faint font-mono">health · {s.recorded?.slice(0, 10)}</span>
                    : <>
                        {verdicts.map((v, i) => <Dot key={i} tone={verdictLabel(v)[1]} />)}
                        <span className="text-2xs text-faint font-mono ml-1">{s.recorded?.slice(0, 10)}</span>
                      </>}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      {mode === "local" && (
        <>
          <PanelHeader title="Onboarded devices"><span className="text-2xs text-faint">{onboarded.length}</span></PanelHeader>
          <ul className="overflow-auto max-h-48">
            {onboarded.length === 0 && <li className="px-2 py-2 text-2xs text-muted">None yet. Onboard a device to monitor it.</li>}
            {onboarded.map((o) => {
              const proven = Object.values(o.summary).filter((v) => String(v).startsWith("proven")).length;
              return (
                <li key={o.name}>
                  <button className="w-full text-left px-2 py-1.5 border-b border-border/60 hover:bg-hover"
                    onClick={() => ui.set({ dialog: "monitor" })}>
                    <span className="block text-xs truncate">{o.device || o.modules}</span>
                    <span className="block text-2xs text-muted font-mono">{o.host ?? "localhost"} · {proven}/{Object.keys(o.summary).length} proven</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}

      <div className="border-t border-border p-2 text-2xs text-muted leading-4">
        {mode === "static" && "Static hosting: recordings of real runs only. Run the container to onboard your own device."}
        {mode === "sandbox" && (status?.paused ? "The live demo is paused by its owner. Recordings still play."
          : `Live runs left on this server: ${status?.runs_left ?? "–"}. ${status?.per_visitor ?? "–"} per visitor per day, ${status?.cooldown_s ?? "–"} s between runs. The target and MIB files are fixed.`)}
        {mode === "local" && "Connected to your local server: any device you can reach, your MIB files, live monitoring."}
      </div>
    </aside>
  );
}
