"""Verified device profile: what to poll on this device, how to read it, and why each part is trusted.

Built after the agent has verified its picks. Everything here comes from the device's own answers
and the MIB's own text, never from a vendor list:

    kind        cpu: per-core rows or one overall value; memory: percent, used+free, used+total or pools
    names       each row named from the table's own name column (or ENTITY-MIB / HOST-RESOURCES by index)
    status      raw codes mapped to the MIB's enum labels; a severity only when the label says so,
                "unknown" when it is ambiguous
    validity    rows outside the MIB's declared range, undefined enum values, other sensor kinds in a
                shared column, absent sensors and physically impossible values are skipped and listed;
                "partial" says some rows were skipped
    proof       the verifier's evidence, plus (lab mode) a load test that moves used and free/total together
    provenance  sysObjectID, sysDescr and firmware, so the profile is tied to the device it was built on

All device access is read-only (GET/WALK).
"""
import re
import time

from . import verify
from .verify import RANGE, number

SYS_DESCR, SYS_OBJECT_ID = "1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.2.0"
ENT_NAME, ENT_DESCR = "1.3.6.1.2.1.47.1.1.1.1.7", "1.3.6.1.2.1.47.1.1.1.1.2"
ENT_FIRMWARE, ENT_SOFTWARE = "1.3.6.1.2.1.47.1.1.1.1.9", "1.3.6.1.2.1.47.1.1.1.1.10"
HR_DEVICE_DESCR = "1.3.6.1.2.1.25.3.2.1.3"
HR_STORAGE = "1.3.6.1.2.1.25.2.3.1"
HR_STORAGE_TYPE, HR_STORAGE_RAM = HR_STORAGE + ".2", "1.3.6.1.2.1.25.2.1.2"
# Standard index objects whose names live in another table (RFC 6933 ENTITY-MIB, RFC 2790 HOST-RESOURCES).
INDEX_NAMES = {"entPhysicalIndex": (ENT_NAME, ENT_DESCR), "augments:entPhysicalEntry": (ENT_NAME, ENT_DESCR),
               "hrDeviceIndex": (HR_DEVICE_DESCR,)}
PREFIX_NAMES = {"1.3.6.1.2.1.99.1.1": (ENT_NAME, ENT_DESCR), "1.3.6.1.2.1.25.3.3": (HR_DEVICE_DESCR,)}

NAME_WORDS = ("name", "descr", "label", "location", "alias")
STATUS_WORDS = ("status", "state", "oper", "condition", "health")
KIND_WORDS = ("type", "unit")
FAMILY_KIND_LABELS = {"temperature": ("celsius", "fahrenheit", "temperature", "temp"), "fan": ("rpm", "fan")}
NAME_KIND = {
    "temperature": re.compile(r"temp|therm|celsius|°c|\bdts\b|\bsensor ?\d*c\b", re.I),
    "fan": re.compile(r"fan|rpm|blower", re.I),
    "voltage": re.compile(r"volt|\bv(cc|dd|in|out|bat|core|tt|pp)|vcc|vdd|vpp|\d+v\d+|\+?\d+(\.\d+)?\s?v\b|\bmv\b",
                          re.I),
    "current/power": re.compile(r"current|\bamp|watt|power|\bpsu\b|\bpwr\b", re.I),
    "status": re.compile(r"status|state|alarm|present", re.I),
}
# In a mixed column, fan rows are named as fans; temperature chips often have neutral names (TD1, LM75 U75),
# so only fan rows need their family in the name.
STRICT_NAME = {"fan"}
USED = ("used", "alloc", "busy", "inuse")
FREE = ("free", "avail", "unused")
TOTAL = ("total", "size", "capacity")
NUMERIC_TYPES = {"gauge", "counter"}

# Severity from the MIB's own enum label. Checked in this order; anything else is "unknown".
ABSENT_LABELS = ("notpresent", "notinstalled", "absent", "missing", "removed", "notavailable")
WARN_FIRST = ("noncritical",)
CRIT_LABELS = ("nonoperational", "nonrecoverable", "critical", "fail", "fault", "broken", "shutdown",
               "alarm", "error", "bad")
WARN_LABELS = ("warning", "degraded", "minor", "marginal")
OK_LABELS = ("normal", "good", "running", "nominal", "online", "operational")
OK_TOKENS = ("ok", "up")
CRIT_TOKENS = ("down",)


def meaning(label: str | None) -> str:
    """ok | warning | critical | absent | unknown, from the label text only."""
    if not label:
        return "unknown"
    flat = re.sub(r"[^a-z]", "", label.lower())
    tokens = [t.lower() for t in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", label)]
    if any(w in flat for w in ABSENT_LABELS):
        return "absent"
    if any(w in flat for w in WARN_FIRST):
        return "warning"
    if any(w in flat for w in CRIT_LABELS) or any(t in CRIT_TOKENS for t in tokens):
        return "critical"
    if any(w in flat for w in WARN_LABELS):
        return "warning"
    if any(w in flat for w in OK_LABELS) or any(t in OK_TOKENS for t in tokens) or flat == "ok":
        return "ok"
    return "unknown"


def _norm(oid):
    return (oid or "").lstrip(".").removesuffix(".0")


def _short(name):
    return (name or "").split("::")[-1]


def _has(name, words):
    n = _short(name).lower()
    return any(w in n for w in words)


def _index(row_oid, col_oid):
    """Instance part of a row OID ("" for a scalar's .0)."""
    s = row_oid[len(col_oid):].lstrip(".")
    return "" if s == "0" else s


def _walk(snmp, oid):
    try:
        return snmp.walk(oid)
    except Exception:  # noqa: BLE001 - a missing name column must never break the profile
        return []


def _column(snmp, oid):
    """{index: Var} for one column or scalar."""
    return {_index(v.oid, oid): v for v in _walk(snmp, oid)}


class Mib:
    """Lookups over the Reader's candidate list (the MIB objects of the chosen modules)."""

    def __init__(self, cands):
        self.cands = cands or []
        self.by_oid = {_norm(c["oid"]): c for c in self.cands}

    def get(self, oid):
        return self.by_oid.get(_norm(oid)) or {}

    def siblings(self, oid):
        """Other objects of the same table row, or other scalars under the same parent."""
        me = self.get(oid)
        parent = me.get("entry") or _norm(oid).rsplit(".", 1)[0]
        return [c for c in self.cands if _norm(c["oid"]) != _norm(oid)
                and (c.get("entry") or _norm(c["oid"]).rsplit(".", 1)[0]) == parent
                and c.get("kind") == (me.get("kind") or c.get("kind"))]

    def sibling(self, oid, words, want=None):
        """Best sibling whose name contains one of `words` (earlier words win); want filters by kind."""
        for w in words:
            for c in self.siblings(oid):
                if w in c["name"].lower() and (want is None or want(c)):
                    return c
        return None


def _is_string(c):
    return (c.get("type") or "").lower() in ("displaystring", "snmpadminstring", "octet string", "octetstring",
                                              "string") or not (c.get("enums") or c.get("type", "").lower()
                                                                .startswith(("int", "gauge", "counter", "unsigned")))


def names_for(snmp, mib: Mib, oid):
    """({index: name}, source, name column OID) from the row's own name column, else the standard table
    its index points at."""
    col = mib.sibling(oid, NAME_WORDS, want=lambda c: _is_string(c) and not c.get("enums"))
    sources = [(_norm(col["oid"]), f"{col['module']}::{col['name']}")] if col else []
    idx = (mib.get(oid).get("index") or [None])[0]
    std = INDEX_NAMES.get(idx) or next((v for p, v in PREFIX_NAMES.items() if _norm(oid).startswith(p)), ())
    sources += [(o, {ENT_NAME: "entPhysicalName", ENT_DESCR: "entPhysicalDescr",
                     HR_DEVICE_DESCR: "hrDeviceDescr"}[o]) for o in std]
    for src_oid, label in sources:
        got = {i: v.value.strip() for i, v in _column(snmp, src_oid).items() if v.value.strip()}
        if got:
            return got, label, src_oid
    return {}, None, None


def _enum_label(enums, raw):
    if not enums or raw is None:
        return None
    for label, code in enums.items():
        if code == int(raw):
            return label
    return None


def _in_ranges(raw, ranges):
    return any((r.get("min") is None or raw >= r["min"]) and (r.get("max") is None or raw <= r["max"])
               for r in ranges)


def read_rows(snmp, mib: Mib, family, pick, scale, unit, value_type):
    """Read the pick's rows with names, status and the validity filter. Returns the profile fields."""
    oid = _norm(pick.get("oid"))
    me = mib.get(oid)
    vals = _column(snmp, oid)
    names, name_src, names_oid = names_for(snmp, mib, oid)

    status_col = None
    if me.get("enums") and value_type == "status":
        status_col = me
    else:
        status_col = mib.sibling(oid, STATUS_WORDS, want=lambda c: bool(c.get("enums")))
    status = _column(snmp, _norm(status_col["oid"])) if status_col and status_col is not me else {}

    kind_col = mib.sibling(oid, KIND_WORDS, want=lambda c: bool(c.get("enums")) and any(
        any(w in lbl.lower() for words in FAMILY_KIND_LABELS.values() for w in words) for lbl in c["enums"]))
    kinds = _column(snmp, _norm(kind_col["oid"])) if kind_col and family in FAMILY_KIND_LABELS else {}

    per_row_scale = {}
    au = mib.sibling(oid, ("allocationunits", "blocksize"))
    if au and family == "memory":
        per_row_scale = {i: number(v) for i, v in _column(snmp, _norm(au["oid"])).items() if number(v)}

    ram_only, unfiltered = {}, False
    if family == "memory" and oid.startswith(HR_STORAGE + "."):
        ram_only = {i: v.value.lstrip(".") == HR_STORAGE_RAM for i, v in _column(snmp, HR_STORAGE_TYPE).items()}
        unfiltered = not ram_only     # type column not served: disks and RAM cannot be told apart

    # One column holding several sensor kinds with no kind column (FortiGate fgHwSensorEntValue: voltages,
    # fans and temperatures together): when some row names say this family, rows whose names positively
    # say another kind are skipped. A column whose names never say the family is left alone.
    by_name = (family in NAME_KIND and not kinds and len(vals) > 1
               and any(NAME_KIND[family].search(n) for n in names.values()))

    mib_ranges = me.get("mib_range") or me.get("range")
    phys = RANGE.get(unit)
    rows, skipped = [], []
    for idx, v in sorted(vals.items(), key=lambda kv: [int(x) if x.isdigit() else x for x in kv[0].split(".")]):
        raw = number(v)
        row = {"index": idx, "name": names.get(idx), "raw": v.value if raw is None else raw}

        def skip(reason):
            skipped.append({**row, "reason": reason})

        if ram_only and not ram_only.get(idx, False):
            skip("not RAM (hrStorageType is not hrStorageRam)")
            continue
        if raw is None:
            skip(f"no numeric value ({v.type})")
            continue
        if me.get("enums") and status_col is not me and _enum_label(me["enums"], raw) is None:
            skip(f"{raw:g} is not one of the MIB's defined values")
            continue
        if mib_ranges and not _in_ranges(raw, mib_ranges):
            r = mib_ranges[0]
            skip(f"{raw:g} is outside the MIB's declared range {r.get('min')}..{r.get('max')}")
            continue
        if kinds:
            kv = kinds.get(idx)
            lbl = _enum_label(kind_col["enums"], number(kv)) if kv is not None else None
            if lbl and not any(w in lbl.lower() for w in FAMILY_KIND_LABELS[family]):
                skip(f"another sensor kind ({kind_col['name']} = {lbl})")
                continue
        elif by_name and not NAME_KIND[family].search(row["name"] or ""):
            other = next((f for f, rx in NAME_KIND.items() if f != family and rx.search(row["name"] or "")), None)
            if other:   # only a name that positively says another kind; neutral names (TD1, LM75) stay
                skip(f"named as another sensor kind ({other})")
                continue
            if family in STRICT_NAME:
                skip(f"its name does not say {family}, while other rows in this column do")
                continue
        st = None
        if status_col is me:
            lbl = _enum_label(me["enums"], raw)
            st = {"raw": int(raw), "label": lbl, "meaning": meaning(lbl)}
        elif status:
            sv = status.get(idx)
            sraw = number(sv) if sv is not None else None
            lbl = _enum_label(status_col["enums"], sraw)
            st = {"raw": None if sraw is None else int(sraw), "label": lbl, "meaning": meaning(lbl)}
            if st["meaning"] == "absent":
                skip(f"sensor reported absent ({status_col['name']} = {lbl})")
                continue
        s = per_row_scale.get(idx, scale)
        value = None if value_type == "counter" or s is None or status_col is me else raw * s
        if value is not None and phys and status_col is not me and not (phys[0] <= value <= phys[1]):
            skip(f"{raw:g} x {s:g} = {value:g} is outside the physical range {phys[0]}..{phys[1]} {unit}")
            continue
        row["value"] = value
        if s != scale:
            row["scale"] = s
        if st:
            row["status"] = st
        rows.append(row)

    return {
        "rows": rows,
        "skipped": skipped,
        "partial": bool(skipped) and bool(rows),
        "names_from": name_src,
        "names_oid": names_oid,
        "scalar": len(vals) == 1 and "" in vals,
        "status_enum": ({str(code): {"label": lbl, "meaning": meaning(lbl)} for lbl, code in
                         (status_col or {}).get("enums", {}).items()} if status_col and status_col.get("enums")
                        else None),
        "status_from": (f"{status_col['module']}::{status_col['name']}" if status_col else None),
        "status_oid": (_norm(status_col["oid"]) if status_col and status_col is not me else None),
        "kind_filter_from": (f"{kind_col['module']}::{kind_col['name']}" if kinds else None),
        "valid_range": {"mib": mib_ranges, "physical": list(phys) if phys else None},
        **({"unfiltered": True, "note": "hrStorageType not read: rows are not filtered to RAM and may include disks"}
           if unfiltered else {}),
    }


def _num_col(c):
    t = (c.get("type") or "").lower()
    return not c.get("enums") and any(t.startswith(x) for x in ("int", "gauge", "counter", "unsigned", "kbytes",
                                                                     "cpqkbytes", "float"))


def memory_partner(mib: Mib, pick):
    """The second memory object that makes used/free/total complete, from the same row or parent."""
    oid, name = _norm(pick.get("oid")), pick.get("object_name")
    if _has(name, USED):
        order = (("free", FREE), ("total", TOTAL))
    elif _has(name, FREE):
        order = (("used", USED), ("total", TOTAL))
    elif _has(name, TOTAL):
        order = (("used", USED), ("free", FREE))
    else:
        return None, None
    stem = _stem(name)
    found = [(rank, role, c) for rank, (role, words) in enumerate(order)
             for c in mib.siblings(oid) if _num_col(c) and _has(c["name"], words)]
    if not found:
        return None, None
    # Same name once the role word is removed wins over role order (memAvailReal pairs with
    # memTotalReal, not memUsedRealTXT, memTotalFree or memTotalSwap); objects naming two roles
    # at once are a poor match; then the preferred role; then the closest name.
    rank, role, best = max(found, key=lambda x: (_stem(x[2]["name"]) == stem,
                                                 -sum(_has(x[2]["name"], g) for g in (USED, FREE, TOTAL)),
                                                 -x[0], _common(_stem(x[2]["name"]), stem)))
    return role, best


def _stem(name):
    n = _short(name).lower()
    for w in USED + FREE + TOTAL:
        n = n.replace(w, "")
    return n


def _common(a, b):
    p = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    s = next((i for i, (x, y) in enumerate(zip(a[::-1], b[::-1])) if x != y), min(len(a), len(b)))
    return p + s


def _role(name):
    return "used" if _has(name, USED) else "free" if _has(name, FREE) else "total" if _has(name, TOTAL) else None


def _memory(snmp, mib, pick, base):
    """Kind and used/free/total for memory."""
    if (pick.get("canonical_unit") or "") == "percent":
        base["kind"] = "percent"
        return base
    me_role = _role(pick.get("object_name"))
    p_role, partner = memory_partner(mib, pick)
    if not partner:
        base["kind"] = f"{me_role or 'single value'} only"
        base["note"] = "no matching used/free/total object found in the same row or group"
        return base
    roles = {me_role, p_role}
    base["role"] = me_role
    base["kind"] = "used+free" if roles == {"used", "free"} else "used+total"
    if roles == {"free", "total"}:
        base["kind"] = "used+total"
        base["note"] = "; ".join(filter(None, [base.get("note"), "used = total - free"]))
    scale = pick.get("scale_to_canonical") or 1
    p_rows = _column(snmp, _norm(partner["oid"]))
    au = mib.sibling(pick.get("oid"), ("allocationunits", "blocksize"))
    per_row = ({i: number(v) for i, v in _column(snmp, _norm(au["oid"])).items()} if au else {})
    for r in base["rows"]:
        pv = p_rows.get(r["index"])
        praw = number(pv) if pv is not None else None
        if praw is None or r.get("value") is None:
            continue
        s = per_row.get(r["index"]) or r.get("scale") or scale
        got = {me_role: r["value"], p_role: praw * s}
        used = got.get("used", (got.get("total") or 0) - (got.get("free") or 0))
        total = got.get("total", (got.get("used") or 0) + (got.get("free") or 0))
        r.update(used_bytes=used, total_bytes=total, free_bytes=total - used,
                 percent=round(100 * used / total, 1) if total else None)
    base["partner"] = {"object": f"{partner['module']}::{partner['name']}", "oid": _norm(partner["oid"]),
                       "role": p_role}
    if len(base["rows"]) > 1 and not base.get("unfiltered"):
        base["pool_kind"] = base["kind"]
        base["kind"] = "pools"
    return base


def prove_memory(snmp, prof, load=None, base_s=10, load_s=20, interval=2):
    """Lab load test of a memory pair: allocate min(1 GiB, 25% of RAM); used must rise (free fall) by
    about the allocation, and total must stay put. Adds evidence to prof["proof"]."""
    partner = prof.get("partner")
    if not partner or not prof.get("rows"):
        return prof
    load = load or verify.default_load
    pick_oid, part_oid = prof["oid"], partner["oid"]
    idx = prof["rows"][0]["index"]
    scale = prof["rows"][0].get("scale") or prof.get("scale") or 1
    total_kb = verify._meminfo_kb() or 4 * 1024 * 1024
    alloc = int(min(1024 ** 3, total_kb * 1024 * 0.25))

    def sample(phase, seconds):
        verify.PHASE["name"] = phase
        out, t0 = [], verify.time.time()
        while True:
            a = _column(snmp, pick_oid).get(idx)
            b = _column(snmp, part_oid).get(idx)
            if a is not None and b is not None and number(a) is not None and number(b) is not None:
                out.append((number(a) * scale, number(b) * scale))
            if verify.time.time() - t0 >= seconds and len(out) >= 2:
                return out
            if verify.time.time() - t0 >= seconds * 3:
                return out
            verify.time.sleep(interval)

    def mean(xs, i):
        return sum(x[i] for x in xs) / len(xs) if xs else None

    base = sample("profile-base", base_s)
    try:
        proc = load("memory", load_s + 10, alloc)
    except verify.NotInducible as why:
        prof["proof"].append({"check": "memory-load", "ok": None, "detail": str(why)})
        return prof
    try:
        verify.time.sleep(min(6, load_s / 2))
        during = sample("profile-load", load_s)
    finally:
        proc.terminate()
        proc.wait(timeout=15)
        verify.PHASE["name"] = "profile"
    if not base or not during:
        prof["proof"].append({"check": "memory-load", "ok": None, "detail": "no paired readings during the test"})
        return prof
    me_role, p_role = _role(prof["object"]), partner["role"]
    moves = {me_role: mean(during, 0) - mean(base, 0), p_role: mean(during, 1) - mean(base, 1)}
    level = {me_role: mean(base, 0), p_role: mean(base, 1)}
    checks = []
    if "used" in moves:
        r = moves["used"] / alloc
        checks.append(("used", 0.3 <= r <= 3, f"used {moves['used']:+,.0f} bytes (ratio {r:.2g})"))
    if "free" in moves:
        r = -moves["free"] / alloc
        checks.append(("free", 0.3 <= r <= 3, f"free {moves['free']:+,.0f} bytes (ratio {r:.2g})"))
    if "total" in moves:
        rel = abs(moves["total"]) / max(level["total"] or 1, 1)
        checks.append(("total", rel <= 0.02, f"total {moves['total']:+,.0f} bytes ({rel:.1%} change)"))
    ok = all(c[1] for c in checks)
    prof["proof"].append({"check": "memory-load", "ok": ok,
                          "detail": f"allocated {alloc / 1024 ** 2:.0f} MiB: " + "; ".join(c[2] for c in checks)})
    if ok:
        prof["load_tested"] = True
    return prof


VERSION_NAMES = (("firmwareversion", "firmwarerev", "fwversion", "fwrev"),
                 ("softwareversion", "softwarerev", "swversion", "swrev", "osversion", "sysversion"),
                 ("firmware", "software"), ("version",))
# Names that look like a version but are something else (serials, hardware revisions, upgrade targets).
NOT_VERSION = ("serial", "mac", "date", "time", "hardware", "hwrev", "hwversion", "pcb", "upgrade", "update",
               "newversion", "latest", "mib", "protocol", "snmp", "tftp", "http", "url", "path", "file")


HR_FIXED_DISK = "1.3.6.1.2.1.25.2.1.4"


def disk(snmp):
    """Fixed disks from the standard hrStorageTable (RFC 2790), read from the device. No model involved:
    the objects are fixed by the standard. Rows with used > size or no allocation unit are skipped."""
    types = _column(snmp, HR_STORAGE_TYPE)
    if not types:
        return None
    names, units = _column(snmp, HR_STORAGE + ".3"), _column(snmp, HR_STORAGE + ".4")
    size, used = _column(snmp, HR_STORAGE + ".5"), _column(snmp, HR_STORAGE + ".6")
    rows, skipped = [], []
    for idx, t in sorted(types.items(), key=lambda kv: [int(x) if x.isdigit() else x for x in kv[0].split(".")]):
        if t.value.lstrip(".") != HR_FIXED_DISK:
            continue
        name = names[idx].value.strip() if idx in names else None
        au, sz, us = (number(d[idx]) if idx in d else None for d in (units, size, used))
        row = {"index": idx, "name": name}
        if not au or not sz or us is None:
            skipped.append({**row, "reason": "size, used or allocation unit not served"})
        elif us > sz:
            skipped.append({**row, "reason": f"used {us:g} is larger than size {sz:g}"})
        else:
            rows.append({**row, "used_bytes": us * au, "total_bytes": sz * au, "percent": round(100 * us / sz, 1)})
    if not rows and not skipped:
        return None
    return {"verdict": "standard-mib", "object": "HOST-RESOURCES-MIB::hrStorageUsed/hrStorageSize", "unit": "percent",
            "kind": "disks" if rows else None, "rows": rows, "skipped": skipped,
            "partial": bool(skipped) and bool(rows), "names_from": "hrStorageDescr",
            "note": "fixed disks from the standard hrStorageTable; used <= size checked"}


def _version_objects(mib):
    """The device MIB's own scalar strings named like a firmware/software version, best names first."""
    if not mib:
        return []
    out = []
    for words in VERSION_NAMES:
        for c in mib.cands:
            n = c["name"].lower()
            if c.get("kind") != "scalar" or not _is_string(c) or c.get("enums"):
                continue
            if any(w in n for w in NOT_VERSION):
                continue
            if any(w in n for w in words) and c not in out:
                out.append(c)
    return out


def firmware(snmp, descr, mib=None):
    """(value, source): ENTITY-MIB software/firmware revision, a version object in the device's own MIB,
    else a version pattern in sysDescr."""
    for oid, label in ((ENT_SOFTWARE, "entPhysicalSoftwareRev"), (ENT_FIRMWARE, "entPhysicalFirmwareRev")):
        for v in _walk(snmp, oid):
            if v.value.strip():
                return v.value.strip(), label
    for c in _version_objects(mib):
        for v in _walk(snmp, _norm(c["oid"])):
            if v.type in ("STRING", "Hex-STRING") and re.search(r"\d", v.value) and len(v.value) <= 80:
                return v.value.strip(), f"{c['module']}::{c['name']}"
    m = re.search(r"(?:version|ver\.?|release|firmware|RouterOS|v)\s*:?\s*(\d+(?:\.\d+)+[\w.()-]*)", descr or "", re.I) \
        or re.search(r"\b(\d+\.\d+(?:\.\d+)*[\w()-]*)", descr or "")
    return (m.group(1), "sysDescr (version pattern)") if m else (None, None)


def provenance(snmp, modules=None, mib=None):
    def one(oid):
        try:
            got = [v for v in snmp.get(oid) if v.type != "missing"]
        except Exception:  # noqa: BLE001
            got = []
        return got[0].value.strip() if got else None
    descr, obj = one(SYS_DESCR), one(SYS_OBJECT_ID)
    fw, src = firmware(snmp, descr, mib)
    return {"sysObjectID": obj.lstrip(".") if obj else None, "sysDescr": descr,
            "firmware": fw, "firmware_from": src, "modules": modules or [],
            "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}


def _cpu(base, pick):
    rows = base["rows"]
    if (pick.get("value_type") or "") == "counter":
        base["kind"] = "overall"
        base["note"] = "CPU time counter: the poller computes the rate between two polls"
        return base
    if len(rows) > 1:
        base["kind"] = "per-core"
        vals = [r["value"] for r in rows if r.get("value") is not None]
        if vals:
            base["overall"] = {"value": round(sum(vals) / len(vals), 2), "how": f"mean of {len(vals)} cores"}
    else:
        base["kind"] = "overall"
        if rows and rows[0].get("value") is not None:
            base["overall"] = {"value": rows[0]["value"], "how": "single value"}
    return base


def _sensor(base):
    rows = base["rows"]
    base["kind"] = "status" if rows and all("status" in r and r.get("value") is None for r in rows) else \
        ("sensors" if len(rows) > 1 else "single sensor")
    return base


def build(snmp, spec, cands, modules=None, lab=False, load=None):
    """The verified device profile for one onboarding result (agent.onboard()["spec"])."""
    mib = Mib(cands)
    saved = verify.PHASE["name"]
    verify.PHASE["name"] = "profile"
    try:
        out = {"provenance": provenance(snmp, modules, mib), "metrics": {}}
        for fam, fin in (spec or {}).items():
            pick = fin.get("pick") or {}
            oid = _norm(pick.get("oid"))
            if not oid:
                out["metrics"][fam] = {"verdict": fin.get("verdict"), "kind": None, "note": "no object chosen"}
                continue
            c = mib.get(oid)
            unit = pick.get("canonical_unit") or "none"
            scale = pick.get("scale_to_canonical")
            vt = pick.get("value_type") or "gauge"
            if fam == "fan" and c.get("enums") and unit not in ("rpm", "percent"):
                unit, vt = "status", "status"
            base = {"verdict": fin.get("verdict"), "object": f"{c['module']}::{c['name']}" if c else
                    pick.get("object_name"), "oid": oid, "scale": scale, "unit": unit, "value_type": vt,
                    "mib_units": c.get("units"), "display_hint": c.get("display_hint"),
                    "proof": [e for e in fin.get("evidence") or [] if e.get("ok") is not None]}
            if fin.get("verdict") == "not-supported":
                base.update(kind=None, rows=[], skipped=[], partial=False,
                            note="not served by this device: kept as the MIB's answer, nothing to poll here")
                out["metrics"][fam] = base
                continue
            base.update(read_rows(snmp, mib, fam, pick, scale, unit, vt))
            base["load_tested"] = any(e.get("check") == "experiment" and e.get("ok") for e in base["proof"])
            if fam == "cpu":
                _cpu(base, pick)
            elif fam == "memory":
                _memory(snmp, mib, pick, base)
                if lab and base.get("partner"):
                    prove_memory(snmp, base, load=load)
            else:
                _sensor(base)
            if not base["rows"]:
                base["kind"] = None   # a kind is only stated from rows actually read
                base["note"] = "no valid rows: every row was skipped (see skipped)" if base["skipped"] else \
                    "no rows read"
            out["metrics"][fam] = base
        d = disk(snmp)
        if d:
            out["metrics"]["disk"] = d
        return out
    finally:
        verify.PHASE["name"] = saved
