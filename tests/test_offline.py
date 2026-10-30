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
    assert sg.cost_usd("meta-models/muse-spark-open-1026", 1_000_000, 0) == pytest.approx(1.25)


def test_providers_have_separate_ledgers_and_limits(env, monkeypatch):
    tmp, sg, _ = env
    monkeypatch.setenv("OIQ_BASETEN_DAILY_USD", "7")
    meta, base = sg.SpendGuard(provider="meta"), sg.SpendGuard(provider="baseten")
    assert meta.daily_usd == 0.05 and base.daily_usd == 7
    assert meta.state_file != base.state_file
    base.record("meta-models/muse-spark-open-1026", 1000, 1000)
    assert meta.spent_today()["usd"] == 0 and base.spent_today()["usd"] > 0
    monkeypatch.setenv("OIQ_BASETEN_DAILY_USD", "99")
    with pytest.raises(sg.SpendLimitError):
        sg.SpendGuard(provider="baseten")


def test_reader_maps_renamed_fields_without_inventing():
    from onboardiq import reader
    out = reader._parse('{"cpu": {"oid": "1.3.6.1.4.1.664.5.53.1.4.3", "module_name": "ADTRAN-AOSCPU::adGenAOS1MinCpuUtil", '
                        '"scale": 1}, "fan": {"oid": null}}')
    assert out["cpu"]["object_name"] == "adGenAOS1MinCpuUtil" and out["cpu"]["scale_to_canonical"] == 1
    assert out["fan"].get("object_name") is None


def _fake_client(create):
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))


def _reply(text='{"ok": true}'):
    usage = types.SimpleNamespace(prompt_tokens=100, completion_tokens=10,
                                  prompt_tokens_details=types.SimpleNamespace(cached_tokens=0))
    return types.SimpleNamespace(usage=usage, choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))])


def test_fireworks_routing_and_own_ledger(env, monkeypatch):
    from onboardiq import config
    _, sg, llm = env
    assert config.provider_for("accounts/fireworks/models/muse-spark-open") == "fireworks"
    monkeypatch.setenv("OIQ_FIREWORKS_DAILY_USD", "7")
    fw = sg.SpendGuard(provider="fireworks")
    assert fw.daily_usd == 7 and fw.state_file.name == "spend_fireworks.json"
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FIREWORKS_API_KEY"):
        llm.client("fireworks")


def test_failover_goes_to_next_provider_and_says_so(env):
    _, sg, llm = env
    llm._clients["baseten"] = _fake_client(lambda **kw: (_ for _ in ()).throw(RuntimeError("no credit")))
    llm._clients["fireworks"] = _fake_client(lambda **kw: _reply())
    r = llm.chat([{"role": "user", "content": "hi"}], max_tokens=50, use_cache=False,
                 model="meta-models/muse-spark-open-1026,accounts/fireworks/models/muse-spark-open")
    assert r["model"] == "accounts/fireworks/models/muse-spark-open"
    assert "meta-models/muse-spark-open-1026" in r["fallback_from"][0]
    # a single model never falls back: its error is raised as it is
    with pytest.raises(RuntimeError, match="no credit"):
        llm.chat([{"role": "user", "content": "hi"}], max_tokens=50, use_cache=False, model="meta-models/muse-spark-open-1026")


def test_failover_when_a_provider_is_over_its_cap_and_all_failing_raises(env):
    _, sg, llm = env
    # the meta ledger is capped at $0.05 in this fixture: spend it, then the call must move on
    sg.SpendGuard(provider="meta").record("muse-spark-1.3", 40_000, 0)
    llm._clients["fireworks"] = _fake_client(lambda **kw: _reply())
    r = llm.chat([{"role": "user", "content": "hi" * 2000}], max_tokens=50, use_cache=False,
                 model="muse-spark-1.3,accounts/fireworks/models/muse-spark-open")
    assert r["model"].startswith("accounts/fireworks/") and "daily cap" in r["fallback_from"][0]
    # both hosts must be fake: with a real BASETEN_API_KEY in .env this test used to call Baseten for real
    llm._clients["baseten"] = _fake_client(lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    llm._clients["fireworks"] = _fake_client(lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(RuntimeError, match="every provider failed"):
        llm.chat([{"role": "user", "content": "x"}], max_tokens=50, use_cache=False,
                 model="meta-models/muse-spark-open-1026,accounts/fireworks/models/muse-spark-open")


def test_fireworks_retries_with_its_own_response_format(env):
    _, _, llm = env
    seen = []

    def create(**kw):
        seen.append(kw["response_format"]["type"])
        if kw["response_format"]["type"] == "json_schema":
            raise ValueError("invalid response_format: use json_object with a schema")
        return _reply()

    llm._clients["fireworks"] = _fake_client(create)
    r = llm.chat([{"role": "user", "content": "hi"}], max_tokens=50, use_cache=False,
                 json_schema={"type": "object"}, model="accounts/fireworks/models/muse-spark-open")
    assert seen == ["json_schema", "json_object"] and json.loads(r["content"]) == {"ok": True}


def test_meta_model_names_and_warning(capsys):
    from onboardiq import config
    for ok in ("muse-spark-1.3", "meta-models/muse-spark-open-1026", "accounts/fireworks/models/muse-spark-open"):
        assert config.is_meta(ok)
    assert not config.is_meta("gpt-4o")
    assert config.require_meta("some-other-model") == "some-other-model"      # warns, does not refuse
    assert "not a Muse Spark model" in capsys.readouterr().err


def test_missing_stress_ng_is_unproven_not_a_crash(monkeypatch):
    import pytest
    from onboardiq import verify

    def boom(*a, **k):
        raise FileNotFoundError("stress-ng")
    monkeypatch.setattr(verify.subprocess, "Popen", boom)
    with pytest.raises(verify.NotInducible) as e:
        verify.default_load("cpu", 5)
    assert "stress-ng" in str(e.value)


# --- SNMP credentials: versions 1 / 2c / 3 ---------------------------------------------------------------------------
def _conf(**kw):
    import glob
    from onboardiq.snmp import Snmp
    s = Snmp("127.0.0.1", runner=lambda cmd: "", **kw)
    return open(glob.glob(s._conf_dir + "/snmp.conf")[0]).read()


def test_snmp_conf_for_each_version():
    assert "defVersion 1" in _conf(community="c1", version="1") and "defCommunity c1" in _conf(community="c1", version="1")
    assert "defVersion 2c" in _conf(community="x")
    v3 = {"user": "ops", "auth_proto": "SHA-256", "auth_pass": "authpassphrase", "priv_proto": "AES-256", "priv_pass": "privpassphrase"}
    c = _conf(v3=v3)
    assert "defVersion 3" in c and "defSecurityLevel authPriv" in c and "defAuthType SHA-256" in c and "defPrivType AES-256" in c
    assert "authNoPriv" in _conf(v3={"user": "ops", "auth_pass": "authpassphrase"})


def test_snmp_credentials_are_checked_before_they_reach_a_config_file():
    from onboardiq.snmp import credentials
    ok = {"version": "3", "v3_user": "ops", "v3_auth_pass": "authpassphrase"}
    assert credentials(ok)["v3"]["user"] == "ops"
    for bad in ({**ok, "v3_auth_proto": "ROT13"}, {**ok, "v3_auth_pass": "short"}, {**ok, "v3_user": ""},
                {**ok, "v3_priv_pass": "privpassphrase", "v3_priv_proto": "XOR"}, {**ok, "v3_user": "a\nb"},
                {**ok, "v3_auth_pass": "x" * 200}, {"version": "9"}, {"version": "1", "community": "a\nb"}):
        with pytest.raises(ValueError):
            credentials(bad)
    assert credentials({"version": "2c"})["community"] == "public"


def test_cli_passphrases_come_from_the_environment_never_the_command_line(monkeypatch):
    import argparse
    from onboardiq import snmp
    ap = argparse.ArgumentParser()
    ap.add_argument("--community", default="public")
    snmp.add_cli_args(ap)
    assert not any("pass" in a for a in ap.format_help().split() if a.startswith("--"))
    a = ap.parse_args(["--snmp-version", "3", "--v3-user", "ops"])
    monkeypatch.setenv("OIQ_V3_AUTH_PASS", "authpassphrase")
    assert snmp.cli_credentials(a)["v3"]["auth_pass"] == "authpassphrase"


def test_reader_parse_empty_reply_is_a_bad_reply():
    import pytest
    from onboardiq import reader
    for empty in (None, "", "  \n"):
        with pytest.raises(ValueError):
            reader._parse(empty)
