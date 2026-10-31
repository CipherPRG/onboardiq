// A drag handle that resizes the panel next to it. Sizes are remembered per viewer (browser storage, best effort).
import { useCallback, useState, type PointerEvent as ReactPointerEvent } from "react";

function remembered(key: string, fallback: number) {
  try { const v = Number(localStorage.getItem(key)); return v > 0 ? v : fallback; } catch { return fallback; }
}

export function useSize(key: string, fallback: number, min: number, max: number) {
  const [size, setSize] = useState(() => Math.min(max, Math.max(min, remembered(key, fallback))));
  const set = useCallback((v: number) => {
    const next = Math.min(max, Math.max(min, v));
    setSize(next);
    try { localStorage.setItem(key, String(next)); } catch { /* storage unavailable: the size just is not kept */ }
  }, [key, min, max]);
  return [size, set] as const;
}

/** axis "x": drag left/right (the panel is on the right); axis "y": drag up/down (the panel is below). */
export function Splitter({ axis, size, onSize }: { axis: "x" | "y"; size: number; onSize: (v: number) => void }) {
  const start = (e: ReactPointerEvent) => {
    e.preventDefault();
    const from = axis === "x" ? e.clientX : e.clientY;
    const base = size;
    const move = (m: PointerEvent) => onSize(base - ((axis === "x" ? m.clientX : m.clientY) - from));
    const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
  };
  return (
    <div role="separator" aria-orientation={axis === "x" ? "vertical" : "horizontal"} onPointerDown={start}
      className={axis === "x" ? "w-1 shrink-0 cursor-col-resize bg-border/40 hover:bg-accent/60"
        : "h-1 shrink-0 cursor-row-resize bg-border/40 hover:bg-accent/60"} />
  );
}
