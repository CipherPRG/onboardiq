"""Record a session against a fake device + fake model, replay it, get the same result."""
import json

from onboardiq import agent, llm, replay
from tests.test_agent import pick
from tests.test_verify import FAST, FakeDevice, ALLOC


def test_record_then_replay_gives_same_verdicts(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    answers = [{"memory": pick("1.9.9.6", "hrMemorySize")},
               {"pick": pick("1.9.9.5", "memAvailReal"), "reasoning": "total did not move"}]

    def fake_chat(messages, **kw):
        out = {"content": json.dumps(answers.pop(0)), "usage": {}, "cost_usd": 0.01, "seconds": 1,
               "model": "m", "cached": False}
        key = f"k{len(llm.USED_KEYS)}"
        llm.USED_KEYS.append(key)
        (llm.CACHE_DIR / "llm").mkdir(parents=True, exist_ok=True)
        (llm.CACHE_DIR / "llm" / f"{key}.json").write_text(json.dumps(out))
        return out

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(llm, "USED_KEYS", [])
    dev = FakeDevice({"1.9.9.6": ("INTEGER", lambda l, t: 8_000_000),
                      "1.9.9.5": ("INTEGER", lambda l, t: 6_000_000 - (ALLOC // 1024 if l else 0))})
    rec = replay.Recorder(dev)
    live = agent.onboard("box", [], rec, lab=True, families=["memory"],
                         verify_kw={"load": dev.load, **FAST}, log=lambda *a: None)
    path = tmp_path / "session.json"
    rec.save(path, args={"device": "box", "lab": True, "families": "memory", "max_attempts": 3,
                         "effort": "high", "model": None}, cands=[])
    monkeypatch.undo()   # real llm.chat back: replay must not need it
    out = replay.replay(path, log=lambda *a: None)
    assert live["spec"]["memory"]["verdict"] == out["spec"]["memory"]["verdict"] == "proven-experiment"
    assert out["spec"]["memory"]["attempts"] == 2 and out["replay"]["unused_answers"] == 0


def test_health_recording_exports_a_timeline(tmp_path):
    import json
    from onboardiq import playback
    rows = [{"name": "c #1", "score": 0.0, "level": "critical", "why": "100%"}]
    lines = [{"score": 100, "level": "ok", "families": {}, "at": "2026-10-28T17:37:00", "anomalies": []},
             {"score": 40, "level": "warning", "capped_by": "critical", "at": "2026-10-28T17:37:10",
              "families": {"cpu_load": {"score": 0.0, "worst": "c #1", "rows": rows}}, "anomalies": [],
              "explanation": {"text": "CPU is at 100 percent.", "source": "m", "checked": True}},
             {"score": 100, "level": "ok", "families": {}, "at": "2026-10-28T17:37:20", "anomalies": []}]
    f = tmp_path / "x.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines))
    pb = playback.export_health(f)
    assert pb["kind"] == "health" and len(pb["samples"]) == 3
    assert pb["samples"][1]["level"] == "critical"  # a critical row reads as critical
    assert pb["samples"][1]["explanation"]["text"] == "CPU is at 100 percent."
    assert "recovered" in pb["highlight"]


def test_replay_keeps_the_declared_memory_size():
    from onboardiq.replay import replay_load
    assert replay_load({"memory_mib": 16.0}).bytes == {"memory": 16 * 1024 ** 2}
    assert not getattr(replay_load({}), "bytes", None)
