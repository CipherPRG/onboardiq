// Recorded times are stored in UTC. People read them in their own time zone, so convert and name the zone.

/** Parse "2026-11-01 12:23 UTC" or an ISO stamp ("2026-11-01T12:23:45+00:00"); null if it isn't a time. */
export function parseUtc(s?: string | null): Date | null {
  if (!s) return null;
  const t = s.trim().replace(/ UTC$/, "").replace(" ", "T");
  const hasZone = /(Z|[+-]\d\d:?\d\d)$/.test(t);
  const d = new Date(hasZone ? t : `${t}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

const zone = (d: Date) => new Intl.DateTimeFormat(undefined, { timeZoneName: "short" }).formatToParts(d).find((p) => p.type === "timeZoneName")?.value ?? "";
const two = (n: number) => String(n).padStart(2, "0");

/** "2026-11-01 17:53 IST" in the viewer's time zone. */
export function localStamp(s?: string | null): string {
  const d = parseUtc(s);
  if (!d) return s ?? "";
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())} ${zone(d)}`;
}

/** "17:53:20 IST" in the viewer's time zone. */
export function localClock(s?: string | null): string {
  const d = parseUtc(s);
  if (!d) return "";
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())} ${zone(d)}`;
}
