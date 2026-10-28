"""Verify a monitoring spec against a live device.

Each spec item (the Reader's pick for one metric family) goes through:
  1. static      exists? right SNMP type for the declared value_type? in a physical range after scaling?
  2. observe     poll for a short window: do counters count, do gauges move?
  3. experiment  (lab mode, local host only) apply CPU or memory load and check the
                 metric responds in the right direction with a plausible magnitude.

Verdicts: proven-experiment | proven-static | unproven | rejected, always with evidence.
All device access is read-only (GET/WALK).
"""
import os
import re
import statistics
import subprocess
import time
from dataclasses import dataclass, field

TYPE_CLASS = {
    "Counter32": "counter", "Counter64": "counter",
    "Gauge32": "gauge", "INTEGER": "gauge", "Integer32": "gauge", "Unsigned32": "gauge",
    "UInteger32": "gauge", "Opaque": "gauge",
    "STRING": "string", "Hex-STRING": "string",
    "Timeticks": "ticks", "OID": "oid", "IpAddress": "ip",
}
# Physical ranges in canonical units after scaling.
RANGE = {"percent": (0, 100), "celsius": (-40, 150), "rpm": (0, 40000), "bytes": (0, 2 ** 50)}
CANDIDATE_SCALES = [1, 0.1, 0.01, 0.001, 1024, 1 / 1024, 1024 ** 2, 1 / 1024 ** 2, 8, 1 / 8, 100]
# RFC 2856: 64-bit gauges travel as Counter64 on the wire (SNMPv2 has no 64-bit gauge type).
GAUGE_DECLARED_AS = {"CounterBasedGauge64", "ZeroBasedCounter64"}
NUM = re.compile(r"-?\d+(?:\.\d+)?")
PHASE = {"name": "static"}
EVENTS = {"hook": None}      # optional callable(dict): one call per poll, for live views   # which step is polling: static | observe | base | load (used by session recording)


@dataclass
class Item:
    family: str
    oid: str
    name: str | None = None
    value_type: str = "gauge"           # gauge | counter | string | status | none
    scale: float | None = 1.0           # raw * scale = canonical; None = model unsure
    canonical_unit: str = "none"
    row_filter: str | None = None
    under_load: str | None = None       # model's prediction: rise | fall | none (None = not given)
    witness: dict | None = None         # second independent object for the same quantity
    mib_type: str | None = None         # SYNTAX declared in the MIB (e.g. CounterBasedGauge64)

    @classmethod
    def from_pick(cls, family: str, pick: dict) -> "Item":
        return cls(family=family, oid=(pick.get("oid") or "").lstrip(".").removesuffix(".0"),
                   name=pick.get("object_name"), value_type=pick.get("value_type") or "gauge",
                   scale=pick.get("scale_to_canonical"), canonical_unit=pick.get("canonical_unit") or "none",
                   row_filter=pick.get("row_filter"), under_load=pick.get("under_load"),
                   witness=pick.get("witness"))


@dataclass
class Result:
    item: Item
    verdict: str = "unproven"
    evidence: list = field(default_factory=list)
    suggested_scale: float | None = None
    rows: int = 0
    seconds: float = 0.0
    series: dict | None = None   # what the experiment measured, for charts: see _chart_series
    partial: bool = False        # only some rows of the column fit: stop before load/witness tests

    def add(self, check: str, ok: bool | None, detail: str):
        self.evidence.append({"check": check, "ok": ok, "detail": detail})

    def reject(self, check: str, detail: str):
        self.add(check, False, detail)
        self.verdict = "rejected"
        return self


def number(var) -> float | None:
    if TYPE_CLASS.get(var.type) in ("gauge", "counter", "ticks"):
        try:
            return float(var.value.split()[0])
        except (ValueError, IndexError):
            return None
    if TYPE_CLASS.get(var.type) == "string":
        m = NUM.search(var.value)  # "29C", "84 PERCENT", "45.5"
        return float(m.group()) if m else None
    return None


def fmt(v, unit=None):
    """Readable numbers for evidence: 7,942,956 / 0.71 / 8.13 GB."""
    if v is None:
        return "?"
    if unit == "bytes" and abs(v) >= 1e6:
        return f"{v / 1e9:.2f} GB" if abs(v) >= 1e9 else f"{v / 1e6:.1f} MB"
    if float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:.3g}"


def _in_range(values, scale, unit):
    lo, hi = RANGE[unit]
    return all(lo <= v * scale <= hi for v in values)


def _poll(snmp, oid):
    return {v.oid: number(v) for v in snmp.walk(oid)}


def _series(snmp, oid, seconds, interval):
    """[(t, {row: value})] for `seconds` (at least two samples)."""
    out, t0 = [], time.time()
    while True:
        out.append((time.time(), _poll(snmp, oid)))
        if EVENTS["hook"]:
            EVENTS["hook"]({"kind": "sample", "phase": PHASE["name"], "rows": out[-1][1]})
        if time.time() - t0 >= seconds and len(out) >= 2:
            return out
        time.sleep(interval)


def _rate(series):
    """Mean per-second increase per row for counters (wraps ignored), summed over rows."""
    if len(series) < 2:
        return 0.0
    (t0, a), (t1, b) = series[0], series[-1]
    total = 0.0
    for row, v0 in a.items():
        v1 = b.get(row)
        if v0 is not None and v1 is not None and v1 >= v0:
            total += (v1 - v0) / max(t1 - t0, 1e-6)
    return total


def _chart_series(base, during, row=None, scale=1.0, counter=False):
    """{"base": [[t, v]], "load": [[t, v]], "unit_note": str} with t in seconds from the first sample.
    Gauges: the chosen row's scaled value. Counters: total rate per second between samples."""
    t0 = base[0][0] if base else 0

    def gauge(series):
        return [[round(t - t0, 1), round(rows[row] * scale, 3)] for t, rows in series
                if row in rows and rows[row] is not None]

    def rates(series):
        # Agents cache counters (net-snmp refreshes every ~5 s): keep only samples where the total
        # changed, so each rate spans a real update instead of alternating 0 and double.
        kept = []
        for s in series:
            tot = sum(v for v in s[1].values() if v is not None)
            if not kept or tot != kept[-1][2]:
                kept.append((s[0], s[1], tot))
        series = [(t, r) for t, r, _ in kept]
        out = []
        for (ta, a), (tb, b) in zip(series, series[1:]):
            tot = sum((b[r] - a[r]) for r in a if r in b and a[r] is not None and b[r] is not None and b[r] >= a[r])
            out.append([round(tb - t0, 1), round(tot / max(tb - ta, 1e-6), 2)])
        return out
    f = rates if counter else gauge
    nrows = max((len(r) for _, r in base + during), default=1)
    return {"base": f(base), "load": f(during), "row": row, "counter": counter, "rows": nrows}


def _row_means(series):
    rows = {}
    for _, vals in series:
        for row, v in vals.items():
            if v is not None:
                rows.setdefault(row, []).append(v)
    return {r: statistics.fmean(v) for r, v in rows.items()}


def _mean(series):
    vals = [v for _, rows in series for v in rows.values() if v is not None]
    return statistics.fmean(vals) if vals else None


# ---------------------------------------------------------------- 1. static
def static_checks(snmp, item: Item) -> Result:
    res = Result(item)
    if not item.oid:
        return res.reject("exists", "no object chosen")
    from .snmp import OID_RE
    if not OID_RE.match(item.oid):
        return res.reject("exists", f"not a numeric OID: {item.oid[:40]!r} (never sent to the device)")
    rows = snmp.walk(item.oid)
    res.rows = len(rows)
    if not rows:
        return res.reject("exists", f"{item.oid} is not served by this device (No Such Object/Instance)")
    types = sorted({v.type for v in rows})
    res.add("exists", True, f"{len(rows)} instance(s), type {', '.join(types)}")

    classes = {TYPE_CLASS.get(t, "other") for t in types}
    want = "counter" if item.value_type == "counter" else "gauge"
    if want == "gauge" and classes == {"counter"}:
        if item.mib_type in GAUGE_DECLARED_AS:
            res.add("type", True, f"{', '.join(types)} on the wire, declared {item.mib_type} in the MIB: "
                                  "a 64-bit gauge sent with the counter type (RFC 2856)")
            classes = {"gauge"}
        else:
            return res.reject("type", "device returns a Counter (it only ever increases, like an odometer); "
                                      "reading it as a gauge gives meaningless values")
    if want == "counter" and "counter" not in classes:
        return res.reject("type", f"declared counter but device returns {', '.join(types)}")
    values = [x for x in (number(v) for v in rows) if x is not None]
    if item.value_type in ("gauge", "status") and not values:
        return res.reject("type", f"no numeric value in {rows[0].type} '{rows[0].value[:40]}'")
    if not (res.evidence and res.evidence[-1]["check"] == "type"):
        res.add("type", True, f"{', '.join(types)} fits declared {item.value_type}")

    unit = item.canonical_unit
    if want == "gauge" and unit in RANGE and values:
        fits = [s for s in CANDIDATE_SCALES if _in_range(values, s, unit)]
        sample = "raw " + ", ".join(fmt(v) for v in values[:3]) + (" ..." if len(values) > 3 else "")
        if item.scale is None:
            res.add("range", None, f"model gave no scale; {sample}; scales giving a valid {unit}: {fits[:4]}")
            res.suggested_scale = fits[0] if len(fits) == 1 else None
        elif _in_range(values, item.scale, unit):
            res.add("range", True, f"{sample} x {item.scale:g} = "
                                   + ", ".join(fmt(v * item.scale, unit) for v in values[:3])
                                   + (f" {unit}" if unit != "bytes" else ""))
        else:
            lo, hi = RANGE[unit]
            inside = [v for v in values if lo <= v * item.scale <= hi]
            if inside and len(values) > 1:
                # Some rows fit and some do not: typical of one column shared by several sensor kinds
                # (voltages, fans and temperatures together) or of absent-sensor markers (-1, 2^32-128).
                # Neither proven nor rejected: the column may be right, but which rows are must be settled.
                res.add("range", None, f"{len(inside)} of {len(values)} rows give a valid {unit} with scale "
                                       f"{item.scale:g}; the others ({sample}) look like other sensor kinds or "
                                       "absent-sensor markers in the same column, so the rows need a filter")
                res.verdict, res.partial = "unproven", True
                return res
            res.suggested_scale = fits[0] if fits else None
            return res.reject("range", f"{sample} x {item.scale:g} is outside {RANGE[unit]} {unit}"
                                       + (f"; scale {fits[0]:g} would fit" if fits else ""))
    res.verdict = "proven-static"
    return res


# ---------------------------------------------------------------- 2. observe
def observe(snmp, res: Result, seconds: float = 20, interval: float = 5) -> Result:
    if res.verdict == "rejected":
        return res
    PHASE["name"] = "observe"
    s = _series(snmp, res.item.oid, seconds, interval)
    PHASE["name"] = "static"
    if res.item.value_type == "counter":
        r = _rate(s)
        res.add("observe", r > 0 or None, f"counter rate {r:.2f}/s over {s[-1][0] - s[0][0]:.0f}s")
    else:
        per_row = {}
        for _, rows in s:
            for r, v in rows.items():
                if v is not None:
                    per_row.setdefault(r, []).append(v)
        moving = [r for r, vs in per_row.items() if len(set(vs)) > 1]
        span = max(per_row.items(), key=lambda kv: max(kv[1]) - min(kv[1]), default=(None, [0]))[1]
        res.add("observe", True if moving else None,
                f"{len(moving)} of {len(per_row)} row(s) moved over {s[-1][0] - s[0][0]:.0f}s"
                + (f" (largest swing {fmt(min(span))} -> {fmt(max(span))})" if moving else " (all constant)"))
    return res


# ---------------------------------------------------------------- 3. experiment
def _meminfo_kb(key="MemTotal"):
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith(key + ":"):
                return int(line.split()[1])
    return None


def default_load(kind: str, seconds: int, size_bytes: int = 0):
    """Start a local load generator (stress-ng). Returns a Popen to wait on."""
    if kind == "cpu":
        n = max(1, (os.cpu_count() or 2))
        cmd = ["stress-ng", "--cpu", str(n), "--timeout", f"{seconds}s", "--quiet"]
    elif kind == "memory":
        cmd = ["stress-ng", "--vm", "1", "--vm-bytes", str(size_bytes), "--vm-keep",
               "--timeout", f"{seconds}s", "--quiet"]
    else:
        raise ValueError(kind)
    try:
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        raise NotInducible("stress-ng is not installed on this machine, so no load could be applied "
                           "(sudo apt install stress-ng); this pick stays unproven") from None


class NotInducible(Exception):
    """This load source cannot produce the requested kind of load."""


def prompt_load(instructions: dict, ask=input, say=print):
    """Load applied by a person on the device itself (e.g. a traffic test on a router).

    instructions: {"cpu": "how to start CPU load on this device"}; families without an entry
    cannot be loaded this way. The verifier pauses until the operator confirms start and stop.
    """
    def start(kind, seconds, size_bytes=0):
        if kind not in instructions:
            raise NotInducible(f"no way to load {kind} on this device by hand")
        say(f"\n>>> START {kind.upper()} LOAD ON THE DEVICE NOW and keep it running for about {seconds} s:")
        say(f"    {instructions[kind]}")
        ask(">>> Press Enter once the load is running... ")

        class Stop:
            def terminate(self):
                say(f"\n>>> STOP the {kind} load on the device now (e.g. Ctrl+C in its console).")

            def wait(self, timeout=None):
                ask(">>> Press Enter once it has stopped... ")
                return 0
        return Stop()
    return start


DOWN_WORDS = ("idle", "free", "avail", "unused")
_LOAD_STATE = {"end": 0.0}  # when the last lab load stopped (for cool-down)


def name_rule(item: Item) -> int:
    """Fallback guess from the object name: idle/free/available fall under load, everything else rises."""
    text = (item.name or "").split("::")[-1].lower()
    return -1 if any(w in text for w in DOWN_WORDS) else 1


def expected_direction(item: Item) -> tuple[int, str]:
    """(+1 rise / -1 fall / 0 no movement, source). The model's own prediction wins; the name rule is a fallback."""
    if item.under_load in ("rise", "fall", "none"):
        return {"rise": 1, "fall": -1, "none": 0}[item.under_load], "model"
    return name_rule(item), "name rule"


def experiment(snmp, res: Result, base_s: float = 15, load_s: int = 30, interval: float = 3,
               load=default_load, adapt_windows: bool = True) -> Result:
    """CPU: all cores busy. Memory: allocate min(1 GiB, 25% of RAM). Others: not inducible."""
    it = res.item
    if res.verdict == "rejected":
        return res
    if it.family not in ("cpu", "memory"):
        res.add("experiment", None, f"{it.family} cannot be induced on demand; static checks only")
        return res
    sign, source = expected_direction(it)
    if source == "model" and sign != 0 and sign != name_rule(it):
        res.add("direction", None, f"model predicts {'rise' if sign > 0 else 'fall'}; the name suggests the "
                                   "opposite: the experiment decides")
    if sign == 0:
        res.add("experiment", None, "model predicts no movement under load: an experiment cannot prove it")
        return res
    # Averaged objects (1/5-minute CPU, load averages) lag the load: give them time.
    text = f"{it.name or ''} {it.row_filter or ''}".lower()
    if adapt_windows and it.family == "cpu" and it.value_type != "counter":
        if re.search(r"5\s*min|5min|300", text):
            load_s = max(load_s, 330)
        elif re.search(r"1\s*min|1min|average|avg|load", text) or "hrprocessorload" in text:
            load_s = max(load_s, 75)
        else:
            load_s = max(load_s, 45)
    # Cool-down: a baseline taken while a previous load is still in an average is wrong.
    settle = {330: 330, 75: 75}.get(load_s, 15) if it.value_type != "counter" else 5
    wait = _LOAD_STATE["end"] + settle - time.time()
    if adapt_windows and wait > 0:
        time.sleep(wait)
    alloc = 0
    if it.family == "memory":
        total_kb = _meminfo_kb() or 4 * 1024 * 1024
        alloc = int(min(1024 ** 3, total_kb * 1024 * 0.25))
    PHASE["name"] = "base"
    base = _series(snmp, it.oid, base_s, interval)
    busy_base = 0.0
    if it.family == "cpu" and it.canonical_unit == "percent" and it.value_type != "counter":
        busy_base = max(_row_means(base).values(), default=0) * (it.scale if it.scale is not None else 1)
    try:
        proc = load(it.family, load_s + 10, alloc)
    except NotInducible as why:
        PHASE["name"] = "static"
        res.add("experiment", None, str(why))
        return res
    PHASE["name"] = "load"
    try:
        warm = min(load_s / 2, 6 if load_s <= 45 else load_s - 30) if adapt_windows else 0
        time.sleep(warm)                         # let caches and averages catch up
        during = _series(snmp, it.oid, load_s - warm, interval)
    finally:
        proc.terminate()
        proc.wait(timeout=15)
        _LOAD_STATE["end"] = time.time()
        PHASE["name"] = "static"

    scale = it.scale if it.scale is not None else (res.suggested_scale or 1)
    if it.value_type == "counter":
        r0, r1 = _rate(base), _rate(during)
        res.series = _chart_series(base, during, counter=True)
        ok = r1 > max(r0 * 1.5, r0 + 1) if sign > 0 else r1 < r0 * 0.67
        res.add("experiment", ok, f"{it.family} load: counter rate {r0:.1f}/s -> {r1:.1f}/s"
                                  f" (expected {'rise' if sign > 0 else 'fall'}, per {source})")
    else:
        b, d = _row_means(base), _row_means(during)
        common = [r for r in b if r in d]
        if not common:
            return res.reject("experiment", "no numeric readings during experiment")
        # Tables (one row per CPU core, storage area, pool...): judge the row that responded most
        # in the expected direction, and report it so the row filter is learned, not guessed.
        row = max(common, key=lambda r: sign * (d[r] - b[r]))
        m0, m1 = b[row], d[row]
        delta = (m1 - m0) * scale
        res.series = _chart_series(base, during, row=row, scale=scale)
        if len(common) > 1:
            res.add("row", None, f"{len(common)} rows; responding row {row.rsplit('.', 1)[-1]} (instance {row})")
        if it.family == "cpu" and sign > 0 and busy_base >= 60 and -10 < delta < 10:
            # Already busy before the test and flat under load: the experiment could not show a
            # rise. Inconclusive, never evidence against the object (a clear fall still rejects).
            PHASE["name"] = "static"
            res.add("experiment", None, f"inconclusive: the device was already busy before the test "
                                        f"(busiest row {busy_base:.0f}%, change {delta:+.1f}); "
                                        "stop other load and run again")
            return res
        if it.family == "cpu":
            ok = sign * delta > 0 and (it.canonical_unit != "percent" or abs(delta) >= 10)
            res.add("experiment", ok, f"cpu load: {m0 * scale:.1f} -> {m1 * scale:.1f} {it.canonical_unit}"
                                      f" (change {delta:+.1f}, expected {'rise' if sign > 0 else 'fall'}, per {source})")
        else:
            if it.canonical_unit == "percent":
                expect = alloc / ((_meminfo_kb() or 1) * 1024) * 100
            else:
                expect = alloc
            ratio = abs(delta) / expect if expect else 0
            ok = 0.3 <= ratio <= 3 and sign * delta > 0
            detail = (f"allocated {alloc / 1024 ** 2:.0f} MiB: change {delta:+,.0f} {it.canonical_unit}"
                      f" (expected {'+' if sign > 0 else '-'}{expect:,.0f} per {source}, ratio {ratio:.2g})")
            if not ok and ratio > 0:
                for s in CANDIDATE_SCALES:
                    if 0.3 <= abs(m1 - m0) * s / expect <= 3:
                        res.suggested_scale = s
                        detail += f"; scale {s:g} would match"
                        break
            res.add("experiment", ok, detail)
    if res.evidence[-1]["ok"] is False:
        res.verdict = "rejected"
    elif res.evidence[-1]["ok"]:
        res.verdict = "proven-experiment"
    return res


TOLERANCE = {"percent": ("abs", 10), "celsius": ("abs", 5), "rpm": ("rel", 0.15), "bytes": ("rel", 0.10)}


def _agg(rows, scale):
    vals = [v * scale for v in rows.values() if v is not None]
    return statistics.fmean(vals) if vals else None


FAMILY_WORDS = {"cpu": ("cpu", "processor"), "memory": ("mem",), "temperature": ("temp", "thermal"),
                "fan": ("fan",)}


def _names_other_family(name, family):
    """A witness whose name says it is another quantity (CPU load offered as a temperature witness)."""
    n = (name or "").lower()
    if any(w in n for w in FAMILY_WORDS.get(family, ())):
        return None
    return next((f for f, words in FAMILY_WORDS.items() if f != family and any(w in n for w in words)), None)


def witness_check(snmp, res: Result, samples: int = 3, interval: float = 2) -> Result:
    """Read the pick and its witness side by side; agreement corroborates, disagreement is a conflict."""
    it, w = res.item, res.item.witness or {}
    woid = (w.get("oid") or "").lstrip(".").removesuffix(".0")
    if not woid:
        return res
    from .snmp import OID_RE
    if not OID_RE.match(woid) or woid == it.oid:
        res.add("witness", None, f"witness {w.get('object_name')!r} is not a usable second object")
        return res
    other = _names_other_family(w.get("object_name"), it.family)
    if other:
        res.add("witness", None, f"witness {w.get('object_name')!r} measures {other}, not {it.family}: "
                                 "not compared")
        return res
    if it.value_type == "counter":
        res.add("witness", None, "witness comparison is for gauges; counters are checked by experiment")
        return res
    PHASE["name"] = "witness"
    pairs = []
    try:
        for i in range(samples):
            a, b = _poll(snmp, it.oid), _poll(snmp, woid)
            if not b:
                res.add("witness", None, f"witness {w.get('object_name')} ({woid}) is not served by this device")
                return res
            pairs.append((_agg(a, it.scale if it.scale is not None else 1),
                          _agg(b, w.get("scale_to_canonical") if w.get("scale_to_canonical") is not None else 1)))
            if i < samples - 1:
                time.sleep(interval)
    finally:
        PHASE["name"] = "static"
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    if not pairs:
        res.add("witness", None, "witness gave no numeric readings")
        return res
    x = statistics.fmean(p[0] for p in pairs)
    y = statistics.fmean(p[1] for p in pairs)
    kind, tol = TOLERANCE.get(it.canonical_unit, ("rel", 0.15))
    diff = abs(x - y) if kind == "abs" else abs(x - y) / max(abs(x), abs(y), 1e-9)
    agree = diff <= tol
    unit = it.canonical_unit if it.canonical_unit != "none" else ""
    detail = (f"{it.name} reads {fmt(x, it.canonical_unit)}{(' ' + unit) if unit and unit != 'bytes' else ''}, "
              f"witness {w.get('object_name')} reads {fmt(y, it.canonical_unit)}"
              f"{(' ' + unit) if unit and unit != 'bytes' else ''}: "
              + ("agree" if agree else "disagree") + f" (tolerance {tol:g}{'' if kind == 'abs' else ' relative'})")
    res.add("witness", agree, detail)
    if agree and res.verdict in ("proven-static", "unproven"):
        res.verdict = "proven-witness"
    elif not agree and res.verdict != "proven-experiment":
        res.verdict = "unproven"          # two objects disagree: one is wrong, the evidence says which to re-read
    return res


def verify_item(snmp, item: Item, lab: bool = False, observe_s: float = 20, **kw) -> Result:
    t0 = time.time()
    witness_interval = kw.pop("witness_interval", 2)
    observe_interval = kw.pop("observe_interval", 5)
    res = static_checks(snmp, item)
    if res.verdict != "rejected" and not res.partial:
        res = experiment(snmp, res, **kw) if lab else observe(snmp, res, seconds=observe_s, interval=observe_interval)
        res = witness_check(snmp, res, interval=witness_interval)
        if res.verdict == "proven-static" and item.family in ("cpu", "memory"):
            res.verdict = "unproven"   # inducible family, but not proven under load
            if not lab:
                res.add("note", None, "run with lab mode on a test device to prove it under load")
    res.seconds = round(time.time() - t0, 1)
    return res
