"""Device health score (0-100) from a verified device profile.

Only what the agent proved goes into the score. Every row gets its own 0-100 score from a
stated rule; the family score is its worst row; the device score is the mean of the family
scores, capped when any row is warning or critical, so one dead fan cannot be averaged away.

    row score       rule (thresholds are printed in the result, with where they came from)
    cpu             <=70% full marks, 90% -> 50, 100% -> 0
    memory / disk   <=80% full marks, 95% -> 40, 100% -> 0
    temperature     <=60 C full marks, 85 C -> 30, >=95 C -> 0     (default: no device thresholds read)
    fan (rpm)       0 rpm -> 0, otherwise 100                      (default)
    status enums    ok 100, warning 60, critical 0 (from the MIB's own labels); unknown is not scored

Not scored, and listed as such: families whose pick was not proven, not served by the device,
or rows with no usable reading. "coverage" says how many families the score rests on.

Anomalies: Baseline keeps a rolling window per row and flags a reading far from that row's own
normal (median and MAD). No model is trained: it is a plain statistic, explainable in one line.
"""
from collections import defaultdict, deque
from statistics import median

PROVEN = ("proven-experiment", "proven-witness", "proven-static", "standard-mib")

# (full-marks limit, partial point (value, score), zero point), for "higher is worse" values
RULES = {
    "cpu": {"unit": "%", "ok": 70, "mid": (90, 50), "zero": 100},
    "memory": {"unit": "%", "ok": 80, "mid": (95, 40), "zero": 100},
    "disk": {"unit": "%", "ok": 80, "mid": (95, 40), "zero": 100},
    "cpu_load": {"unit": "%", "ok": 70, "mid": (90, 50), "zero": 100},
    "temperature": {"unit": "C", "ok": 60, "mid": (85, 30), "zero": 95},
}
STATUS_SCORE = {"ok": 100, "warning": 60, "critical": 0}
CAP_WARNING, CAP_CRITICAL = 70, 40


def _line(x, ok, mid, zero):
    """100 up to `ok`, then straight to mid=(x, score), then straight to 0 at `zero`."""
    if x <= ok:
        return 100.0
    mx, ms = mid
    if x <= mx:
        return 100 - (100 - ms) * (x - ok) / (mx - ok)
    if x >= zero:
        return 0.0
    return ms * (zero - x) / (zero - mx)


def _level(score):
    return "ok" if score >= 75 else ("warning" if score >= 40 else "critical")


def _row_value(family, kind, row):
    """The number a rule is applied to: percent for cpu/memory/disk, degrees for temperature."""
    if family == "memory":
        if row.get("percent") is not None:
            return row["percent"]
        return row.get("value") if kind == "percent" else None
    if family == "disk":
        return row.get("percent")
    return row.get("value")


def row_score(family, kind, unit, row):
    """(score|None, rule text). None = not scored (the reason is in the text). A row with both a value
    and a status gets the lower of the two scores."""
    parts = []
    st = row.get("status")
    if st:
        m = st.get("meaning")
        if m in STATUS_SCORE:
            parts.append((STATUS_SCORE[m], f"status {st.get('label')} is {m} (from the MIB's labels)"))
        elif row.get("value") is None:
            return None, f"status {st.get('label')} has no clear meaning in the MIB (unknown): not scored"
    v = _row_value(family, kind, row) if unit != "status" else None
    if v is not None:
        if family == "fan":
            if unit == "rpm":
                parts.append((0.0, "0 rpm: stopped") if v <= 0 else (100.0, f"{v:g} rpm: spinning"))
        elif family in RULES:
            r = RULES[family]
            parts.append((round(_line(v, r["ok"], r["mid"], r["zero"]), 1),
                          f"{v:g}{r['unit']} (full marks up to {r['ok']}{r['unit']}, 0 at {r['zero']}{r['unit']})"))
    if not parts:
        return None, "no usable reading"
    return min(parts, key=lambda p: p[0])


def score(profile):
    """Health score from a device profile (profile.build output)."""
    fams, excluded, worst_level = {}, [], "ok"
    metrics = profile.get("metrics") or {}
    superseded = set()
    if "cpu_load" in metrics and metrics.get("cpu", {}).get("value_type") == "counter" or \
            "cpu_load" in metrics and metrics.get("cpu", {}).get("kind") == "overall" and \
            all(r.get("value") is None for r in metrics["cpu"].get("rows") or [{}]):
        superseded.add("cpu")
        excluded.append({"family": "cpu", "reason": "raw time counter has no percentage from one poll: "
                                                      "scored through hrProcessorLoad (cpu_load) instead"})
    for fam, m in metrics.items():
        if fam in superseded:
            continue
        if m.get("verdict") not in PROVEN:
            excluded.append({"family": fam, "reason": "not served by this device" if m.get("verdict") == "not-supported"
                             or not m.get("object") else "not proven on this device"})
            continue
        rows = []
        for r in m.get("rows") or []:
            s, why = row_score(fam, m.get("kind"), m.get("unit"), r)
            if s is None:
                if m.get("value_type") == "counter" and why == "no usable reading":
                    why = "time counter: becomes a percentage only from two polls (scripts/monitor.py), not scored yet"
                elif fam == "memory" and why == "no usable reading":
                    why = "no total to compare the reading with: not scored"
                excluded.append({"family": fam, "row": r.get("name") or r.get("index"), "reason": why})
                continue
            rows.append({"name": r.get("name") or r.get("index"), "score": s, "level": _level(s), "why": why})
        if not rows:
            excluded.append({"family": fam, "reason": "no scorable rows"})
            continue
        worst = min(rows, key=lambda x: x["score"])
        fams[fam] = {"score": worst["score"], "worst": worst["name"], "rows": rows,
                     "partial": bool(m.get("partial")), "thresholds": "default" if fam in RULES or fam == "fan" else "mib"}
        if any(x["level"] == "critical" for x in rows):
            worst_level = "critical"
        elif any(x["level"] == "warning" for x in rows) and worst_level != "critical":
            worst_level = "warning"
    if not fams:
        return {"score": None, "level": None, "families": {}, "excluded": excluded, "coverage": "0 families",
                "note": "nothing proven to score"}
    total = sum(f["score"] for f in fams.values()) / len(fams)
    cap = {"critical": CAP_CRITICAL, "warning": CAP_WARNING}.get(worst_level)
    final = min(total, cap) if cap is not None else total
    drivers = sorted(((f["score"], k, f["worst"]) for k, f in fams.items()))[:2]
    summary = "; ".join(f"{k} {w} {s:g}/100" for s, k, w in drivers if s < 100) or "all proven readings are healthy"
    served = len([1 for k, m in metrics.items() if m.get("verdict") != "not-supported" and m.get("object")
                  and k not in superseded])
    return {"score": round(final), "level": _level(final), "capped_by": worst_level if cap is not None and total > cap else None,
            "summary": summary, "families": fams, "excluded": excluded,
            "coverage": f"{len(fams)} of {served} served families scored"}


class Baseline:
    """Rolling per-row normal. observe() returns an anomaly note when a reading is far from its own history."""

    def __init__(self, window=30, min_points=8, k=5.0, floor=5.0):
        self.window, self.min_points, self.k, self.floor = window, min_points, k, floor
        self.h = defaultdict(lambda: deque(maxlen=window))

    def observe(self, key, x):
        h = self.h[key]
        note = None
        if len(h) >= self.min_points:
            med = median(h)
            mad = median(abs(v - med) for v in h)
            limit = max(self.k * 1.4826 * mad, self.floor)
            if abs(x - med) > limit:
                note = f"{key}: {x:g} is far from its usual {med:g} (normal spread {limit:g})"
        h.append(x)
        return note

    def scan(self, profile):
        out = []
        for fam, m in (profile.get("metrics") or {}).items():
            if m.get("verdict") not in PROVEN:
                continue
            for r in m.get("rows") or []:
                v = _row_value(fam, m.get("kind"), r)
                if v is not None and not (r.get("status") and r.get("value") is None):
                    n = self.observe(f"{fam}/{r.get('name') or r.get('index')}", v)
                    if n:
                        out.append(n)
        return out
