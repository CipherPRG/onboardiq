// The top bar: where you are (mode and source), the filter, playback for recordings and the run actions.
import { app, run, ui, useStore } from "../lib/store";
import { attach, loadNow, replay, startRun, stopReplay, stopRun } from "../lib/control";
import { localStamp } from "../lib/time";
import { CommandBar } from "./CommandBar";
import { Button, Dot, Pill } from "./ui";

const MODE = { static: ["Replay only", "muted"], sandbox: ["Hosted demo", "info"], local: ["Local server", "ok"] } as const;
const showError = (e: Error) => run.set({ message: e.message });
const SPEEDS: [number, string][] = [[1, "1×"], [4, "4×"], [16, "16×"], [0, "instant"]];

export function TopBar({ lastReplay }: { lastReplay: string | null }) {
  const mode = useStore(app, (s) => s.mode);
  const paused = useStore(app, (s) => s.status?.paused);
  const serverRun = useStore(app, (s) => s.status?.run);
  const { origin, status, kind, loadUntil, recorded } = useStore(run, (s) => ({ origin: s.origin, status: s.status, kind: s.kind,
    loadUntil: s.loadUntil, recorded: s.source.startsWith("Replay") ? s.source.replace(/^Replay · recorded /, "") : "" }));
  const { speed, uiPaused } = useStore(ui, (s) => ({ speed: s.speed, uiPaused: s.paused }));
  const [label, tone] = MODE[mode];
  const liveRunning = origin === "live" && status === "running";

  return (
    <header className="flex items-center gap-3 h-9 px-3 border-b border-border bg-panel">
      <a href="../" className="text-sm font-semibold tracking-tight text-fg no-underline">OnboardIQ</a>
      <span className="text-faint">/</span>
      <span className="text-xs text-muted">console</span>
      <Pill tone={tone}><Dot tone={tone} />&nbsp;{label}</Pill>
      {origin === "replay" && <Pill tone="info" className="!h-6 !text-xs">Recording from {recorded ? localStamp(recorded) : "an earlier run"}, not live</Pill>}
      {origin === "live" && (
        <Pill tone="crit" className="!h-6 !text-xs"><Dot tone="crit" />&nbsp;
          {status === "running" ? "Live now" : "Live run, ended"}{mode === "sandbox" ? " on the demo device" : ""}
        </Pill>
      )}
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
        {serverRun && !liveRunning && (
          <Button variant="primary" onClick={() => attach(serverRun)}>
            Watch live {serverRun.kind === "monitor" ? "monitor" : "run"}
          </Button>
        )}
        {liveRunning && kind === "onboarding" && <span className="text-2xs text-muted">Live onboarding · ends by itself</span>}
        {liveRunning && kind === "health" && mode === "local" && (
          <>
            <Button onClick={() => loadNow("cpu").catch(showError)} disabled={Date.now() < loadUntil}>CPU load 120 s</Button>
            <Button onClick={() => loadNow("memory").catch(showError)} disabled={Date.now() < loadUntil}
              title="Holds about 90% of this machine's memory for 120 s; refused when there is not enough room">Memory load 120 s</Button>
            <Button onClick={() => loadNow("both").catch(showError)} disabled={Date.now() < loadUntil}
              title="CPU and memory load together, for 120 s">CPU + memory 120 s</Button>
          </>
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
