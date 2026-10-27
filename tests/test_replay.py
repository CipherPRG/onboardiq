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
