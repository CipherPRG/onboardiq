"""Health score: rules, caps, exclusions, anomaly baseline."""
from onboardiq import health, profile
from onboardiq.snmp import Var


def prof(**metrics):
    return {"metrics": metrics}


def m(rows, kind="per-core", verdict="proven-static", unit="percent", **kw):
    return {"verdict": verdict, "kind": kind, "unit": unit, "rows": rows, **kw}


def test_healthy_device_scores_high_and_says_so():
    p = prof(cpu=m([{"name": "c0", "value": 10.0}]), memory=m([{"name": "RAM", "percent": 50.0}], kind="used+total"))
    r = health.score(p)
    assert r["score"] == 100 and r["level"] == "ok" and r["summary"] == "all proven readings are healthy"


def test_cpu_rule_points():
    f = health._line
    assert f(70, 70, (90, 50), 100) == 100 and f(90, 70, (90, 50), 100) == 50 and f(100, 70, (90, 50), 100) == 0
    assert health.row_score("cpu", "overall", "percent", {"value": 80.0})[0] == 75.0


def test_one_dead_fan_cannot_be_averaged_away():
    p = prof(cpu=m([{"name": "c", "value": 5.0}]),
             fan=m([{"name": "fan1", "value": 4000.0}, {"name": "fan2", "value": 0.0}], kind="sensors", unit="rpm"))
    r = health.score(p)
    assert r["families"]["fan"]["score"] == 0 and r["families"]["fan"]["worst"] == "fan2"
    assert r["score"] <= health.CAP_CRITICAL and r["capped_by"] == "critical"
    assert "fan fan2" in r["summary"]


def test_unproven_and_not_served_are_excluded_not_guessed():
    p = prof(cpu=m([{"name": "c", "value": 5.0}], object="X::cpu"),
             temperature=m([{"name": "t", "value": 90.0}], verdict="unproven", object="X::temp"),
             fan=m([], verdict="not-supported"))
    r = health.score(p)
    assert r["score"] == 100 and set(r["families"]) == {"cpu"}
    assert {e["reason"] for e in r["excluded"]} == {"not proven on this device", "not served by this device"}
    assert r["coverage"] == "1 of 2 served families scored"      # fan is not served, temperature is served but unproven


def test_status_rows_use_the_mib_meaning_and_unknown_is_not_scored():
    rows = [{"name": "psu1", "status": {"raw": 1, "label": "normal", "meaning": "ok"}},
            {"name": "psu2", "status": {"raw": 3, "label": "critical", "meaning": "critical"}},
            {"name": "psu3", "status": {"raw": 9, "label": "weird", "meaning": "unknown"}}]
    r = health.score(prof(fan=m(rows, kind="status", unit="status")))
    assert r["families"]["fan"]["score"] == 0
    assert any(e["row"] == "psu3" for e in r["excluded"])


def test_value_and_status_take_the_lower_score():
    row = {"name": "t", "value": 40.0, "status": {"raw": 2, "label": "warning", "meaning": "warning"}}
    assert health.row_score("temperature", "sensors", "celsius", row)[0] == 60


def test_nothing_proven_gives_no_score():
    r = health.score(prof(cpu=m([{"name": "c", "value": 5.0}], verdict="unproven")))
    assert r["score"] is None


def test_baseline_flags_a_jump_after_enough_history_only():
    b = health.Baseline(min_points=8)
    notes = [b.observe("cpu/c0", v) for v in [10, 11, 9, 10, 12, 10, 11, 10]]
    assert not any(notes)
    assert b.observe("cpu/c0", 11) is None
    assert "far from its usual" in b.observe("cpu/c0", 60)


def test_disk_rows_from_hr_storage_checked_and_scored():
    S = "1.3.6.1.2.1.25.2.3.1"

    class Dev:
        def walk(self, oid):
            cols = {S + ".2": [("1", "OID", ".1.3.6.1.2.1.25.2.1.4"), ("2", "OID", ".1.3.6.1.2.1.25.2.1.4"),
                               ("3", "OID", ".1.3.6.1.2.1.25.2.1.2")],
                    S + ".3": [("1", "STRING", "/"), ("2", "STRING", "/bad"), ("3", "STRING", "RAM")],
                    S + ".4": [("1", "INTEGER", "4096"), ("2", "INTEGER", "4096"), ("3", "INTEGER", "1024")],
                    S + ".5": [("1", "INTEGER", "1000"), ("2", "INTEGER", "100"), ("3", "INTEGER", "10")],
                    S + ".6": [("1", "INTEGER", "950"), ("2", "INTEGER", "500"), ("3", "INTEGER", "5")]}
            return [Var(f"{oid}.{i}", t, v) for i, t, v in cols.get(oid, [])]

    d = profile.disk(Dev())
    assert [r["name"] for r in d["rows"]] == ["/"] and d["rows"][0]["percent"] == 95.0
    assert d["partial"] is True and "larger than size" in d["skipped"][0]["reason"]
    r = health.score(prof(disk=d))
    assert r["families"]["disk"]["score"] == 40.0


def test_scalar_groups_get_no_row_name_from_an_error_label():
    UCD = "1.3.6.1.4.1.2021.11"
    cands = [profile_c("UCD-SNMP-MIB", "ssCpuRawUser", UCD + ".50", kind="scalar", typ="Counter32"),
             profile_c("UCD-SNMP-MIB", "ssErrorName", UCD + ".2", kind="scalar", typ="DisplayString")]

    class Dev:
        def walk(self, oid):
            return {UCD + ".50": [Var(UCD + ".50.0", "Counter32", "1000")],
                    UCD + ".2": [Var(UCD + ".2.0", "STRING", "systemStats")]}.get(oid, [])

        def get(self, *oids):
            return []

    spec = {"cpu": {"verdict": "proven-experiment", "evidence": [],
                    "pick": {"oid": UCD + ".50", "object_name": "ssCpuRawUser", "canonical_unit": "percent",
                             "scale_to_canonical": None, "value_type": "counter"}}}
    p = profile.build(Dev(), spec, cands)
    assert p["metrics"]["cpu"]["rows"][0]["name"] is None


def profile_c(module, name, oid, kind="column", typ="Integer32"):
    return {"module": module, "name": name, "oid": oid, "kind": kind, "type": typ, "units": None, "range": None,
            "status": "current", "description": "", "mib_range": None, "enums": None, "display_hint": None,
            "entry": None, "index": None}


def test_counter_cpu_is_scored_through_hr_processor_load():
    UCD, HRL, HRD = "1.3.6.1.4.1.2021.11.50", "1.3.6.1.2.1.25.3.3.1.2", "1.3.6.1.2.1.25.3.2.1.3"

    class Dev:
        def walk(self, oid):
            return {UCD: [Var(UCD + ".0", "Counter32", "1000")],
                    HRL: [Var(HRL + ".196608", "INTEGER", "95"), Var(HRL + ".196609", "INTEGER", "90")],
                    HRD: [Var(HRD + ".196608", "STRING", "cpu0"), Var(HRD + ".196609", "STRING", "cpu1")]}.get(oid, [])

        def get(self, *oids):
            return []

    spec = {"cpu": {"verdict": "proven-experiment", "evidence": [],
                    "pick": {"oid": UCD, "object_name": "ssCpuRawUser", "canonical_unit": "percent",
                             "scale_to_canonical": None, "value_type": "counter"}}}
    p = profile.build(Dev(), spec, [profile_c("UCD-SNMP-MIB", "ssCpuRawUser", UCD, kind="scalar", typ="Counter32")])
    assert p["metrics"]["cpu_load"]["kind"] == "per-core"
    r = health.score(p)
    assert set(r["families"]) == {"cpu_load"} and r["families"]["cpu_load"]["worst"] == "cpu0"
    assert r["score"] < 75 and any("hrProcessorLoad" in e["reason"] for e in r["excluded"])
    assert r["coverage"] == "1 of 1 served families scored"


def test_read_only_filesystems_do_not_count_as_disks_and_unserved_families_do_not_count():
    S, FS = "1.3.6.1.2.1.25.2.3.1", "1.3.6.1.2.1.25.3.8.1"

    class Dev:
        def walk(self, oid):
            cols = {S + ".2": [("1", "OID", ".1.3.6.1.2.1.25.2.1.4"), ("2", "OID", ".1.3.6.1.2.1.25.2.1.4")],
                    S + ".3": [("1", "STRING", "/"), ("2", "STRING", "/usr/src/headers")],
                    S + ".4": [("1", "INTEGER", "4096"), ("2", "INTEGER", "4096")],
                    S + ".5": [("1", "INTEGER", "1000"), ("2", "INTEGER", "100")],
                    S + ".6": [("1", "INTEGER", "100"), ("2", "INTEGER", "100")],
                    FS + ".5": [("1", "INTEGER", "1"), ("2", "INTEGER", "2")],
                    FS + ".7": [("1", "INTEGER", "1"), ("2", "INTEGER", "2")]}
            return [Var(f"{oid}.{i}", t, v) for i, t, v in cols.get(oid, [])]

    d = profile.disk(Dev())
    assert [r["name"] for r in d["rows"]] == ["/"] and "read-only" in d["skipped"][0]["reason"]
    unserved = {"verdict": "unproven", "kind": None, "rows": [], "note": "no object chosen"}
    r = health.score(prof(disk=d, temperature=unserved))
    assert r["score"] == 100 and r["coverage"] == "1 of 1 served families scored"
    assert {"family": "temperature", "reason": "not served by this device"} in r["excluded"]


def test_cores_that_share_a_name_get_their_own_labels_and_baselines():
    rows = [{"name": "Intel Core", "value": 10.0}, {"name": "Intel Core", "value": 95.0}]
    r = health.score(prof(cpu_load=m(rows, kind="per-core")))
    assert [x["name"] for x in r["families"]["cpu_load"]["rows"]] == ["Intel Core #1", "Intel Core #2"]
    assert r["families"]["cpu_load"]["worst"] == "Intel Core #2"
    r = health.score(prof(memory=m([{"percent": 40.0}], kind="used+total")))
    assert r["families"]["memory"]["worst"] == "overall"


def test_many_rows_tripping_together_become_one_anomaly_line():
    b = health.Baseline(min_points=8)
    calm = prof(cpu_load=m([{"name": "cpu", "value": 1.0}] * 6, kind="per-core"))
    for _ in range(9):
        assert b.scan(calm) == []
    busy = prof(cpu_load=m([{"name": "cpu", "value": 90.0}] * 6, kind="per-core"))
    out = b.scan(busy)
    assert len(out) == 1 and "6 of 6 rows far from their usual" in out[0]
