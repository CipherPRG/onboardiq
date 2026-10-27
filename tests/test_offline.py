"""Offline tests: spend guard, cost maths, cache and the API path with a fake client."""
import json
import types

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OIQ_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OIQ_DAILY_USD", "0.05")
    monkeypatch.setenv("OIQ_MAX_CALLS_PER_RUN", "2")
    monkeypatch.setenv("OIQ_PAID", "on")
    import importlib
    from onboardiq import config, spend_guard, llm
    importlib.reload(config)
    importlib.reload(spend_guard)
    importlib.reload(llm)
    return tmp_path, spend_guard, llm


def test_cost_math(env):
    _, sg, _ = env
    assert sg.cost_usd("muse-spark-1.3", 1_000_000, 0) == pytest.approx(1.25)
    assert sg.cost_usd("muse-spark-1.3", 0, 1_000_000) == pytest.approx(4.25)
    assert sg.cost_usd("muse-spark-1.3", 1_000_000, 0, 1_000_000) == pytest.approx(0.15)
    assert sg.cost_usd("unknown-model", 1_000_000, 0) == pytest.approx(5.0)


def test_daily_cap_blocks(env):
    tmp, sg, _ = env
    g = sg.SpendGuard(tmp / "s.json")
    g.record("muse-spark-1.3", 30_000, 0)  # $0.0375
    with pytest.raises(sg.SpendLimitError):
        g.check("muse-spark-1.3", 20_000, 0)  # +$0.025 > $0.05


def test_call_cap_and_off_switch(env, monkeypatch):
    tmp, sg, _ = env
    g = sg.SpendGuard(tmp / "s.json")
    g.record("muse-spark-1.3", 1, 1)
    g.record("muse-spark-1.3", 1, 1)
    with pytest.raises(sg.SpendLimitError):
        g.check("muse-spark-1.3", 1, 1)
    monkeypatch.setenv("OIQ_PAID", "off")
    with pytest.raises(sg.SpendLimitError):
        sg.SpendGuard(tmp / "t.json").check("muse-spark-1.3", 1, 1)


def test_bad_limit_refuses(env, monkeypatch):
    _, sg, _ = env
    monkeypatch.setenv("OIQ_DAILY_USD", "999")
    with pytest.raises(sg.SpendLimitError):
        sg.SpendGuard()


def test_chat_records_cost_and_caches(env):
    tmp, _, llm = env
    calls = []

    def create(**kw):
        calls.append(kw)
        usage = types.SimpleNamespace(prompt_tokens=1000, completion_tokens=100,
                                      prompt_tokens_details=types.SimpleNamespace(cached_tokens=0))
        msg = types.SimpleNamespace(content='{"ok": true}')
        return types.SimpleNamespace(usage=usage, choices=[types.SimpleNamespace(message=msg)])

    llm._clients["meta"] = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    msgs = [{"role": "user", "content": "hi"}]
    r1 = llm.chat(msgs, max_tokens=100, json_schema={"type": "object"})
    r2 = llm.chat(msgs, max_tokens=100, json_schema={"type": "object"})
    assert json.loads(r1["content"]) == {"ok": True}
    assert r1["cached"] is False and r2["cached"] is True
    assert len(calls) == 1
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert r1["cost_usd"] == pytest.approx((1000 * 1.25 + 100 * 4.25) / 1e6)
    assert llm.guard().spent_today()["calls"] == 1


def test_provider_routing(env, monkeypatch):
    from onboardiq import config
    assert config.provider_for("muse-spark-1.3") == "meta"
    assert config.provider_for("meta-models/muse-spark-open-1026") == "baseten"
    _, sg, llm = env
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="BASETEN_API_KEY"):
        llm.client("baseten")
    # open model has no confirmed price yet -> charged conservatively
    assert sg.cost_usd("meta-models/muse-spark-open-1026", 1_000_000, 0) == pytest.approx(5.0)
