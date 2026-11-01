// One store for the console. Every event, from a recording or the live stream, goes through `reduce`, so a replay
// and a live run build exactly the same state and the same panels.
import { useRef, useSyncExternalStore } from "react";
import type { Check, Family, HealthSample, Mode, Onboarded, Pick, RunEvent, Series, ServerStatus, SessionIndexEntry, Verdict } from "./types";
import type { LiveSample } from "./series";

export interface Attempt {
  key: string; family: Family; attempt: number; object: string; oid: string; pick: Pick;
  verdict?: Verdict; evidence?: Check[]; series?: Series; live: LiveSample[]; seconds?: number;
  reasoning?: string; started?: number;
}
export interface Stamped extends RunEvent { seq: number }
export type Origin = "replay" | "live";

export interface RunState {
  runId: string | null; origin: Origin | null; kind: "onboarding" | "health" | null; source: string;
  device: string; model?: string; modules: string[]; objects?: number; planned: string[];
  events: Stamped[]; attempts: Attempt[]; finals: Record<string, { verdict: Verdict; attempts?: number }>; noPick: string[];
  health: HealthSample[]; interval?: number; policy?: string; notScored: { family: string; row?: string; reason: string }[];
  status: "idle" | "running" | "done" | "error"; message?: string;
  stats: { seconds?: number; snmp_calls?: number; cost_usd?: number };
  prompt: { text: string; cmd: string; waiting: boolean } | null;
  loadUntil: number; connected: boolean; started: number;
}

export const emptyRun = (): RunState => ({
  runId: null, origin: null, kind: null, source: "", device: "", modules: [], planned: [], events: [], attempts: [], finals: {}, noPick: [],
  health: [], notScored: [], status: "idle", stats: {}, prompt: null, loadUntil: 0, connected: false, started: 0,
});

function create<T>(initial: T) {
  let state = initial;
  const subs = new Set<() => void>();
  return {
    get: () => state,
    set(patch: Partial<T> | ((s: T) => Partial<T>)) {
      state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
      subs.forEach((f) => f());
    },
    subscribe(f: () => void) { subs.add(f); return () => { subs.delete(f); }; },
  };
}

export const run = create<RunState>(emptyRun());

export interface UiState {
  selected: string | null; query: string; drawer: boolean; wrap: boolean; follow: boolean; sidebar: boolean;
  dialog: "onboard" | "monitor" | null; speed: number; paused: boolean;
}
export const ui = create<UiState>({
  selected: null, query: "", drawer: false, wrap: false, follow: true, sidebar: true, dialog: null, speed: 4, paused: false,
});

export interface AppState {
  mode: Mode; status: ServerStatus | null; index: SessionIndexEntry[]; onboarded: Onboarded[]; error: string;
}
/** A short message shown over the page (a refused click, a failed load), then dismissed. */
export const toast = create<{ text: string; id: number }>({ text: "", id: 0 });
export const notify = (text: string) => toast.set((s) => ({ text, id: s.id + 1 }));

export const app = create<AppState>({ mode: "static", status: null, index: [], onboarded: [], error: "" });

function shallowEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || !a || !b || Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  return ka.length === kb.length && ka.every((k) => Object.is((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]));
}

/** Subscribe to part of a store. A selector may return a fresh object: it is kept while shallow-equal. */
export function useStore<S, T>(store: { get: () => S; subscribe: (f: () => void) => () => void }, pick: (s: S) => T): T {
  const last = useRef<{ value: T } | null>(null);
  return useSyncExternalStore(store.subscribe, () => {
    const next = pick(store.get());
    if (last.current && shallowEqual(last.current.value, next)) return last.current.value;
    last.current = { value: next };
    return next;
  });
}

const keyOf = (ev: RunEvent) => `${ev.family}-${ev.attempt ?? 1}`;

/** Apply one event. Pure: returns the changed fields. */
export function reduce(s: RunState, ev: RunEvent): Partial<RunState> {
  const events = [...s.events, { ...ev, seq: s.events.length }];
  const patchAttempt = (key: string, f: (a: Attempt) => Attempt) => s.attempts.map((a) => (a.key === key ? f(a) : a));
  switch (ev.kind) {
    case "start":
      return { events, device: String(ev.device ?? s.device), model: ev.model as string, objects: ev.objects as number,
        modules: String(ev.modules ?? "").split(",").filter(Boolean),
        planned: Array.isArray(ev.families) ? (ev.families as string[]) : s.planned };
    case "read":
      return { events, objects: (ev.objects as number) ?? s.objects };
    case "verify-start": {
      const pick = ev.pick ?? {};
      const reread = [...s.events].reverse().find((e) => e.kind === "repick" && e.family === ev.family && e.attempt === ev.attempt);
      const a: Attempt = { key: keyOf(ev), family: ev.family as Family, attempt: ev.attempt ?? 1,
        object: ev.object ?? pick.object_name ?? "", oid: ev.oid ?? pick.oid ?? "", pick, live: [],
        reasoning: reread?.reasoning, started: ev.t };
      return { events, attempts: [...s.attempts.filter((x) => x.key !== a.key), a] };
    }
    case "sample": {                                  // live only: measurements as they are taken (not in recordings)
      const key = keyOf(ev);
      const sample = { t: ev.t ?? 0, phase: ev.phase, rows: ev.rows ?? {} };
      return { attempts: patchAttempt(key, (a) => ({ ...a, live: [...a.live, sample] })), events };
    }
    case "verify":
      return { events, attempts: patchAttempt(keyOf(ev), (a) => ({ ...a, verdict: ev.verdict, evidence: ev.evidence,
        series: ev.series, seconds: ev.seconds as number, pick: ev.pick ?? a.pick })) };
    case "no-pick":
      return { events, noPick: [...s.noPick, String(ev.family)] };
    case "final":
      return { events, finals: { ...s.finals, [String(ev.family)]: { verdict: ev.verdict as Verdict, attempts: ev.attempts } } };
    case "done":
      return { events, status: "done" };
    case "prompt": {
      const text = String(ev.text ?? "");
      const p = s.prompt ?? { text: "", cmd: "", waiting: false };
      return { events, prompt: /^(STOP|START)/i.test(text) ? { ...p, text, cmd: /^STOP/i.test(text) ? "" : p.cmd } : { ...p, cmd: text } };
    }
    case "prompt-wait":
      return { events, prompt: { ...(s.prompt ?? { text: "", cmd: "" }), waiting: true } };
    case "monitor-start":
      return { events, kind: "health", device: String(ev.device), interval: ev.interval as number, policy: ev.policy as string };
    case "load":
      return { events, loadUntil: Date.now() + Number(ev.seconds) * 1000 };
    case "end":
      return { events, status: "done", prompt: null, stats: { seconds: ev.seconds as number, snmp_calls: ev.snmp_calls as number,
        cost_usd: ev.cost_usd as number }, finals: typeof ev.summary === "object" ? Object.fromEntries(Object.entries(ev.summary).map(
        ([f, v]) => [f, { verdict: v, attempts: s.finals[f]?.attempts }])) : s.finals };
    case "error":
      return { events, status: "error", message: ev.message, prompt: null };
    default:
      return { events };
  }
}

export const dispatch = (ev: RunEvent) => {
  run.set((s) => reduce(s, ev));
  if (ev.kind === "verify-start") ui.set({ selected: null });    // the inspector follows the pick being tested
};
