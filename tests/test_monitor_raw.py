import json
from pathlib import Path

from onboardiq import health, monitor, replay
from onboardiq.monitor import Monitor, restarted
from onboardiq.recorded import RecordedSnmp
from onboardiq.snmp import Var

ROOT = Path(__file__).resolve().parent.parent
SNMPREC = """1.3.6.1.2.1.1.3.0|67|90000
1.3.6.1.2.1.25.3.2.1.3.1|4|CPU 0
1.3.6.1.2.1.25.3.3.1.2.1|2|10
"""
SPEC = {"cpu": {"verdict": "proven-experiment", "evidence": [],
                "pick": {"oid": "1.3.6.1.2.1.25.3.3.1.2", "object_name": "hrProcessorLoad",
                         "scale_to_canonical": 1, "canonical_unit": "percent", "value_type": "gauge"}}}


def _cands():
    d = json.loads((ROOT / "demo" / "laptop_full.json").read_text())
    return d["cands"]


def _device(tmp_path):
    p = tmp_path / "dev.snmprec"
    p.write_text(SNMPREC)
    return RecordedSnmp(p)


def _set(dev, oid, typ, value):
    dev.rows[tuple(int(x) for x in oid.split("."))] = Var(oid, typ, str(value))


def _run(tmp_path):
    """12 live polls: CPU 10% steady, then the device restarts and CPU jumps to 95%."""
    dev = _device(tmp_path)
    clock = [1_000_000.0]
    rec = replay.PollRecorder(dev, clock=lambda: clock[0])
    mon = Monitor(rec, {"spec": SPEC}, ["HOST-RESOURCES-MIB"], None, policy=health.load_policy(None),
                  cands=_cands(), clock=lambda: clock[0])
    out = []
    for i in range(12):
        clock[0] += 10
        up = 90000 + 1000 * (i + 1) if i < 11 else 500        # poll 12: uptime fell
        _set(dev, "1.3.6.1.2.1.1.3.0", "Timeticks", up)
        _set(dev, "1.3.6.1.2.1.25.3.3.1.2.1", "INTEGER", 95 if i == 11 else 10)
        rec.begin()
        out.append(mon.poll())
    path = tmp_path / "health.raw.json"
    rec.save(path, device="Test box", spec=SPEC, modules=["HOST-RESOURCES-MIB"], cands=_cands(),
             policy=mon.pol, explain=False, interval=10)
    return out, path


def test_restart_detection_rules():
    assert restarted(100_000, 500, 10)                       # fell: restarted
    assert not restarted(100_000, 101_000, 10)               # normal
    assert not restarted(None, 500, 10)
    wrap = 2 ** 32
    assert not restarted(wrap - 500, 500, 10)                # passed 497 days: wrapped, not restarted
    assert restarted(wrap - 500, 90_000, 10)                 # same drop but far from the wrap point


def test_live_run_sees_the_restart(tmp_path):
    live, _ = _run(tmp_path)
    assert all(not r.get("events") for r in live[:-1])
    assert "restarted" in live[-1]["events"][0]
    assert live[0]["score"] == 100 and live[-1]["score"] < live[0]["score"]


def test_replay_recomputes_the_same_readings(tmp_path):
    live, path = _run(tmp_path)
    again = replay.replay_health(path)
    assert [r["score"] for r in again] == [r["score"] for r in live]
    assert [r.get("events") for r in again] == [r.get("events") for r in live]
    assert [r["at"] for r in again] == [r["at"] for r in live]


def test_changing_a_raw_number_changes_the_replayed_score(tmp_path):
    """The replay is computed from the recorded answers, not stored scores: edit one and the score moves."""
    live, path = _run(tmp_path)
    d = json.loads(path.read_text())
    for c in d["polls"][-1]["calls"]:
        for v in c["vars"]:
            if v[0] == "1.3.6.1.2.1.25.3.3.1.2.1":
                v[2] = "5"                                    # the 95% reading becomes 5%
    path.write_text(json.dumps(d))
    edited = replay.replay_health(path)
    assert edited[-1]["score"] == 100 and live[-1]["score"] < 100


def test_export_marks_the_session_as_recomputed(tmp_path):
    from onboardiq import playback
    _, path = _run(tmp_path)
    s = playback.export_health_raw(path)
    assert s["kind"] == "health" and s["recomputed"]["polls"] == 12
    assert s["recomputed"]["snmp_responses"] > 12 and len(s["samples"]) == 12
    assert s["samples"][-1]["events"]


def test_scrub_removes_the_device_name_everywhere():
    d = {"polls": [{"t": 0, "calls": [{"op": "get", "args": [], "vars": [
        ["1.3.6.1.2.1.1.5.0", "STRING", "MYLAPTOP"],
        ["1.3.6.1.2.1.1.1.0", "STRING", "Linux MYLAPTOP 6.1 #1 SMP"],
        ["1.3.6.1.2.1.1.4.0", "STRING", "me@example.com"],
        ["1.3.6.1.2.1.1.6.0", "STRING", "my desk"]]}]}]}
    out = json.dumps(replay.scrub_raw(d))
    assert "MYLAPTOP" not in out and "example.com" not in out and "my desk" not in out
    assert "Linux device 6.1" in out
