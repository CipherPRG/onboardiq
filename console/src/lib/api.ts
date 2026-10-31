// The OnboardIQ server's API (absent on static hosting, where only recordings play) and the recorded sessions.
import type { HealthSample, Onboarded, RunEvent, ServerStatus, Session, SessionIndexEntry } from "./types";

const SESSIONS = "../sessions/";                     // web/sessions, next to the console folder

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data as { error?: string }).error || `request failed (${r.status})`);
  return data as T;
}

export const getStatus = () => json<ServerStatus>("/api/status", { cache: "no-store" }).catch(() => null);
export const getIndex = () => json<SessionIndexEntry[]>(`${SESSIONS}index.json`, { cache: "no-store" });
export function getSession(id: string): Promise<Session> {
  if (!/^[A-Za-z0-9_-]+$/.test(id)) return Promise.reject(new Error("bad session id"));
  return json<Session>(`${SESSIONS}${id}.json`);
}
export const getOnboarded = () => json<Onboarded[]>("/api/onboarded", { cache: "no-store" });
export const getModules = (q: string) => json<string[]>(`/api/modules?q=${encodeURIComponent(q)}`);

export const post = <T>(path: string, body: unknown) => json<T>(path, {
  method: "POST", headers: { "Content-Type": "application/json", "X-OnboardIQ": "1" }, body: JSON.stringify(body),
});

/** Follow a run's events; `onClose` fires once, when the run ends or the connection drops. Returns a closer. */
export function stream(id: string, onEvent: (ev: RunEvent) => void, onClose: (dropped: boolean) => void) {
  const es = new EventSource(`/api/runs/${id}/events`);
  let done = false;
  const close = (dropped: boolean) => { if (!done) { done = true; es.close(); onClose(dropped); } };
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data) as RunEvent;
    onEvent(ev);
    if (ev.kind === "end" || ev.kind === "error") close(false);
  };
  es.onerror = () => close(true);
  return () => close(false);
}

export interface UploadReport {
  converted: string[]; failed: Record<string, string>; missing_imports: Record<string, string[]>;
  incomplete: string[]; files: Record<string, string>; skipped: { file: string; reason: string }[];
}
export const uploadMibs = (files: { name: string; text: string }[]) => post<UploadReport>("/api/mibs", { files });

/** A live monitor event carries the full score result; reduce it to the recorded sample shape. */
export function sampleFromHealth(res: RunEvent): HealthSample {
  const families: HealthSample["families"] = {};
  const fams = (res.families ?? {}) as Record<string, { score: number; worst?: string; partial?: boolean;
    rows: { name: string; why?: string; level?: string }[] }>;
  for (const [k, f] of Object.entries(fams)) {
    const worst = f.rows.find((r) => r.name === f.worst) ?? f.rows[0];
    families[k] = { score: f.score, worst: f.worst, why: worst?.why, rows: f.rows.length,
      flagged: f.rows.filter((r) => r.level !== "ok").length, partial: f.partial };
  }
  const pick = <K extends keyof HealthSample>(k: K) => res[k as string] as HealthSample[K];
  return { at: pick("at"), score: pick("score") ?? null, level: pick("level"), capped_by: pick("capped_by"),
    summary: pick("summary"), note: pick("note"), coverage: pick("coverage"), families,
    anomalies: pick("anomalies") ?? [], events: pick("events") ?? [], explanation: pick("explanation"), alert: pick("alert") };
}
