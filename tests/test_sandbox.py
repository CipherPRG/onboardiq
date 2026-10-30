"""The hosted sandbox: a run can only be the one fixed demo run, whatever the browser sends."""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from onboardiq import server


def test_sandbox_options_ignore_the_request():
    o = server.sandbox_options()
    assert o["host"] == "localhost" and o["port"] is None and o["lab"] is True
    assert o["modules"] == ["UCD-SNMP-MIB", "HOST-RESOURCES-MIB"] and o["load_by_hand"] is None
    assert o["record"] is False and o["device"] == server.SANDBOX_DEVICE
    assert o == server.sandbox_options()                 # nothing request-dependent can change it


def test_hosts_and_origins():
    assert server.host_allowed("127.0.0.1:8765", 8765, sandbox=False, public_hosts=set())
    assert not server.host_allowed("demo.run.app", 8765, sandbox=False, public_hosts=set())     # not a sandbox
    assert server.host_allowed("demo.run.app", 8765, sandbox=True, public_hosts=set())          # any *.run.app
    assert not server.host_allowed("evil.example", 8765, sandbox=True, public_hosts=set())
    assert server.host_allowed("demo.example.org", 8765, sandbox=True, public_hosts={"demo.example.org"})
    assert not server.host_allowed("demo.run.app", 8765, sandbox=True, public_hosts={"demo.example.org"})
    assert server.origin_allowed(None, "demo.run.app", 8765, sandbox=True)
    assert server.origin_allowed("https://demo.run.app", "demo.run.app", 8765, sandbox=True)
    assert not server.origin_allowed("https://evil.example", "demo.run.app", 8765, sandbox=True)
    assert not server.origin_allowed("https://demo.run.app", "demo.run.app", 8765, sandbox=False)


def test_gate_enforces_cooldown_and_run_limit():
    st = {"runs": 0, "last_end": 100.0}
    assert server.sandbox_gate(st, now=100.0 + 61, cooldown=60, max_runs=3) is None
    msg, wait = server.sandbox_gate(st, now=100.0 + 10, cooldown=60, max_runs=3)
    assert wait == 51 and "cooling down" in msg
    st["runs"] = 3
    msg, wait = server.sandbox_gate(st, now=1e9, cooldown=60, max_runs=3)
    assert "run limit" in msg and wait == 0


@pytest.fixture()
def sandbox_srv(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "SANDBOX", True)
    monkeypatch.setattr(server, "SANDBOX_MAX_RUNS", 1)
    started = []
    monkeypatch.setattr(server, "execute", lambda run, opts: started.append(opts) or setattr(run, "finished", True))
    server.STATE["sandbox"].update(runs=0, last_end=0.0)
    server.STATE["run"] = None
    mibs = tmp_path / "m"
    mibs.mkdir()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    server.STATE.update(port=port, mib_dir=mibs)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield port, started
    httpd.shutdown()
    server.STATE["run"] = None


def post(port, path, body):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", path, body=json.dumps(body).encode(),
              headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "X-OnboardIQ": "1"})
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}")


def test_a_hostile_request_still_runs_only_the_fixed_demo(sandbox_srv):
    port, started = sandbox_srv
    code, out = post(port, "/api/runs", {"host": "10.0.0.5", "port": 22, "modules": ["EVIL-MIB"], "device": "x" * 50,
                                         "lab": False, "load_by_hand": "rm -rf /", "community": "secret"})
    assert code == 202 and len(started) == 1
    assert started[0] == server.sandbox_options()
    code, out = post(port, "/api/runs", {})
    assert code == 429 and "run limit" in out["error"]          # second run refused (limit is 1 in this test)


def test_monitor_mode_is_off_in_the_sandbox(sandbox_srv):
    port, _ = sandbox_srv
    code, out = post(port, "/api/monitor", {"onboarded": "onboard_20260101_000000"})
    assert code == 404 and "not available" in out["error"]


def test_status_reports_the_sandbox(sandbox_srv):
    port, _ = sandbox_srv
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/api/status", headers={"Host": f"127.0.0.1:{port}"})
    st = json.loads(c.getresponse().read())
    assert st["sandbox"] is True and st["runs_left"] == 1 and st["modules"] == server.SANDBOX_MODULES


def test_no_cache_flag_skips_the_model_cache(monkeypatch, tmp_path):
    from onboardiq import llm
    f = tmp_path / "llm"
    f.mkdir()
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path)
    monkeypatch.setenv("OIQ_NO_CACHE", "1")
    monkeypatch.setattr(llm, "guard", lambda p: (_ for _ in ()).throw(RuntimeError("reached the network path")))
    monkeypatch.setattr(llm.config, "require_meta", lambda m: None)
    import hashlib
    msgs = [{"role": "user", "content": "x"}]
    request = {"model": "muse-spark-1.3", "messages": msgs, "max_tokens": 10}
    key = hashlib.sha256(json.dumps([request, None], sort_keys=True).encode()).hexdigest()
    (f / f"{key}.json").write_text(json.dumps({"content": "cached"}))
    with pytest.raises(RuntimeError, match="network path"):        # the cache hit was ignored
        llm._chat_one(msgs, "muse-spark-1.3", 10, None, None, True)


def test_sandbox_errors_do_not_leak_paths(monkeypatch):
    err = FileNotFoundError("/srv/app/secret/path/UCD-SNMP-MIB.json not found")
    monkeypatch.setattr(server, "SANDBOX", False)
    assert "/srv/app" in server.public_error(err)                  # locally the detail helps the operator
    monkeypatch.setattr(server, "SANDBOX", True)
    msg = server.public_error(err)
    assert "/srv" not in msg and "UCD" not in msg and "recorded session" in msg
    from onboardiq.spend_guard import SpendLimitError
    assert "budget" in server.public_error(SpendLimitError("daily cap: spent $3"))
