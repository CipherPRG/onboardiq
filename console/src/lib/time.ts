// Recorded times are stored in UTC and shown in UTC, always labelled. The model's explanations and the alert
// emails quote UTC clock times too, so every time on screen and in a mail reads against the same clock.

/** "2026-11-01 12:23 UTC" from "2026-11-01 12:23 UTC" or an ISO stamp; the input unchanged if it is neither. */
export function utcStamp(s?: string | null): string {
  const m = /^(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d)/.exec((s ?? "").trim());
  return m ? `${m[1]} ${m[2]} UTC` : s ?? "";
}

/** "12:23:45 UTC" from an ISO stamp; empty when there is none. */
export function utcClock(s?: string | null): string {
  const m = /[ T](\d\d:\d\d:\d\d)/.exec(s ?? "");
  return m ? `${m[1]} UTC` : "";
}
