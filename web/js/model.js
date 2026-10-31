// What a verdict means and how it is shown, for the story's charts.
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

export const LEVEL_TONE = { ok: "ok", warning: "warn", critical: "bad" };
