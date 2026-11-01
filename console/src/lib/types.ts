// Shapes of the data the OnboardIQ server streams and the recorded sessions store. Fields are optional where the
// recorded and live forms differ; nothing here is invented client-side.

export type Family = "cpu" | "memory" | "temperature" | "fan";
export type Verdict = "proven-experiment" | "proven-witness" | "proven-static" | "unproven" | "not-supported" | "rejected";
export type Level = "ok" | "warning" | "critical";

export interface Check { check: string; ok: boolean | null; detail: string }
export interface Series { base: [number, number][]; load: [number, number][]; counter?: boolean; rows?: number }
export interface Pick {
  object_name?: string; oid?: string; evidence?: string; under_load?: string; value_type?: string;
  canonical_unit?: string; raw_unit?: string; scale_to_canonical?: number | null; confidence?: number;
}

/** One event as it arrived: from the live stream or from a recording. */
export interface RunEvent {
  kind: string;
  t?: number;
  family?: Family | null;
  attempt?: number;
  object?: string;
  oid?: string;
  pick?: Pick;
  verdict?: Verdict;
  evidence?: Check[];
  series?: Series;
  reasoning?: string;
  attempts?: number;
  summary?: Record<string, Verdict> | string;   // verdicts per metric (runs) or a one-line summary (health polls)
  phase?: string;
  rows?: Record<string, number | null>;
  message?: string;
  text?: string;
  [key: string]: unknown;
}

export interface FamilyScore { score: number; worst?: string; why?: string; rows: number; flagged?: number; partial?: boolean }
export interface Explanation { headline?: string; what_happened?: string; why?: string; check?: string; text?: string; source?: string }
export interface HealthSample {
  at?: string; score: number | null; level?: Level; capped_by?: string; summary?: string; note?: string; coverage?: string;
  families?: Record<string, FamilyScore>; anomalies?: string[]; events?: string[]; explanation?: Explanation;
  alert?: { via: string; subject: string; file?: string; smtp_error?: string }; load?: boolean;
}

export interface SessionIndexEntry {
  id: string; device: string; highlight?: string; summary?: Record<string, Verdict>; recorded?: string; kind?: "health";
}
export interface OnboardingSession {
  id: string; device: string; modules: string; objects: number; model: string; recorded: string; highlight?: string;
  summary: Record<string, Verdict>; seconds?: number; snmp_calls?: number; events: RunEvent[]; lab?: boolean;
}
export interface HealthSession {
  id: string; kind: "health"; device: string; recorded: string; highlight?: string; policy?: string; interval: number;
  samples: HealthSample[]; not_scored?: { family: string; row?: string; reason: string }[];
}
export type Session = OnboardingSession | HealthSession;

export type Mode = "static" | "sandbox" | "local";
export interface ServerStatus {
  live?: boolean; sandbox?: boolean; model?: string; runs_left?: number; per_visitor?: number; cooldown_s?: number;
  paused?: boolean; mib_dir_found?: boolean; running?: boolean; mail?: boolean; monitor_ready?: boolean;
  run?: { id: string; kind: "onboard" | "monitor"; device?: string | null };   // local server: the run in progress
}
export interface Onboarded { name: string; device?: string; modules?: string; host?: string; port?: number | null; summary: Record<string, Verdict>; recording?: string | null }
