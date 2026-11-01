// What the console can do: replay a recording at its real pace (scaled), start a live run, monitor, stop, and export.
import { app, dispatch, emptyRun, notify, rememberRun, run, ui, type RunState } from "./store";
import { getIndex, getSession, getStatus, post, sampleFromHealth, stream } from "./api";
import type { HealthSample, HealthSession, OnboardingSession, RunEvent, Session } from "./types";

let token = 0;                                         // a newer replay or live run cancels the old one
let closeStream: (() => void) | null = null;
const MAX_GAP_MS = 6000;                               // long silences in a recording are shortened, never skipped

function reset(patch: Partial<RunState>) {
  token++;
  closeStream?.();
  closeStream = null;
  run.set({ ...emptyRun(), ...patch, started: Date.now() });
  ui.set({ selected: null, paused: false });
}

const wait = (ms: number, mine: number) => new Promise<boolean>((resolve) => {
  const step = (left: number) => {
    if (mine !== token) return resolve(false);
    if (ui.get().paused) return setTimeout(() => step(left), 120);
    if (left <= 0) return resolve(true);
    const slice = Math.min(left, 120);
    setTimeout(() => step(left - slice), slice);
  };
  step(ms);
});

interface Step { t?: number; ev: RunEvent; sample?: HealthSample }

/** A recording as steps with their recorded times (health polls are spaced by the poll interval). */
function timeline(s: Session): Step[] {
  if ("samples" in s) {
    const h = s as HealthSession;
    return h.samples.map((sample, i) => ({ t: i * h.interval, sample, ev: { ...sample, kind: "health", t: i * h.interval } }));
  }
  return (s as OnboardingSession).events.map((ev) => ({ t: ev.t, ev }));
}

export async function replay(id: string) {
  const s = await getSession(id);
  const health = "samples" in s;
  reset({
    origin: "replay", kind: health ? "health" : "onboarding", status: "running", device: s.device,
    source: `Replay · recorded ${s.recorded}`,
    ...(health
      ? { interval: (s as HealthSession).interval, policy: (s as HealthSession).policy, notScored: (s as HealthSession).not_scored ?? [] }
      : { model: (s as OnboardingSession).model, objects: (s as OnboardingSession).objects,
          modules: (s as OnboardingSession).modules.split(","),
          stats: { seconds: (s as OnboardingSession).seconds, snmp_calls: (s as OnboardingSession).snmp_calls } }),
  });
  const mine = token;
  let last = 0;
  for (const { t, ev, sample } of timeline(s)) {
    const speed = ui.get().speed;
    const gap = Math.max(0, (t ?? last) - last) * 1000;
    last = t ?? last;
    if (speed > 0 && !(await wait(Math.min(gap / speed, MAX_GAP_MS), mine))) return;
    if (mine !== token) return;
    if (sample) run.set((r) => ({ health: [...r.health, sample], events: [...r.events, { ...ev, seq: r.events.length }] }));
    else dispatch(ev);
  }
  if (mine === token) run.set({ status: "done" });
}

function follow(id: string) {
  run.set({ runId: id, connected: true });
  closeStream = stream(id, (ev) => {
    if (ev.kind === "health") {
      const sample = { ...sampleFromHealth(ev), load: Date.now() < run.get().loadUntil };
      run.set((r) => ({ health: [...r.health, sample], events: [...r.events, { ...ev, seq: r.events.length }],
        notScored: r.health.length ? r.notScored : ((ev.excluded as RunState["notScored"]) ?? []) }));
    } else dispatch(ev);
    if ((ev.kind === "end" || ev.kind === "error") && app.get().ownRun?.id === id) rememberRun(null);   // finished: nothing to go back to
  }, (dropped) => {
    if (dropped && !run.get().events.length && app.get().ownRun?.id === id) {      // the server no longer has it
      rememberRun(null);
      notify("That run has already ended.", "Ended");
    }
    getIndex().then((index) => app.set({ index })).catch(() => {});    // the run just saved a recording: list it
    run.set((r) => ({ connected: false, status: r.status === "running" ? (dropped ? "error" : "done") : r.status,
    message: dropped && r.status === "running" ? "The connection to the server dropped." : r.message }));
  });
}

/** Start an onboarding run. On the hosted demo the server ignores the body and runs its fixed device. */
let starting = false;                                  // a start request is in flight: a second click must not wipe the view
export async function startRun(body: unknown, device: string) {
  if (starting) return;
  starting = true;
  try {
    // Ask first: a refused start must leave what is on screen (a finished run) untouched.
    const { id } = await post<{ id: string }>("/api/runs", body).catch(rejoinOr);
    reset({ origin: "live", kind: "onboarding", status: "running", device, source: "Live" });
    rememberRun({ id, kind: "onboard", device });
    follow(id);
  } finally { starting = false; }
}

export async function startMonitor(body: unknown, device: string) {
  const { id } = await post<{ id: string }>("/api/monitor", body).catch(rejoinOr);
  reset({ origin: "live", kind: "health", status: "running", device, source: "Live monitor" });
  rememberRun({ id, kind: "monitor", device });
  follow(id);
}

/** Follow a run that is already going on the server (the page was reloaded, or another tab started it).
 *  The server sends its events from the beginning, so the view is complete. */
export function attach(r: { id: string; kind: "onboard" | "monitor"; device?: string | null }) {
  const health = r.kind === "monitor";
  reset({ origin: "live", kind: health ? "health" : "onboarding", status: "running", device: r.device || "",
    source: health ? "Live monitor" : "Live" });
  follow(r.id);
}

/** A start refused because a run is already in progress: show that run instead of an error. */
async function rejoinOr(err: Error): Promise<{ id: string }> {
  const s = await getStatus();
  if (!s?.run) throw err;
  attach(s.run);
  throw new Rejoined();
}
export class Rejoined extends Error { constructor() { super("rejoined the run in progress"); } }

const runId = () => run.get().runId;
export const stopRun = () => post(`/api/runs/${runId()}/stop`, {});
export const loadNow = (kind: "cpu" | "memory" | "both") => post(`/api/runs/${runId()}/load`, { kind, seconds: 120 });
export async function confirmPrompt() {
  run.set((r) => ({ prompt: r.prompt && { ...r.prompt, waiting: false } }));
  await post(`/api/runs/${runId()}/confirm`, {});
}

export function stopReplay() { token++; run.set((r) => ({ status: r.status === "running" ? "done" : r.status })); }

/** Download what was captured, as JSON, for debugging elsewhere. */
export function exportJson() {
  const r = run.get();
  const doc = { exported_at: new Date().toISOString(), source: r.source, device: r.device, model: r.model,
    modules: r.modules, stats: r.stats, finals: r.finals, events: r.events.map(({ seq: _seq, ...e }) => e) };
  const url = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `onboardiq-${(r.device || "run").toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 40)}-${Date.now()}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
