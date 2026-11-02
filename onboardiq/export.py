"""Export an onboarding to the monitoring tools teams already run: Prometheus (snmp_exporter) and Zabbix.

Only what the onboarding proved is exported, plus the standard objects the health score already uses
(hrProcessorLoad per core, fixed disks from hrStorageTable). Alert limits are not invented here: they are
the points where OnboardIQ's own health score changes level (health_policy.json), so Prometheus alerts and
Zabbix triggers fire exactly where the console would turn "Under strain" or "In trouble".

Nothing is read from the device: the input is a saved onboarding (results/onboard_*.json: its "profile").
"""
import re
import uuid

from . import health
from .profile import HR_DEVICE_DESCR, HR_LOAD, HR_STORAGE

PROVEN = ("proven-experiment", "proven-witness", "proven-static")
HR_STORAGE_DESCR, HR_STORAGE_UNITS = HR_STORAGE + ".3", HR_STORAGE + ".4"
HR_STORAGE_SIZE, HR_STORAGE_USED = HR_STORAGE + ".5", HR_STORAGE + ".6"
INDEX_NAME = {HR_LOAD: "hrDeviceIndex", HR_STORAGE: "hrStorageIndex"}
UNITS = {"percent": "%", "bytes": "B", "celsius": "C", "rpm": "rpm", "ticks": "ticks"}


# ------------------------------------------------------------------ what to export

def level_limits(family, policy=None):
    """(warning_at, critical_at) for a family: the reading where the health score drops below the
    'ok' and 'warning' level cut-offs. None for families the policy scores another way (fan, status)."""
    pol = policy or health.DEFAULT
    rule = pol["rules"].get(family)
    if not rule:
        return None
    ok, (mx, ms), zero = rule["ok"], rule["mid"], rule["zero"]

    def value_at(score):            # the inverse of health._line (straight pieces, falling)
        if score >= 100:
            return ok
        if score >= ms:
            return ok + (100 - score) * (mx - ok) / (100 - ms)
        return zero - score * (zero - mx) / ms
    lv = pol["levels"]
    return round(value_at(lv["ok_from"]), 2), round(value_at(lv["warning_from"]), 2)


def _short(obj):
    return (obj or "").split("::")[-1] or "value"


def _indexes(rows):
    return sorted({str(r["index"]) for r in rows or [] if str(r.get("index", "")) not in ("",)},
                  key=lambda s: [int(x) if x.isdigit() else x for x in s.split(".")])


def series(saved, cands=None):
    """The metrics to export, in one neutral shape both exporters read. Each:
    {family, name, oid, table, index_label, names_oid, type, multiplier, units_oid, rows, unit, percent}
    where `percent` (if any) says how the health percentage is formed from this and a partner object."""
    prof = (saved.get("profile") or {}).get("metrics") or {}
    by_oid = {c["oid"]: c for c in cands or []}
    out = []

    def label_for(oid):
        c = by_oid.get(oid) or {}
        idx = c.get("index") or []
        if len(idx) == 1 and not idx[0].startswith("augments:"):
            return idx[0]
        return INDEX_NAME.get(oid.rsplit(".", 2)[0]) or INDEX_NAME.get(oid.rsplit(".", 1)[0]) or "index"

    for fam, m in prof.items():
        if fam in ("cpu_load", "disk"):
            continue
        if m.get("verdict") not in PROVEN or not m.get("oid") or not m.get("rows"):
            continue
        oid, table = m["oid"], not m.get("scalar")
        vt = m.get("value_type") or "gauge"
        s = {"family": fam, "name": _short(m.get("object")), "oid": oid, "table": table,
             "index_label": label_for(oid) if table else None, "names_oid": m.get("names_oid") if table else None,
             "type": "counter" if vt == "counter" else "gauge", "multiplier": m.get("scale"),
             "units_oid": None, "rows": _indexes(m["rows"]) if table else None,
             "unit": UNITS.get(m.get("unit"), m.get("unit")), "status": vt == "status", "percent": None,
             "verdict": m.get("verdict")}
        if vt == "counter":
            s["unit"] = f"{s['unit']}/s"
            s["multiplier"] = None
        if fam == "memory" and isinstance(m.get("partner"), dict):
            p = m["partner"]
            s["percent"] = {"role": m.get("role"), "partner_oid": p["oid"], "partner_role": p.get("role"),
                            "partner_name": _short(p.get("object"))}
            if m.get("unit") == "bytes" and oid.startswith(HR_STORAGE + "."):
                s["units_oid"], s["multiplier"] = HR_STORAGE_UNITS, None   # bytes = value x allocation units
        out.append(s)

    cl = prof.get("cpu_load")
    if cl and cl.get("rows"):
        out.append({"family": "cpu_load", "name": "hrProcessorLoad", "oid": HR_LOAD, "table": True,
                    "index_label": "hrDeviceIndex", "names_oid": HR_DEVICE_DESCR, "type": "gauge",
                    "multiplier": None, "units_oid": None, "rows": _indexes(cl["rows"]), "unit": "%",
                    "status": False, "percent": None, "verdict": "standard-mib"})
    dk = prof.get("disk")
    if dk and dk.get("rows"):
        out.append({"family": "disk", "name": "hrStorageUsed", "oid": HR_STORAGE_USED, "table": True,
                    "index_label": "hrStorageIndex", "names_oid": HR_STORAGE_DESCR, "type": "gauge",
                    "multiplier": None, "units_oid": HR_STORAGE_UNITS, "rows": _indexes(dk["rows"]), "unit": "B",
                    "status": False, "verdict": "standard-mib",
                    "percent": {"role": "used", "partner_oid": HR_STORAGE_SIZE, "partner_role": "total",
                                "partner_name": "hrStorageSize"}})
    return out


def _device(saved):
    return str(saved.get("device") or (saved.get("args") or {}).get("device") or "device")[:120]


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "device"


def _pct_parts(p):
    """(numerator, denominator) of used/total as functions of (me, partner) expressions."""
    roles = {p["role"]: "@A@", p["partner_role"]: "@B@"}
    if {"used", "total"} <= roles.keys():
        return f"{roles['used']}", f"{roles['total']}"
    if {"free", "total"} <= roles.keys():
        return f"({roles['total']} - {roles['free']})", f"{roles['total']}"
    if {"used", "free"} <= roles.keys():
        return f"{roles['used']}", f"({roles['used']} + {roles['free']})"
    return None


# ------------------------------------------------------------------ Prometheus (snmp_exporter)

def snmp_exporter(saved, cands=None, community_auth="public_v2"):
    """(snmp.yml module dict, alert rules dict). The module is ready for snmp_exporter (no MIBs or generator
    needed); the rules use the health score's level cut-offs."""
    dev, mod = _device(saved), f"onboardiq_{slug(_device(saved))}"
    walk, get, metrics, rules = [], [], [], []
    have = set()

    def metric(name, oid, typ, help_, table, label, names_oid, scale=None):
        if name in have:
            return
        have.add(name)
        m = {"name": name, "oid": oid, "type": typ, "help": help_[:300]}
        if table:
            m["indexes"] = [{"labelname": label, "type": "gauge"}]
            if names_oid:
                m["lookups"] = [{"labels": [label], "labelname": "name", "oid": names_oid, "type": "DisplayString"}]
                if names_oid not in walk:
                    walk.append(names_oid)
            if oid not in walk:
                walk.append(oid)
        else:
            get.append(oid + ".0")
        if scale not in (None, 1, 1.0):
            m["scale"] = scale
        metrics.append(m)

    for s in series(saved, cands):
        proof = "proven on the device by OnboardIQ" if s["verdict"] in PROVEN else "standard object"
        unit = "allocation units (bytes = value x hrStorageAllocationUnits)" if s["units_oid"] else s["unit"]
        metric(s["name"], s["oid"], s["type"], f"{s['family']} ({unit}): {proof}", s["table"],
               s["index_label"], s["names_oid"], s["multiplier"])
        if s["units_oid"]:
            metric("hrStorageAllocationUnits", s["units_oid"], "gauge", "bytes per allocation unit", s["table"],
                   s["index_label"], s["names_oid"])
        # the rows the onboarding kept (RAM only, real disks, ...); scalars need no selector
        sel = (f'{{{s["index_label"]}=~"{"|".join(map(re.escape, s["rows"]))}"}}' if s["table"] and s["rows"] else "")
        expr = None
        if s["percent"]:
            p = s["percent"]
            metric(p["partner_name"], p["partner_oid"], "gauge", f"{s['family']} partner ({p['partner_role']})",
                   s["table"], s["index_label"], s["names_oid"], s["multiplier"])
            parts = _pct_parts(p)
            if parts:
                a, b = f"{s['name']}{sel}", f"{p['partner_name']}{sel}"
                num, den = (x.replace("@A@", a).replace("@B@", b) for x in parts)
                expr = f"100 * {num} / {den}"
        elif s["unit"] in ("%", "C") and not s["status"]:
            expr = f"{s['name']}{sel}"
        elif s["unit"] == "rpm":
            stopped = health.DEFAULT["fan"]["stopped_at_or_below_rpm"]
            rules.append({"alert": f"OnboardIQ{s['family'].title()}Stopped", "expr": f"{s['name']}{sel} <= {stopped}",
                          "for": "2m", "labels": {"severity": "critical"},
                          "annotations": {"summary": f"{dev}: fan {{{{ $labels.name }}}} stopped"}})
        lim = level_limits(s["family"]) if expr else None
        if lim:
            unit = "°C" if s["unit"] == "C" else "%"
            what = {"cpu": "CPU", "cpu_load": "CPU load", "memory": "memory", "disk": "disk",
                    "temperature": "temperature"}.get(s["family"], s["family"])
            for level, at, sev in (("UnderStrain", lim[0], "warning"), ("InTrouble", lim[1], "critical")):
                rules.append({"alert": f"OnboardIQ{s['family'].title().replace('_', '')}{level}",
                              "expr": f"{expr} > {at:g}", "for": "5m", "labels": {"severity": sev},
                              "annotations": {"summary": f"{dev}: {what} above {at:g}{unit} "
                                                         f"({'under strain' if sev == 'warning' else 'in trouble'})",
                                              "description": "Same limit at which the OnboardIQ health score "
                                                             "changes level (health_policy.json)."}})
    module = {"walk": walk, **({"get": get} if get else {}), "metrics": metrics}
    snmp_yml = {"auths": {community_auth: {"community": "public", "version": 2}}, "modules": {mod: module}}
    rules_yml = {"groups": [{"name": f"onboardiq-{slug(dev)}", "rules": rules}]}
    return snmp_yml, rules_yml


# ------------------------------------------------------------------ Zabbix

def _uuid(*parts):
    """A stable UUIDv4-shaped id (Zabbix requires v4 format), so re-exports update instead of duplicating."""
    u = uuid.uuid5(uuid.NAMESPACE_URL, "onboardiq:" + ":".join(parts)).hex
    return u[:12] + "4" + u[13:16] + "89ab"[int(u[16], 16) % 4] + u[17:]


def zabbix(saved, cands=None, policy=None):
    """A Zabbix 6.0 template (importable into 6.0 and later) as a dict ready for YAML."""
    pol = policy or health.DEFAULT
    dev = _device(saved)
    # Zabbix host names allow only letters, digits, space, dot, dash and underscore (no #, (), : and so on).
    short = re.split(r"\s[#(]", dev)[0]                      # drop the kernel build and sysObjectID tail
    tname = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ._-]+", " ", f"OnboardIQ {short}")).strip()
    if len(tname) > 60:
        tname = tname[:60].rsplit(" ", 1)[0]                   # cut at a word, not mid-word
    group = "Templates/OnboardIQ"
    items, rules, macros = [], [], {}

    def macro(fam, level, value):
        key = f"{{$OIQ.{fam.upper()}.{level}}}"
        macros[key] = f"{value:g}"
        return key

    def triggers(fam, key, label, unit, proto):
        lim = level_limits(fam, pol)
        if not lim:
            return []
        w, c = macro(fam, "WARN", lim[0]), macro(fam, "CRIT", lim[1])
        return [
            {"uuid": _uuid(tname, key, "warn"), "expression": f"min(/{tname}/{key},5m)>{w}",
             "name": f"{label} above {w}{unit} (under strain)", "priority": "WARNING",
             "dependencies": [{"name": f"{label} above {c}{unit} (in trouble)",
                               "expression": f"min(/{tname}/{key},5m)>{c}"}]},
            {"uuid": _uuid(tname, key, "crit"), "expression": f"min(/{tname}/{key},5m)>{c}",
             "name": f"{label} above {c}{unit} (in trouble)", "priority": "HIGH"},
        ]

    def item(name, key, oid, value_type, units, desc, fam, multiplier=None, change=False, delay="1m"):
        it = {"uuid": _uuid(tname, key), "name": name, "type": "SNMP_AGENT", "snmp_oid": oid, "key": key,
              "delay": delay, "history": "7d", "value_type": value_type, "description": desc[:500],
              "tags": [{"tag": "component", "value": fam}]}
        if units:
            it["units"] = units
        pre = []
        if change:
            pre.append({"type": "CHANGE_PER_SECOND", "parameters": [""]})
        if multiplier not in (None, 1, 1.0):
            pre.append({"type": "MULTIPLIER", "parameters": [f"{multiplier:g}"]})
        if pre:
            it["preprocessing"] = pre
        return it

    def calc(name, key, formula, units, fam):
        return {"uuid": _uuid(tname, key), "name": name, "type": "CALCULATED", "key": key, "delay": "1m",
                "history": "7d", "value_type": "FLOAT", "units": units, "params": formula,
                "tags": [{"tag": "component", "value": fam}]}

    for s in series(saved, cands):
        fam, label = s["family"], {"cpu": "CPU", "cpu_load": "CPU load", "memory": "Memory", "disk": "Disk",
                                   "temperature": "Temperature", "fan": "Fan"}.get(s["family"], s["family"])
        proof = "Proven on the device by OnboardIQ." if s["verdict"] in PROVEN else "Standard MIB object."
        unit = {"%": "%", "C": "°C", "rpm": "rpm", "B": "B"}.get(s["unit"], s["unit"] or "")
        mult = s["multiplier"]
        if not s["table"]:
            key = f"oiq.{fam}"
            items.append(item(f"{label} ({s['name']})", key, s["oid"] + ".0", "FLOAT", unit, proof, fam,
                              mult, change=s["type"] == "counter"))
            if s["percent"]:
                p = s["percent"]
                pkey = f"oiq.{fam}.{p['partner_role']}"
                items.append(item(f"{label} {p['partner_role']} ({p['partner_name']})", pkey, p["partner_oid"] + ".0",
                                  "FLOAT", unit, proof, fam, mult))
                parts = _pct_parts(p)
                if parts:
                    num, den = (x.replace("@A@", f"last(//{key})").replace("@B@", f"last(//{pkey})") for x in parts)
                    pct = calc(f"{label} used, %", f"oiq.{fam}.pused", f"100*{num}/{den}", "%", fam)
                    pct["triggers"] = triggers(fam, f"oiq.{fam}.pused", f"{label} used", "%", False)
                    items.append(pct)
            elif unit in ("%", "°C") and not s["status"]:
                items[-1]["triggers"] = triggers(fam, key, label, unit, False)
            continue

        # a table: low-level discovery over the rows the onboarding kept, named from the MIB's name column
        name_m = "{#OIQNAME}"
        oids = f"{{#OIQVAL}},{s['oid']}" + (f",{name_m},{s['names_oid']}" if s["names_oid"] else "")
        filt = {"evaltype": "AND", "conditions": [{"macro": "{#OIQVAL}", "operator": "EXISTS", "formulaid": "A"}]}
        if s["rows"]:
            filt["conditions"].append({"macro": "{#SNMPINDEX}", "value": "^(" + "|".join(map(re.escape, s["rows"])) + ")$",
                                       "formulaid": "B"})
        rule = {"uuid": _uuid(tname, fam, "lld"), "name": f"{label} discovery", "type": "SNMP_AGENT",
                "snmp_oid": f"discovery[{oids}]", "key": f"oiq.{fam}.discovery", "delay": "1h",
                "filter": filt, "item_prototypes": [],
                "description": f"Rows kept at onboarding: {', '.join(s['rows'] or []) or 'all'}. {proof}"}
        rowname = f"{name_m}" if s["names_oid"] else "{#SNMPINDEX}"
        key = f"oiq.{fam}[{{#SNMPINDEX}}]"
        protos = rule["item_prototypes"]
        protos.append(item(f"{label} {rowname} ({s['name']})", key, f"{s['oid']}.{{#SNMPINDEX}}", "FLOAT",
                           "" if s["units_oid"] else unit, proof, fam, mult, change=s["type"] == "counter"))
        if s["percent"]:
            p = s["percent"]
            pkey = f"oiq.{fam}.{p['partner_role']}[{{#SNMPINDEX}}]"
            protos.append(item(f"{label} {rowname} {p['partner_role']} ({p['partner_name']})", pkey,
                               f"{p['partner_oid']}.{{#SNMPINDEX}}", "FLOAT", "", proof, fam, mult))
            parts = _pct_parts(p)
            if parts:
                num, den = (x.replace("@A@", f"last(//{key})").replace("@B@", f"last(//{pkey})") for x in parts)
                pk = f"oiq.{fam}.pused[{{#SNMPINDEX}}]"
                pct = calc(f"{label} {rowname} used, %", pk, f"100*{num}/{den}", "%", fam)
                pct["trigger_prototypes"] = triggers(fam, pk, f"{label} {rowname} used", "%", True)
                protos.append(pct)
        elif unit in ("%", "°C") and not s["status"]:
            protos[0]["trigger_prototypes"] = triggers(fam, key, f"{label} {rowname}", unit, True)
        elif s["unit"] == "rpm":
            stopped = pol["fan"]["stopped_at_or_below_rpm"]
            protos[0]["trigger_prototypes"] = [{"uuid": _uuid(tname, key, "stopped"),
                                                "expression": f"last(/{tname}/{key})<={stopped}",
                                                "name": f"Fan {rowname} stopped", "priority": "HIGH"}]
        rules.append(rule)

    template = {"uuid": _uuid(tname), "template": tname, "name": tname,
                "description": (f"Generated by OnboardIQ from a verified onboarding of {dev}. Every item was proven "
                                "on the device or is a standard MIB object. Trigger limits are the points where "
                                "the OnboardIQ health score changes level (health_policy.json)."),
                "groups": [{"name": group}], "items": items, "discovery_rules": rules,
                "macros": [{"macro": k, "value": v} for k, v in sorted(macros.items())]}
    # Zabbix rejects empty lists in an import
    for k in ("items", "discovery_rules", "macros"):
        if not template[k]:
            del template[k]
    return {"zabbix_export": {"version": "6.0", "groups": [{"uuid": _uuid(group), "name": group}],
                              "templates": [template]}}
