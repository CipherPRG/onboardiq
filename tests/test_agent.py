"""Agent loop with a fake Reader and a fake device: mistake -> evidence -> re-pick -> proven."""
from onboardiq import agent, reader
from tests.test_verify import DEV, FAST


def pick(oid, name, scale=1024, unit="bytes", vt="gauge"):
    return {"oid": oid, "object_name": name, "value_type": vt, "scale_to_canonical": scale,
            "canonical_unit": unit, "row_filter": None, "evidence": "", "raw_unit": None, "confidence": 0.9}


def test_mistake_is_caught_and_corrected(monkeypatch):
    seen = {}

    def fake_repick(device, cands, family, attempts, model=None, effort="high"):
        seen["evidence"] = attempts[-1]["evidence"][-1]["detail"]
        return pick("1.9.9.5", "memAvailReal"), "total RAM did not move under load; use available memory", \
            {"cost_usd": 0.01, "cached": False}

    monkeypatch.setattr(reader, "repick", fake_repick)
    out = agent.onboard("test box", [], DEV, lab=True, families=["memory"],
                        initial={"memory": pick("1.9.9.6", "hrMemorySize")},
                        verify_kw={"load": DEV.load, **FAST}, log=lambda *a: None)
    m = out["spec"]["memory"]
    assert m["verdict"] == "proven-experiment" and m["attempts"] == 2
    assert m["pick"]["object_name"] == "memAvailReal"
    assert "change +0" in seen["evidence"]                      # the Reader was told why
    assert [t["kind"] for t in out["trace"]] == ["read", "verify-start", "verify", "repick", "verify-start",
                                                  "verify", "final", "done"]


def test_absent_everywhere_is_not_supported(monkeypatch):
    monkeypatch.setattr(reader, "repick", lambda *a, **k: (pick("1.9.9.11", "otherTemp", 0.1, "celsius"), "",
                                                           {"cost_usd": 0, "cached": True}))
    out = agent.onboard("test box", [], DEV, families=["temperature"],
                        initial={"temperature": pick("1.9.9.10", "lmTemp", 0.001, "celsius")},
                        max_attempts=2, verify_kw={"observe_s": 0.01}, log=lambda *a: None)
    assert out["spec"]["temperature"]["verdict"] == "not-supported"



def test_events_stream_and_hook_is_restored(monkeypatch):
    from onboardiq import verify
    events = []
    monkeypatch.setattr(reader, "repick", lambda *a, **k: (pick("1.9.9.5", "memAvailReal"), "use available",
                                                           {"cost_usd": 0, "cached": True}))
    agent.onboard("test box", [], DEV, lab=True, families=["memory"],
                  initial={"memory": pick("1.9.9.6", "hrMemorySize")},
                  verify_kw={"load": DEV.load, **FAST}, log=lambda *a: None, on_event=events.append)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "read" and kinds[-1] == "done" and "sample" in kinds
    phases = {e["phase"] for e in events if e["kind"] == "sample"}
    assert {"base", "load"} <= phases
    assert all(e["family"] == "memory" for e in events if e["kind"] == "sample")
    assert verify.EVENTS["hook"] is None
