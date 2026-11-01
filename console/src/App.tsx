// The console layout: top bar, sessions on the left, the run in the middle (graph and table, or health),
// the inspector on the right, the raw event stream at the bottom, the status line under everything.
import { useEffect, useState } from "react";
import { app, run, ui, useStore } from "./lib/store";
import { getIndex, getOnboarded, getStatus } from "./lib/api";
import { attach, replay } from "./lib/control";
import { TopBar } from "./components/TopBar";
import { Sidebar } from "./components/Sidebar";
import { RunGraph } from "./components/RunGraph";
import { ObjectTable } from "./components/ObjectTable";
import { Inspector } from "./components/Inspector";
import { HealthMain, HealthSide } from "./components/HealthView";
import { StreamDrawer } from "./components/StreamDrawer";
import { StatusBar } from "./components/StatusBar";
import { PromptBanner } from "./components/PromptBanner";
import { MonitorDialog, OnboardDialog } from "./components/Dialogs";
import { Splitter, useSize } from "./components/Splitter";

const FIRST = "laptop_full";                         // the recording shown on arrival: a pick caught and corrected

export default function App() {
  const kind = useStore(run, (s) => s.kind);
  const dialog = useStore(ui, (s) => s.dialog);
  const [current, setCurrent] = useState<string | null>(null);
  const [side, setSide] = useSize("oiq.inspector", 420, 300, 720);
  const [drawer, setDrawer] = useSize("oiq.drawer", 220, 90, 600);

  useEffect(() => {
    (async () => {
      const status = await getStatus();
      const mode = status?.live ? (status.sandbox ? "sandbox" : "local") : "static";
      app.set({ status, mode });
      if (mode === "local") getOnboarded().then((list) => app.set({ onboarded: list })).catch(() => {});
      try {
        const index = await getIndex();
        app.set({ index });
        if (status?.run) { attach(status.run); return; }           // a live run is going: show it, not a recording
        const first = index.find((s) => s.id === FIRST) ?? index[0];
        if (first) { setCurrent(first.id); void replay(first.id); }
      } catch (e) { app.set({ error: (e as Error).message }); }
    })();
    // Keep checking the server, so a run started elsewhere (or still going after a reload) can be watched.
    const timer = setInterval(async () => {
      if (app.get().mode === "static") return;
      const status = await getStatus();
      if (!status) return;
      const was = app.get().status?.running;
      app.set({ status });
      if (was && !status.running && app.get().mode === "local")     // a run just ended: it may have onboarded a device
        getOnboarded().then((list) => app.set({ onboarded: list })).catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="h-full grid grid-rows-[36px_minmax(0,1fr)_22px] grid-cols-[minmax(0,1fr)]">
      <TopBar lastReplay={current} />
      <div className="flex min-h-0">
        <Sidebar current={current} onPick={setCurrent} />
        <main className="flex-1 min-w-0 flex flex-col">
          <PromptBanner />
          <div className="flex flex-1 min-h-0">
            <div className="flex-1 min-w-0 flex flex-col">
              {kind === "health" ? <HealthMain /> : <><RunGraph /><ObjectTable /></>}
            </div>
            <Splitter axis="x" size={side} onSize={setSide} />
            <div className="shrink-0 min-h-0 border-l border-border" style={{ width: side }}>
              {kind === "health" ? <HealthSide /> : <Inspector />}
            </div>
          </div>
          <Splitter axis="y" size={drawer} onSize={setDrawer} />
          <StreamDrawer height={drawer} />
        </main>
      </div>
      <StatusBar />
      {dialog === "onboard" && <OnboardDialog />}
      {dialog === "monitor" && <MonitorDialog />}
    </div>
  );
}
