// The command bar's filter language: `key:value` terms plus free text, all must match.
//   family:cpu  verdict:rejected  object:ssCpu  oid:2021.11  kind:verify  attempt:2  free text
import type { Attempt, Stamped } from "./store";

export interface Query { terms: [string, string][]; text: string[]; bad: string[] }
export const KEYS = ["family", "verdict", "object", "oid", "kind", "attempt"] as const;

export function parse(input: string): Query {
  const q: Query = { terms: [], text: [], bad: [] };
  for (const raw of input.trim().split(/\s+/).filter(Boolean)) {
    const m = raw.match(/^([a-z]+):(.+)$/i);
    if (!m) { q.text.push(raw.toLowerCase()); continue; }
    const key = m[1].toLowerCase();
    if ((KEYS as readonly string[]).includes(key)) q.terms.push([key, m[2].toLowerCase()]);
    else q.bad.push(raw);
  }
  return q;
}

const has = (v: unknown, needle: string) => String(v ?? "").toLowerCase().includes(needle);

export function matchAttempt(a: Attempt, q: Query): boolean {
  const verdict = a.verdict ?? "testing";
  for (const [k, v] of q.terms) {
    if (k === "family" && !has(a.family, v)) return false;
    if (k === "verdict" && !has(verdict, v)) return false;
    if (k === "object" && !has(a.object, v)) return false;
    if (k === "oid" && !has(a.oid, v)) return false;
    if (k === "attempt" && String(a.attempt) !== v) return false;
  }
  const blob = `${a.object} ${a.oid} ${a.family} ${verdict} ${(a.evidence ?? []).map((c) => c.detail).join(" ")}`.toLowerCase();
  return q.text.every((t) => blob.includes(t));
}

export function matchEvent(e: Stamped, q: Query): boolean {
  for (const [k, v] of q.terms) {
    if (k === "kind" && !has(e.kind, v)) return false;
    if (k === "family" && !has(e.family, v)) return false;
    if (k === "verdict" && !has(e.verdict, v)) return false;
    if (k === "object" && !has(e.object, v)) return false;
    if (k === "oid" && !has(e.oid, v)) return false;
    if (k === "attempt" && String(e.attempt ?? "") !== v) return false;
  }
  if (!q.text.length) return true;
  const blob = JSON.stringify(e).toLowerCase();
  return q.text.every((t) => blob.includes(t));
}
