// Every object the model picked, one row per attempt: what it predicted, what the device measured, the verdict.
// Fixed header, fixed column widths, virtualized rows, sortable, filtered by the command bar.
import { useMemo, useRef, useState } from "react";
import { createColumnHelper, flexRender, getCoreRowModel, getSortedRowModel, useReactTable, type SortingState } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { run, ui, useStore, type Attempt } from "../lib/store";
import { matchAttempt, parse } from "../lib/query";
import { FAMILY_LABEL, num, verdictLabel } from "../lib/format";
import { seriesFromSamples, summarize } from "../lib/series";
import { PanelHeader, Pill, cn } from "./ui";

interface Row extends Attempt { base: number | null; load: number | null; change: number | null; passed: number; total: number; unit?: string }

const col = createColumnHelper<Row>();
const mono = "font-mono tabular-nums";
const columns = [
  col.accessor("family", { header: "Metric", size: 132, cell: (c) => (
    <span>{FAMILY_LABEL[c.getValue()] ?? c.getValue()}{c.row.original.attempt > 1 && <span className="text-warn text-2xs"> · try {c.row.original.attempt}</span>}</span>) }),
  col.accessor("object", { header: "Object", size: 200, cell: (c) => <span className={cn(mono, "text-fg")} title={`${c.getValue()} (${c.row.original.oid})`}>{c.getValue()}</span> }),
  col.accessor("base", { header: "At rest", size: 96, cell: (c) => <span className={mono}>{num(c.getValue(), c.row.original.unit)}</span> }),
  col.accessor("load", { header: "Under load", size: 96, cell: (c) => <span className={mono}>{num(c.getValue(), c.row.original.unit)}</span> }),
  col.accessor("change", {
    header: "Change", size: 84, cell: (c) => {
      const v = c.getValue();
      // large rises read better as a factor (×503) than as a percentage (+50,200%)
      const text = v === null ? "–" : v >= 9 ? `×${Math.round(1 + v).toLocaleString()}` : `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
      return <span className={cn(mono, v === null ? "text-faint" : v > 0 ? "text-ok" : "text-crit")}>{text}</span>;
    },
  }),
  col.accessor((r) => r.verdict ?? "", { id: "verdict", header: "Verdict", size: 160,
    cell: (c) => { const [l, t] = verdictLabel(c.row.original.verdict); return <Pill tone={t}>{l}</Pill>; } }),
];

export function ObjectTable() {
  const attempts = useStore(run, (s) => s.attempts);
  const { query, selected } = useStore(ui, (s) => ({ query: s.query, selected: s.selected }));
  const [sorting, setSorting] = useState<SortingState>([]);
  const q = useMemo(() => parse(query), [query]);

  const data = useMemo<Row[]>(() => attempts.filter((a) => matchAttempt(a, q)).map((a) => {
    const series = a.series ?? (a.live.length > 1 ? seriesFromSamples(a.live, a.pick) : undefined);
    const ev = a.evidence ?? [];
    return { ...a, ...summarize(series), passed: ev.filter((c) => c.ok === true).length, total: ev.filter((c) => c.ok !== null).length,
      unit: a.pick.canonical_unit };
  }), [attempts, q]);

  const table = useReactTable({ data, columns, state: { sorting }, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(), getRowId: (r) => r.key });
  const rows = table.getRowModel().rows;
  const scroller = useRef<HTMLDivElement>(null);
  const virtual = useVirtualizer({ count: rows.length, getScrollElement: () => scroller.current, estimateSize: () => 26, overscan: 12 });
  const width = table.getTotalSize();

  return (
    <section className="flex flex-col min-h-0 flex-1" aria-label="Picked objects">
      <PanelHeader title="Objects">
        <span className="text-2xs text-faint">{rows.length}{rows.length !== attempts.length && ` of ${attempts.length}`} attempt{attempts.length === 1 ? "" : "s"}</span>
      </PanelHeader>
      <div ref={scroller} className="overflow-auto flex-1 min-h-0">
        <div className="min-w-full" style={{ width }}>
          <div className="sticky top-0 z-10 flex bg-raise border-b border-border text-2xs text-muted" role="row">
            {table.getHeaderGroups()[0].headers.map((h) => (
              <button key={h.id} style={{ width: h.getSize() }} onClick={h.column.getToggleSortingHandler()}
                className="shrink-0 text-left px-2 h-6 border-r border-border/60 hover:text-fg" role="columnheader"
                aria-sort={h.column.getIsSorted() === "asc" ? "ascending" : h.column.getIsSorted() === "desc" ? "descending" : "none"}>
                {flexRender(h.column.columnDef.header, h.getContext())}
                {{ asc: " ↑", desc: " ↓" }[h.column.getIsSorted() as string] ?? ""}
              </button>
            ))}
          </div>
          {rows.length === 0 && <p className="px-2 py-3 text-xs text-muted">{attempts.length ? "No attempt matches the filter." : "Waiting for the model's first pick…"}</p>}
          <div className="relative" style={{ height: virtual.getTotalSize() }}>
            {virtual.getVirtualItems().map((v) => {
              const r = rows[v.index];
              return (
                <div key={r.id} role="row" onClick={() => ui.set({ selected: r.id })} aria-selected={selected === r.id}
                  className={cn("absolute left-0 flex items-center h-[26px] border-b border-border/50 text-xs cursor-pointer w-full",
                    selected === r.id ? "bg-accent/10 shadow-[inset_2px_0_0_#4c8dff]" : "hover:bg-hover")}
                  style={{ transform: `translateY(${v.start}px)` }}>
                  {r.getVisibleCells().map((c) => (
                    <div key={c.id} style={{ width: c.column.getSize() }} className="shrink-0 px-2 truncate" role="cell">
                      {flexRender(c.column.columnDef.cell, c.getContext())}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}
