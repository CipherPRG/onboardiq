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
    p = prof(cpu=m([{"name": "c", "value": 5.0}]), temperature=m([{"name": "t", "value": 90.0}], verdict="unproven"),
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
