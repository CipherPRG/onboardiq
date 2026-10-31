// The top bar: where you are (mode and source), the filter, playback for recordings and the run actions.
import { app, run, ui, useStore } from "../lib/store";
import { loadCpu, replay, startRun, stopReplay, stopRun } from "../lib/control";
import { CommandBar } from "./CommandBar";
import { Button, Dot, Pill } from "./ui";

const MODE = { static: ["Replay only", "muted"], sandbox: ["Hosted demo", "info"], local: ["Local server", "ok"] } as const;
const SPEEDS: [number, string][] = [[1, "1×"], [4, "4×"], [16, "16×"], [0, "instant"]];

export function TopBar({ lastReplay }: { lastReplay: string | null }) {
  const mode = useStore(app, (s) => s.mode);
  const paused = useStore(app, (s) => s.status?.paused);
  const { origin, status, kind, loadUntil } = useStore(run, (s) => ({ origin: s.origin, status: s.status, kind: s.kind, loadUntil: s.loadUntil }));
  const { speed, uiPaused } = useStore(ui, (s) => ({ speed: s.speed, uiPaused: s.paused }));
  const [label, tone] = MODE[mode];
  const liveRunning = origin === "live" && status === "running";

  return (
    <header className="flex items-center gap-3 h-9 px-3 border-b border-border bg-panel">
      <a href="../" className="text-sm font-semibold tracking-tight text-fg no-underline">OnboardIQ</a>
      <span className="text-faint">/</span>
      <span className="text-xs text-muted">console</span>
      <Pill tone={tone}><Dot tone={tone} />&nbsp;{label}</Pill>
      <CommandBar />
      <div className="ml-auto flex items-center gap-1.5">
        {origin === "replay" && (
          <>
            <Button onClick={() => ui.set({ paused: !uiPaused })} disabled={status !== "running"} aria-label={uiPaused ? "Resume" : "Pause"}>
              {uiPaused ? "Resume" : "Pause"}
            </Button>
            <Button onClick={() => lastReplay && replay(lastReplay)} disabled={!lastReplay}>Restart</Button>
            <select aria-label="Replay speed" value={speed} onChange={(e) => ui.set({ speed: Number(e.target.value) })} className="!w-auto !h-6 text-xs">
              {SPEEDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            {status === "running" && <Button variant="ghost" onClick={stopReplay}>Skip to end</Button>}
          </>
        )}
        {liveRunning && kind === "health" && mode === "local" && (
          <Button onClick={() => loadCpu()} disabled={Date.now() < loadUntil}>CPU load 120 s</Button>
        )}
        {liveRunning && kind === "health" && <Button variant="danger" onClick={() => stopRun()}>Stop</Button>}
        {mode === "local" && (
          <>
            <Button onClick={() => ui.set({ dialog: "monitor" })} disabled={liveRunning}>Monitor…</Button>
            <Button variant="primary" onClick={() => ui.set({ dialog: "onboard" })} disabled={liveRunning}>Onboard device…</Button>
          </>
        )}
        {mode === "sandbox" && (
          <Button variant="primary" disabled={liveRunning || paused}
            onClick={() => startRun({}, "Demo device").catch((e: Error) => run.set({ status: "error", message: e.message }))}>
            Run live on demo device
          </Button>
        )}
      </div>
    </header>
  );
}
