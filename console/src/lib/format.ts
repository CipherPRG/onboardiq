import type { Verdict, Level } from "./types";

export type Tone = "ok" | "warn" | "crit" | "muted" | "info";

const VERDICT: Record<string, [string, Tone]> = {
  "proven-experiment": ["proven · experiment", "ok"],
  "proven-witness": ["proven · witness", "ok"],
  "proven-static": ["proven · static", "ok"],
  unproven: ["unproven", "warn"],
  "not-supported": ["not on device", "muted"],
  rejected: ["rejected", "crit"],
};

export const verdictLabel = (v?: Verdict | string): [string, Tone] =>
  v ? VERDICT[v] ?? [v, "muted"] : ["testing", "info"];

export const levelTone = (l?: Level): Tone => (l === "critical" ? "crit" : l === "warning" ? "warn" : l === "ok" ? "ok" : "muted");

export function num(v: number | null | undefined, unit?: string): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (unit === "bytes") {
    if (Math.abs(v) >= 1e9) return `${(v / 1e9).toFixed(2)} GB`;
    if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)} MB`;
    return `${Math.round(v).toLocaleString()} B`;
  }
  if (unit === "percent") return `${Math.round(v)}%`;
  if (Math.abs(v) >= 1000) return Math.round(v).toLocaleString();
  return Number(v.toPrecision(3)).toString();
}

export const secs = (s?: number) => (s === undefined ? "–" : s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m${Math.round(s % 60).toString().padStart(2, "0")}s`);

export const FAMILY_LABEL: Record<string, string> = {
  cpu: "CPU", memory: "Memory", temperature: "Temperature", fan: "Fan", cpu_load: "CPU load", disk: "Disk",
};
