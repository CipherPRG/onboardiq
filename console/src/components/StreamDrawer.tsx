// Bottom drawer: every event exactly as it arrived (live stream or recording), filterable, with JSON export.
import { useEffect, useMemo, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { run, ui, useStore } from "../lib/store";
import { matchEvent, parse } from "../lib/query";
import { exportJson } from "../lib/control";
import { Button, PanelHeader, cn } from "./ui";

const KIND: Record<string, string> = {
  verify: "text-ok", "verify-start": "text-info", repick: "text-warn", error: "text-crit", "poll-error": "text-crit",
  health: "text-fg", sample: "text-faint", end: "text-ok", done: "text-ok", prompt: "text-warn",
};

export function StreamDrawer({ height }: { height: number }) {
  const events = useStore(run, (s) => s.events);
  const { query, wrap, follow, drawer } = useStore(ui, (s) => ({ query: s.query, wrap: s.wrap, follow: s.follow, drawer: s.drawer }));
  const q = useMemo(() => parse(query), [query]);
  const shown = useMemo(() => events.filter((e) => matchEvent(e, q)), [events, q]);
  const scroller = useRef<HTMLDivElement>(null);
  const virtual = useVirtualizer({ count: shown.length, getScrollElement: () => scroller.current, estimateSize: () => (wrap ? 48 : 18), overscan: 20 });

  useEffect(() => { if (follow && drawer && shown.length) virtual.scrollToIndex(shown.length - 1, { align: "end" }); }, [shown.length, follow, drawer, virtual]);

  return (
    <section className="flex flex-col min-h-0 bg-bg border-t border-border" style={{ height: drawer ? height : 28 }} aria-label="Event stream">
      <PanelHeader title={<>Event stream <span className="text-faint font-normal">{shown.length}{shown.length !== events.length && ` of ${events.length}`}</span></>}>
        {drawer && (
          <>
            <label className="flex items-center gap-1 text-2xs text-muted"><input type="checkbox" checked={wrap} onChange={(e) => ui.set({ wrap: e.target.checked })} /> wrap</label>
            <label className="flex items-center gap-1 text-2xs text-muted ml-2"><input type="checkbox" checked={follow} onChange={(e) => ui.set({ follow: e.target.checked })} /> follow</label>
            <Button className="ml-2" onClick={exportJson} disabled={!events.length}>Export JSON</Button>
          </>
        )}
        <Button variant="ghost" onClick={() => ui.set({ drawer: !drawer })} aria-expanded={drawer}>{drawer ? "Hide" : "Show"}</Button>
      </PanelHeader>
      {drawer && (
        <div ref={scroller} className="overflow-auto flex-1 min-h-0 font-mono text-2xs">
          <div className="relative" style={{ height: virtual.getTotalSize() }}>
            {virtual.getVirtualItems().map((v) => {
              const e = shown[v.index];
              const { seq, kind, t, ...rest } = e;
              return (
                <div key={seq} ref={virtual.measureElement} data-index={v.index}
                  className={cn("absolute left-0 w-full flex gap-2 px-2 py-px hover:bg-hover", !wrap && "whitespace-nowrap")}
                  style={{ transform: `translateY(${v.start}px)` }}>
                  <span className="text-faint w-10 shrink-0 text-right">{seq}</span>
                  <span className="text-faint w-12 shrink-0 text-right">{t !== undefined ? `${Number(t).toFixed(1)}s` : ""}</span>
                  <span className={cn("w-24 shrink-0", KIND[kind] ?? "text-muted")}>{kind}</span>
                  <span className={cn("text-muted min-w-0", wrap ? "break-all whitespace-pre-wrap" : "truncate")}>{JSON.stringify(rest)}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
