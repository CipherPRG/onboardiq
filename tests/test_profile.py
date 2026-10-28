"""Verified device profile against a fake device shaped like real tables (no SNMP agent)."""
from onboardiq import mib, profile
from onboardiq.snmp import Var


class Dev:
    """oid -> list of (suffix, type, value); walk returns every row under the oid."""
    def __init__(self, cols, scalars=None):
        self.cols, self.scalars, self.calls = cols, scalars or {}, 0

    def walk(self, oid):
        self.calls += 1
        if oid in self.scalars:
            t, v = self.scalars[oid]
            return [Var(oid + ".0", t, v)]
        return [Var(f"{oid}.{s}", t, v) for s, t, v in self.cols.get(oid, [])]

    def get(self, *oids):
        out = []
        for o in oids:
            base = o.removesuffix(".0")
            t, v = self.scalars.get(base, ("missing", "No Such Object"))
            out.append(Var(o, t, v))
        return out


def c(module, name, oid, kind="column", typ="Integer32", **kw):
    entry = oid.rsplit(".", 1)[0] if kind == "column" else None
    return {"module": module, "name": name, "oid": oid, "kind": kind, "type": typ, "units": None,
            "range": None, "status": "current", "description": "", "mib_range": None, "enums": None,
            "display_hint": None, "entry": entry, "index": None, **kw}


def fin(oid, name, unit, scale=1, vt="gauge", verdict="proven-static"):
    return {"verdict": verdict, "pick": {"oid": oid, "object_name": name, "canonical_unit": unit,
                                         "scale_to_canonical": scale, "value_type": vt}, "evidence": []}


HR_LOAD, HR_DESCR = "1.3.6.1.2.1.25.3.3.1.2", "1.3.6.1.2.1.25.3.2.1.3"
SYS = {"1.3.6.1.2.1.1.1": ("STRING", "RouterOS CHR 7.25 (stable)"), "1.3.6.1.2.1.1.2": ("OID", ".1.3.6.1.4.1.14988.1")}


def test_cpu_per_core_named_from_hr_device_and_overall_mean():
    cands = [c("HOST-RESOURCES-MIB", "hrProcessorLoad", HR_LOAD, index=["hrDeviceIndex"],
               mib_range=[{"min": 0, "max": 100}])]
    dev = Dev({HR_LOAD: [("196608", "INTEGER", "10"), ("196609", "INTEGER", "30")],
               HR_DESCR: [("196608", "STRING", "CPU core 0"), ("196609", "STRING", "CPU core 1")]}, SYS)
    p = profile.build(dev, {"cpu": fin(HR_LOAD, "hrProcessorLoad", "percent")}, cands)
    m = p["metrics"]["cpu"]
    assert m["kind"] == "per-core" and m["names_from"] == "hrDeviceDescr"
    assert [r["name"] for r in m["rows"]] == ["CPU core 0", "CPU core 1"]
    assert m["overall"] == {"value": 20.0, "how": "mean of 2 cores"} and m["partial"] is False


GAUGE = "1.3.6.1.4.1.14988.1.1.3.100.1"
UNIT_ENUM = {"celsius": 1, "rpm": 2, "dV": 3, "dA": 4, "dW": 5, "status": 6}


def mikrotik_gauges():
    cands = [c("MIKROTIK-MIB", "mtxrGaugeName", GAUGE + ".2", typ="DisplayString"),
             c("MIKROTIK-MIB", "mtxrGaugeValue", GAUGE + ".3"),
             c("MIKROTIK-MIB", "mtxrGaugeUnit", GAUGE + ".4", typ="INTEGER", enums=UNIT_ENUM)]
    dev = Dev({GAUGE + ".2": [("1", "STRING", "cpu-temperature"), ("2", "STRING", "psu1-voltage"),
                              ("3", "STRING", "fan1-speed"), ("4", "STRING", "board-temperature")],
               GAUGE + ".3": [("1", "INTEGER", "52"), ("2", "INTEGER", "240"), ("3", "INTEGER", "4200"),
                              ("4", "INTEGER", "41")],
               GAUGE + ".4": [("1", "INTEGER", "1"), ("2", "INTEGER", "3"), ("3", "INTEGER", "2"), ("4", "INTEGER", "1")]},
              SYS)
    return cands, dev


def test_shared_column_kept_rows_by_unit_enum_and_partial_flag():
    cands, dev = mikrotik_gauges()
    p = profile.build(dev, {"temperature": fin(GAUGE + ".3", "mtxrGaugeValue", "celsius")}, cands)
    m = p["metrics"]["temperature"]
    assert [r["name"] for r in m["rows"]] == ["cpu-temperature", "board-temperature"]
    assert m["partial"] is True and len(m["skipped"]) == 2
    assert all("another sensor kind" in s["reason"] for s in m["skipped"])
    assert m["kind_filter_from"] == "MIKROTIK-MIB::mtxrGaugeUnit" and m["kind"] == "sensors"


def test_fan_rows_from_the_same_column():
    cands, dev = mikrotik_gauges()
    m = profile.build(dev, {"fan": fin(GAUGE + ".3", "mtxrGaugeValue", "rpm")}, cands)["metrics"]["fan"]
    assert [(r["name"], r["value"]) for r in m["rows"]] == [("fan1-speed", 4200)]


ES = "1.3.6.1.2.1.99.1.1.1"
STATUS_ENUM = {"ok": 1, "unavailable": 2, "nonoperational": 3}


def test_status_meanings_from_enum_labels_mib_range_and_entity_names():
    cands = [c("ENTITY-SENSOR-MIB", "entPhySensorValue", ES + ".4", index=["entPhysicalIndex"],
               mib_range=[{"min": -1000000000, "max": 1000000000}]),
             c("ENTITY-SENSOR-MIB", "entPhySensorOperStatus", ES + ".5", typ="EntitySensorStatus", enums=STATUS_ENUM)]
    dev = Dev({ES + ".4": [("10", "INTEGER", "45"), ("11", "INTEGER", "2000000000"), ("12", "INTEGER", "60")],
               ES + ".5": [("10", "INTEGER", "1"), ("11", "INTEGER", "1"), ("12", "INTEGER", "3")],
               profile.ENT_NAME: [("10", "STRING", "Inlet"), ("11", "STRING", "Ghost"), ("12", "STRING", "CPU")]}, SYS)
    m = profile.build(dev, {"temperature": fin(ES + ".4", "entPhySensorValue", "celsius")}, cands)["metrics"]["temperature"]
    assert m["names_from"] == "entPhysicalName"
    assert [(r["name"], r["status"]["label"], r["status"]["meaning"]) for r in m["rows"]] == [
        ("Inlet", "ok", "ok"), ("CPU", "nonoperational", "critical")]
    assert "outside the MIB's declared range" in m["skipped"][0]["reason"] and m["partial"] is True


def test_meaning_words():
    assert profile.meaning("notPresent") == "absent"
    assert profile.meaning("nonCritical") == "warning"
    assert profile.meaning("running") == "ok" and profile.meaning("up") == "ok"
    assert profile.meaning("backup") == "unknown"          # "up" only as a whole word
    assert profile.meaning("unavailable") == "unknown"     # ambiguous: no severity invented
    assert profile.meaning("other") == "unknown" and profile.meaning(None) == "unknown"


def test_fan_status_object_uses_its_own_enum():
    oid = "1.3.6.1.4.1.9.9.13.1.4.1.3"
    enums = {"normal": 1, "warning": 2, "critical": 3, "shutdown": 4, "notPresent": 5, "notFunctioning": 6}
    cands = [c("CISCO-ENVMON-MIB", "ciscoEnvMonFanState", oid, typ="CiscoEnvMonState", enums=enums),
             c("CISCO-ENVMON-MIB", "ciscoEnvMonFanStatusDescr", oid[:-1] + "2", typ="DisplayString")]
    dev = Dev({oid: [("1", "INTEGER", "1"), ("2", "INTEGER", "4")],
               oid[:-1] + "2": [("1", "STRING", "Fan 1"), ("2", "STRING", "Fan 2")]}, SYS)
    m = profile.build(dev, {"fan": fin(oid, "ciscoEnvMonFanState", "status", vt="status")}, cands)["metrics"]["fan"]
    assert m["kind"] == "status"
    assert [(r["name"], r["status"]["label"], r["status"]["meaning"]) for r in m["rows"]] == [
        ("Fan 1", "normal", "ok"), ("Fan 2", "shutdown", "critical")]


UCD = "1.3.6.1.4.1.2021.4"


def test_memory_avail_pairs_with_total_not_total_free():
    cands = [c("UCD-SNMP-MIB", "memTotalSwap", UCD + ".3", kind="scalar"),
             c("UCD-SNMP-MIB", "memTotalFree", UCD + ".11", kind="scalar"),
             c("UCD-SNMP-MIB", "memTotalReal", UCD + ".5", kind="scalar"),
             c("UCD-SNMP-MIB", "memAvailReal", UCD + ".6", kind="scalar")]
    dev = Dev({}, {**SYS, UCD + ".6": ("INTEGER", "6000000"), UCD + ".5": ("INTEGER", "8000000"),
                   UCD + ".11": ("INTEGER", "1"), UCD + ".3": ("INTEGER", "2")})
    m = profile.build(dev, {"memory": fin(UCD + ".6", "memAvailReal", "bytes", 1024)}, cands)["metrics"]["memory"]
    assert m["partner"]["object"] == "UCD-SNMP-MIB::memTotalReal"
    assert m["kind"] == "used+total" and m["note"] == "used = total - free"
    r = m["rows"][0]
    assert r["total_bytes"] == 8000000 * 1024 and r["used_bytes"] == 2000000 * 1024 and r["percent"] == 25.0


def test_memory_hr_storage_ram_row_and_allocation_units():
    s = profile.HR_STORAGE
    cands = [c("HOST-RESOURCES-MIB", "hrStorageType", s + ".2", typ="AutonomousType"),
             c("HOST-RESOURCES-MIB", "hrStorageDescr", s + ".3", typ="DisplayString"),
             c("HOST-RESOURCES-MIB", "hrStorageAllocationUnits", s + ".4"),
             c("HOST-RESOURCES-MIB", "hrStorageSize", s + ".5"),
             c("HOST-RESOURCES-MIB", "hrStorageUsed", s + ".6")]
    dev = Dev({s + ".2": [("1", "OID", ".1.3.6.1.2.1.25.2.1.2"), ("31", "OID", ".1.3.6.1.2.1.25.2.1.4")],
               s + ".3": [("1", "STRING", "Physical memory"), ("31", "STRING", "/")],
               s + ".4": [("1", "INTEGER", "1024"), ("31", "INTEGER", "4096")],
               s + ".5": [("1", "INTEGER", "8000"), ("31", "INTEGER", "100")],
               s + ".6": [("1", "INTEGER", "2000"), ("31", "INTEGER", "50")]}, SYS)
    m = profile.build(dev, {"memory": fin(s + ".6", "hrStorageUsed", "bytes", None)}, cands)["metrics"]["memory"]
    assert m["kind"] == "used+total" and m["partner"]["object"] == "HOST-RESOURCES-MIB::hrStorageSize"
    assert [r["name"] for r in m["rows"]] == ["Physical memory"]
    assert m["rows"][0]["percent"] == 25.0 and m["rows"][0]["total_bytes"] == 8000 * 1024
    assert m["skipped"][0]["reason"].startswith("not RAM") and m["partial"] is True


def test_memory_load_proof_moves_used_keeps_total():
    s = profile.HR_STORAGE
    cands = [c("HOST-RESOURCES-MIB", "hrStorageSize", s + ".5"), c("HOST-RESOURCES-MIB", "hrStorageUsed", s + ".6")]
    state = {"loaded": False}
    gib_kb = 1024 * 1024

    class LoadDev(Dev):
        def walk(self, oid):
            if oid == s + ".6":
                return [Var(oid + ".1", "INTEGER", str(2 * gib_kb + (gib_kb if state["loaded"] else 0)))]
            if oid == s + ".5":
                return [Var(oid + ".1", "INTEGER", str(8 * gib_kb))]
            return super().walk(oid)

    def load(kind, seconds, size):
        state["loaded"] = True

        class P:
            def terminate(self):
                state["loaded"] = False

            def wait(self, timeout=None):
                return 0
        return P()

    from onboardiq import verify
    saved = verify._meminfo_kb
    verify._meminfo_kb = lambda key="MemTotal": 16 * gib_kb
    try:
        prof = profile.build(LoadDev({}, SYS), {"memory": fin(s + ".6", "hrStorageUsed", "bytes", 1024)}, cands,
                             lab=True, load=load)
    finally:
        verify._meminfo_kb = saved
    m = prof["metrics"]["memory"]
    proof = [e for e in m["proof"] if e["check"] == "memory-load"]
    assert proof and proof[0]["ok"] is True and m["load_tested"] is True, proof


def test_provenance_from_sysdescr_and_entity_revision():
    dev = Dev({profile.ENT_SOFTWARE: [("1", "STRING", ""), ("2", "STRING", "17.9.4a")]}, SYS)
    p = profile.provenance(dev)
    assert p["sysObjectID"] == "1.3.6.1.4.1.14988.1" and p["firmware"] == "17.9.4a"
    assert p["firmware_from"] == "entPhysicalSoftwareRev"
    p2 = profile.provenance(Dev({}, SYS))
    assert p2["firmware"] == "7.25" and p2["firmware_from"] == "sysDescr (version pattern)"


def test_not_supported_keeps_no_rows_and_says_why():
    p = profile.build(Dev({}, SYS), {"fan": fin("1.3.6.1.4.1.1.2", "x", "rpm", verdict="not-supported")}, [])
    assert p["metrics"]["fan"]["rows"] == [] and "not served" in p["metrics"]["fan"]["note"]


def test_resolve_syntax_follows_textual_conventions():
    tcs = {"EntitySensorStatus": {"class": "textualconvention", "type": {
        "type": "INTEGER", "constraints": {"enumeration": {"ok": 1, "unavailable": 2}}}},
        "Temperature": {"class": "textualconvention", "displayhint": "d-1",
                        "type": {"type": "Integer32", "constraints": {"range": [{"min": -2147483648, "max": 2147483647}]}}}}
    r = mib.resolve_syntax({"type": "EntitySensorStatus"}, tcs)
    assert r["enums"] == {"ok": 1, "unavailable": 2}
    t = mib.resolve_syntax({"type": "Temperature"}, tcs)
    assert t["display_hint"] == "d-1" and t["range"][0]["max"] == 2147483647


def test_mixed_column_without_kind_column_split_by_row_names():
    """FortiGate-style fgHwSensorEntValue: voltages, fans and temperatures in one column, no unit column."""
    col, names = "1.3.6.1.4.1.12356.101.4.3.2.1.3", "1.3.6.1.4.1.12356.101.4.3.2.1.2"
    cands = [c("FORTINET-FORTIGATE-MIB", "fgHwSensorEntName", names, typ="DisplayString"),
             c("FORTINET-FORTIGATE-MIB", "fgHwSensorEntValue", col, typ="DisplayString")]
    rows = [("1", "+VCC3", "3.33"), ("2", "P1V0_PCH", "0.99"), ("3", "CPU DTS0", "43"), ("4", "LM75 U75", "35"),
            ("5", "Fan 1", "6500"), ("6", "PS1 Temp 1", "26"), ("7", "PS1 Status", "0")]
    dev = Dev({names: [(i, "STRING", n) for i, n, _ in rows], col: [(i, "STRING", v) for i, _, v in rows]}, SYS)
    t = profile.build(dev, {"temperature": fin(col, "fgHwSensorEntValue", "celsius")}, cands)["metrics"]["temperature"]
    assert [r["name"] for r in t["rows"]] == ["CPU DTS0", "LM75 U75", "PS1 Temp 1"]   # neutral chip names kept
    f = profile.build(dev, {"fan": fin(col, "fgHwSensorEntValue", "rpm")}, cands)["metrics"]["fan"]
    assert [r["name"] for r in f["rows"]] == ["Fan 1"]                               # fans must say fan
