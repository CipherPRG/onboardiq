// The keyboard-first filter: `key:value` terms and free text. Ctrl/Cmd+K or "/" focuses it, Esc clears it.
import { useEffect, useMemo, useRef, useState } from "react";
import { ui, useStore } from "../lib/store";
import { KEYS, parse } from "../lib/query";
import { Kbd, cn } from "./ui";

export function CommandBar() {
  const query = useStore(ui, (s) => s.query);
  const ref = useRef<HTMLInputElement>(null);
  const bad = useMemo(() => parse(query).bad, [query]);
  const [open, setOpen] = useState(false);              // folded to a small button until wanted, to keep the bar calm
  const shown = open || query !== "";
  useEffect(() => { if (open) ref.current?.focus(); }, [open]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement;
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) { e.preventDefault(); setOpen(true); ref.current?.focus(); }
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, []);

  if (!shown) return (
    <button onClick={() => setOpen(true)} aria-label="Filter objects and events"
      className="flex items-center gap-2 h-6 px-2 rounded-sm border border-border bg-bg text-2xs text-muted hover:text-fg hover:bg-hover">
      Filter <Kbd>⌘K</Kbd>
    </button>
  );
  return (
    <div className={cn("flex items-center gap-2 h-6 px-2 rounded-sm border bg-bg min-w-0 flex-1 max-w-[640px]",
      bad.length ? "border-warn/70" : "border-border focus-within:border-accent")}>
      <span className="text-faint font-mono text-2xs" aria-hidden="true">›</span>
      <input ref={ref} value={query} spellCheck={false} aria-label="Filter objects and events"
        placeholder="family:cpu verdict:rejected object:ssCpu kind:verify …"
        onChange={(e) => ui.set({ query: e.target.value })}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => { if (e.key === "Escape") { ui.set({ query: "" }); setOpen(false); e.currentTarget.blur(); } }}
        className="!border-0 !h-5 !px-0 bg-transparent font-mono text-xs" />
      {bad.length > 0 && <span className="text-2xs text-warn whitespace-nowrap" title={`Keys: ${KEYS.join(", ")}`}>unknown: {bad[0]}</span>}
      <Kbd>⌘K</Kbd>
    </div>
  );
}
