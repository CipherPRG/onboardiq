"""Device health score (0-100) from a verified device profile.

Only what the agent proved goes into the score. Every row gets its own 0-100 score from a
stated rule; the family score is its worst row; the device score is the mean of the family
scores, capped when any row is warning or critical, so one dead fan cannot be averaged away.

    row score       rule from the policy file (health_policy.json): per-family limits, fan rule, status
                    scores, level cut-offs and caps. Nothing is fixed in the code; the result names the policy.
                    Status labels come from the MIB's own enum (ok / warning / critical / unknown).

Not scored, and listed as such: families whose pick was not proven, not served by the device,
or rows with no usable reading. "coverage" says how many families the score rests on.

Anomalies: Baseline keeps a rolling window per row and flags a reading far from that row's own
normal (median and MAD). No model is trained: it is a plain statistic, explainable in one line.
"""
import json
import os
from collections import Counter, defaultdict, deque
from pathlib import Path
from statistics import median

PROVEN = ("proven-experiment", "proven-witness", "proven-static", "standard-mib")
POLICY_FILE = Path(__file__).with_name("health_policy.json")


def load_policy(path=None):
    """The limits, status scores and caps the score uses, from a JSON file (default: health_policy.json
    next to this module; override with a path or the OIQ_HEALTH_POLICY environment variable)."""
    src = Path(path or os.environ.get("OIQ_HEALTH_POLICY") or POLICY_FILE)
    pol = json.loads(src.read_text())
    for fam, r in pol["rules"].items():
        if not r["ok"] < r["mid"][0] < r["zero"]:
            raise ValueError(f"policy {src.name}: '{fam}' needs ok < mid < zero")
    pol["source"] = src.name
    return pol


DEFAULT = load_policy()
CAP_WARNING, CAP_CRITICAL = DEFAULT["caps"]["warning"], DEFAULT["caps"]["critical"]


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


def _labels(rows):
    """One unique, readable label per row: its name, else its index, else "overall". Rows that share a name
    (every core of a CPU is often named after the processor model) get #1, #2, ..."""
    names = [r.get("name") or r.get("index") or "overall" for r in rows]
    total, seen, out = Counter(names), Counter(), []
    for n in names:
        if total[n] > 1:
            seen[n] += 1
            out.append(f"{n} #{seen[n]}")
        else:
            out.append(n)
    return out


def _level(score, pol=None):
    lv = (pol or DEFAULT)["levels"]
    return "ok" if score >= lv["ok_from"] else ("warning" if score >= lv["warning_from"] else "critical")


def _row_value(family, kind, row):
    """The number a rule is applied to: percent for cpu/memory/disk, degrees for temperature."""
    if family == "memory":
        if row.get("percent") is not None:
            return row["percent"]
        return row.get("value") if kind == "percent" else None
    if family == "disk":
        return row.get("percent")
    return row.get("value")


def row_score(family, kind, unit, row, pol=None):
    """(score|None, rule text). None = not scored (the reason is in the text). A row with both a value
    and a status gets the lower of the two scores."""
    pol = pol or DEFAULT
    parts = []
    st = row.get("status")
    if st:
        m = st.get("meaning")
        if m in pol["status_scores"]:
            parts.append((pol["status_scores"][m], f"status {st.get('label')} is {m} (from the MIB's labels)"))
        elif row.get("value") is None:
            return None, f"status {st.get('label')} has no clear meaning in the MIB (unknown): not scored"
    v = _row_value(family, kind, row) if unit != "status" else None
    if v is not None:
        if family == "fan":
            if unit == "rpm":
                fan = pol["fan"]
                parts.append((fan["stopped_score"], f"{v:g} rpm: stopped") if v <= fan["stopped_at_or_below_rpm"]
                             else (fan["spinning_score"], f"{v:g} rpm: spinning"))
        elif family in pol["rules"]:
            r = pol["rules"][family]
            parts.append((round(_line(v, r["ok"], r["mid"], r["zero"]), 1),
                          f"{v:g}{r['unit']} (full marks up to {r['ok']}{r['unit']}, 0 at {r['zero']}{r['unit']})"))
    if not parts:
        return None, "no usable reading"
    return min(parts, key=lambda p: p[0])


def score(profile, policy=None):
    """Health score from a device profile (profile.build output) under a policy (default: health_policy.json)."""
    pol = policy or DEFAULT
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
        src = m.get("rows") or []
        for r, label in zip(src, _labels(src)):
            s, why = row_score(fam, m.get("kind"), m.get("unit"), r, pol)
            if s is None:
                if m.get("value_type") == "counter" and why == "no usable reading":
                    why = "time counter: becomes a percentage only from two polls (scripts/monitor.py), not scored yet"
                elif fam == "memory" and why == "no usable reading":
                    why = "no total to compare the reading with: not scored"
                excluded.append({"family": fam, "row": label, "reason": why})
                continue
            rows.append({"name": label, "score": s, "level": _level(s, pol), "why": why})
        if not rows:
            excluded.append({"family": fam, "reason": "no scorable rows"})
            continue
        worst = min(rows, key=lambda x: x["score"])
        fams[fam] = {"score": worst["score"], "worst": worst["name"], "rows": rows,
                     "partial": bool(m.get("partial")), "thresholds": f"policy {pol['source']}"}
        if any(x["level"] == "critical" for x in rows):
            worst_level = "critical"
        elif any(x["level"] == "warning" for x in rows) and worst_level != "critical":
            worst_level = "warning"
    if not fams:
        return {"score": None, "level": None, "families": {}, "excluded": excluded, "coverage": "0 families",
                "note": "nothing proven to score"}
    total = sum(f["score"] for f in fams.values()) / len(fams)
    cap = {"critical": pol["caps"]["critical"], "warning": pol["caps"]["warning"]}.get(worst_level)
    final = min(total, cap) if cap is not None else total
    drivers = sorted(((f["score"], k, f["worst"]) for k, f in fams.items()))[:2]
    summary = "; ".join(f"{k} {w} {s:g}/100" for s, k, w in drivers if s < 100) or "all proven readings are healthy"
    served = len([1 for k, m in metrics.items() if m.get("verdict") != "not-supported" and m.get("object")
                  and k not in superseded])
    level = _level(final, pol)
    if worst_level == "critical" or (worst_level == "warning" and level == "ok"):
        level = worst_level  # a critical row must never read as a lesser level because the cap equals a cut-off
    return {"score": round(final), "level": level, "policy": f"{pol['name']} ({pol['source']})", "capped_by": worst_level if cap is not None and total > cap else None,
            "summary": summary, "families": fams, "excluded": excluded,
            "coverage": f"{len(fams)} of {served} served families scored"}


class Baseline:
    """Rolling per-row normal. observe() returns an anomaly note when a reading is far from its own history."""

    def __init__(self, window=30, min_points=8, k=5.0, floor=5.0):
        self.window, self.min_points, self.k, self.floor = window, min_points, k, floor
        self.h = defaultdict(lambda: deque(maxlen=window))

    def _check(self, key, x):
        """(median, limit) when x is far from this row's own history, else None. Adds x to the history."""
        h = self.h[key]
        hit = None
        if len(h) >= self.min_points:
            med = median(h)
            mad = median(abs(v - med) for v in h)
            limit = max(self.k * 1.4826 * mad, self.floor)
            if abs(x - med) > limit:
                hit = (med, limit)
        h.append(x)
        return hit

    def observe(self, key, x):
        hit = self._check(key, x)
        return f"{key}: {x:g} is far from its usual {hit[0]:g} (normal spread {hit[1]:g})" if hit else None

    def scan(self, profile):
        out = []
        for fam, m in (profile.get("metrics") or {}).items():
            if m.get("verdict") not in PROVEN:
                continue
            src = m.get("rows") or []
            hits = []
            for r, label in zip(src, _labels(src)):
                v = _row_value(fam, m.get("kind"), r)
                if v is None or (r.get("status") and r.get("value") is None):
                    continue
                hit = self._check(f"{fam}/{label}", v)
                if hit:
                    hits.append((label, v, hit[0]))
            if len(hits) > 3:
                label, v, med = max(hits, key=lambda t: abs(t[1] - t[2]))
                out.append(f"{fam}: {len(hits)} of {len(src)} rows far from their usual, e.g. {label} "
                           f"at {v:g} (usual {med:g})")
            else:
                out += [f"{fam}/{label}: {v:g} is far from its usual {med:g}" for label, v, med in hits]
        return out
