// What the sessions mean: names for metrics, and how a verdict is shown.
export const FAMILY = { cpu: "CPU", memory: "Memory", temperature: "Temperature", fan: "Fan" };
export const HEALTH_FAMILY = { ...FAMILY, cpu_load: "CPU load", disk: "Disk" };

const VERDICT = {
  "proven-experiment": ["Proven", "by experiment on the device", "ok"],
  "proven-witness": ["Proven", "by an independent second object", "ok"],
  "proven-static": ["Proven", "by static checks", "ok"],
  "unproven": ["Not proven", "checks passed, but nothing showed it under load", "warn"],
  "not-supported": ["Not on this device", "the device does not serve this object", "muted"],
  "rejected": ["Rejected", "failed on the device", "bad"],
};

/** [title, detail, tone] for a verdict; a rejection because the object is absent reads as "not served". */
export function verdictFor(verdict, evidence = []) {
  if (verdict === "rejected" && evidence.some((c) => (c.detail || "").includes("not served"))) {
    return ["Not served", "this device does not have this object", "muted"];
  }
  return VERDICT[verdict] || [verdict, "", "muted"];
}

export const isProven = (verdict) => String(verdict).startsWith("proven");

/** "3 of 4 metrics proven on the device; the agent caught and corrected 1 wrong pick." */
export function outcome(summary, events) {
  const fams = Object.keys(summary);
  const proven = fams.filter((f) => isProven(summary[f]));
  const corrected = proven.filter((f) => events.some((e) => e.kind === "verify" && e.family === f && e.verdict === "rejected"));
  let msg = `${proven.length} of ${fams.length} metrics proven on the device`;
  if (corrected.length) msg += `; the agent caught and corrected ${corrected.length} wrong pick${corrected.length > 1 ? "s" : ""}`;
  return msg + ".";
}

export const LEVEL_TONE = { ok: "ok", warning: "warn", critical: "bad" };
