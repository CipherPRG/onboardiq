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
                                                  "verify", "final", "profile", "done"]
    assert m["triggers"] == ["experiment"]                       # what made it re-read, for the eval split


def test_absent_everywhere_is_not_supported(monkeypatch):
    monkeypatch.setattr(reader, "repick", lambda *a, **k: (pick("1.9.9.11", "otherTemp", 0.1, "celsius"), "",
                                                           {"cost_usd": 0, "cached": True}))
    out = agent.onboard("test box", [], DEV, families=["temperature"],
                        initial={"temperature": pick("1.9.9.10", "lmTemp", 0.001, "celsius")},
                        max_attempts=2, verify_kw={"observe_s": 0.01, "witness_interval": 0.001, "observe_interval": 0.001}, log=lambda *a: None)
    assert out["spec"]["temperature"]["verdict"] == "not-supported"
    # absence says nothing about which object the MIB means: the Reader's first answer is reported
    assert out["spec"]["temperature"]["pick"]["object_name"] == "lmTemp"
    assert out["spec"]["temperature"]["triggers"] == ["absent"]



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


def test_pick_outside_the_given_mib_is_rejected_without_polling():
    from onboardiq import agent

    class NoTouch:
        calls = 0

        def get(self, *a):
            raise AssertionError("an invented OID must not be polled")

        walk = get

    types = {"1.3.6.1.2.1.25.3.3.1.2": "Integer32"}
    a = agent._verify(NoTouch(), "cpu", {"oid": "1.3.6.1.4.1.99999.1.2", "object_name": "madeUp"}, False, {}, types)
    assert a["verdict"] == "rejected" and a["evidence"][0]["check"] == "in-mib"
    assert agent.trigger(a["evidence"]) == "in-mib"
    assert agent.in_mib("1.3.6.1.2.1.25.3.3.1.2.196608", set(types))      # an instance of a MIB object
    assert agent.in_mib(".1.3.6.1.2.1.25.3.3.1.2", set(types))
    assert not agent.in_mib("1.3.6.1.2.1.25.3.3", set(types))              # a parent is not the object


def test_reader_survives_schema_ignoring_replies(monkeypatch):
    from onboardiq import llm, reader
    # a bare string for a family is no pick, not a crash
    assert reader._parse('{"cpu": "hrProcessorLoad", "memory": null}')["cpu"] is None
    replies = iter(['{"pick": "hrProcessorLoad"}', '{"pick": {"oid": "1.3.6.1.2.1.25.3.3.1.2", "object_name": "hrProcessorLoad", "scale_to_canonical": 1}'])
    monkeypatch.setattr(llm, "chat", lambda *a, **k: {"content": next(replies)})
    pick, why, r = reader.repick("dev", [], "cpu", [{"pick": {"object_name": "x"}, "verdict": "rejected", "evidence": []}])
    assert pick["oid"] is None and r["parse_failed"]                 # both replies unusable: no pick, no crash
    replies = iter(['{"pick": "x"}', '{"pick": {"oid": "1.3.6.1.2.1.25.3.3.1.2", "object_name": "hrProcessorLoad", "scale_to_canonical": 1}, "reasoning": "r"}'])
    pick, why, r = reader.repick("dev", [], "cpu", [{"pick": {"object_name": "x"}, "verdict": "rejected", "evidence": []}])
    assert pick["object_name"] == "hrProcessorLoad"                  # the retry recovers


def test_bare_string_witness_is_no_witness():
    from onboardiq import reader
    d = reader._parse('{"cpu": {"oid": "1.3.6.1.2.1.25.3.3.1.2", "object_name": "hrProcessorLoad", '
                      '"scale_to_canonical": 1, "witness": "hrProcessorFrwID"}}')
    assert d["cpu"]["witness"] is None and d["cpu"]["oid"] == "1.3.6.1.2.1.25.3.3.1.2"
    d = reader._parse('{"pick": {"oid": "1.3", "object_name": "x", "scale_to_canonical": 1, "witness": ["a"]}}')
    assert d["pick"]["witness"] is None
