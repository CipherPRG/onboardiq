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


def test_gate_enforces_cooldown_and_run_limit(monkeypatch):
    monkeypatch.setenv("OIQ_PAID", "on")
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
    monkeypatch.setattr(server, "budget_left", lambda: 5.0)
    monkeypatch.setenv("OIQ_PAID", "on")       # CI runs with OIQ_PAID=off (the kill switch); these tests need runs to start
    server.STATE["sandbox"].clear()
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


# ---- abuse limits -------------------------------------------------------------------------------------------

def test_client_ip_trusts_only_the_proxys_entry():
    # a visitor can write anything on the left of X-Forwarded-For; our proxy appends the real address on the right
    assert server.client_ip("6.6.6.6, 203.0.113.9", "10.0.0.1", hops=1) == "203.0.113.9"
    assert server.client_ip("203.0.113.9", "10.0.0.1", hops=1) == "203.0.113.9"
    assert server.client_ip(None, "10.0.0.1", hops=1) == "10.0.0.1"
    assert server.client_ip("not-an-ip", "10.0.0.1", hops=1) == "10.0.0.1"
    assert server.client_ip("1.1.1.1, 2.2.2.2, 3.3.3.3", "10.0.0.1", hops=2) == "2.2.2.2"
    assert server.client_ip("2001:db8:1:2:aaaa::1", "10.0.0.1", hops=1) == \
        server.client_ip("2001:db8:1:2:bbbb::9", "10.0.0.1", hops=1)         # one IPv6 subscriber, one /64


def test_each_visitor_gets_a_daily_number_of_runs():
    t, now = {}, 1_000_000.0
    for i in range(3):
        assert server.ip_gate(t, "203.0.113.9", now + i, per_ip=3) is None
        server.ip_note(t, "203.0.113.9", now + i)
    assert "used its 3" in server.ip_gate(t, "203.0.113.9", now + 10, per_ip=3)
    assert server.ip_gate(t, "198.51.100.7", now + 10, per_ip=3) is None       # someone else is not affected
    assert server.ip_gate(t, "203.0.113.9", now + server.DAY + 5, per_ip=3) is None   # a day later


def test_the_address_table_is_bounded():
    t = {}
    for i in range(50):
        server.ip_note(t, f"10.0.{i // 250}.{i % 250}", float(i), cap=20)
    assert len(t) <= 20


def test_kill_switch_budget_and_visitor_limit_are_checked_before_a_run():
    st = {"runs": 0, "last_end": 0.0}
    assert "paused" in server.sandbox_gate(st, now=1e9, paid=False)[0]
    assert "budget" in server.sandbox_gate(st, now=1e9, paid=True, budget_left=0.05)[0]
    assert server.sandbox_gate(st, now=1e9, paid=True, budget_left=2.0, ip="1.2.3.4", per_ip=3) is None
    st["ips"] = {"1.2.3.4": [1e9 - 5] * 3}
    assert "today" in server.sandbox_gate(st, now=1e9, paid=True, budget_left=2.0, ip="1.2.3.4", per_ip=3)[0]


def test_kill_switch_env_stops_a_run_before_any_model_call(sandbox_srv, monkeypatch):
    port, started = sandbox_srv
    monkeypatch.setenv("OIQ_PAID", "off")
    code, out = post(port, "/api/runs", {})
    assert code == 429 and "paused" in out["error"] and not started


def test_the_per_visitor_limit_applies_over_http(sandbox_srv, monkeypatch):
    port, started = sandbox_srv
    monkeypatch.setattr(server, "SANDBOX_MAX_RUNS", 10)
    monkeypatch.setattr(server, "SANDBOX_COOLDOWN_S", 0)
    monkeypatch.setattr(server, "SANDBOX_PER_IP", 2)
    codes = [post(port, "/api/runs", {})[0] for _ in range(3)]
    assert codes == [202, 202, 429] and len(started) == 2


def test_a_spoofed_forwarded_header_does_not_buy_more_runs(sandbox_srv, monkeypatch):
    port, started = sandbox_srv
    monkeypatch.setattr(server, "SANDBOX_MAX_RUNS", 10)
    monkeypatch.setattr(server, "SANDBOX_COOLDOWN_S", 0)
    monkeypatch.setattr(server, "SANDBOX_PER_IP", 1)
    monkeypatch.setattr(server, "TRUSTED_HOPS", 1)
    codes = []
    for fake in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/api/runs", body=b"{}", headers={
            "Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "X-OnboardIQ": "1",
            "X-Forwarded-For": f"{fake}, 203.0.113.9"})        # the right-most entry is the proxy's, the same each time
        codes.append(c.getresponse().status)
    assert codes == [202, 429, 429]


# ---- headers and malformed requests -------------------------------------------------------------------------

def test_every_page_carries_the_security_headers(sandbox_srv):
    port, _ = sandbox_srv
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    r.read()
    h = {k.lower(): v for k, v in r.getheaders()}
    assert "frame-ancestors 'none'" in h["content-security-policy"] and "script-src 'self'" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "no-referrer" and "max-age" in h["strict-transport-security"]
    assert "'unsafe-inline'" not in h["content-security-policy"] and "unsafe-eval" not in h["content-security-policy"]


def test_bad_content_length_is_refused_not_waited_on(sandbox_srv):
    port, started = sandbox_srv
    for bad in ("-1", "abc"):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/api/runs", body=b"", headers={
            "Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "X-OnboardIQ": "1",
            "Content-Length": bad})
        assert c.getresponse().status == 400
    assert not started


def test_run_ids_are_random_and_the_sandbox_hides_saved_results(sandbox_srv, monkeypatch):
    port, _ = sandbox_srv
    monkeypatch.setattr(server, "SANDBOX_MAX_RUNS", 10)
    monkeypatch.setattr(server, "SANDBOX_COOLDOWN_S", 0)
    monkeypatch.setattr(server, "SANDBOX_PER_IP", 10)
    ids = {post(port, "/api/runs", {})[1]["id"] for _ in range(4)}
    assert len(ids) == 4 and all(len(i) == 12 for i in ids)
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/api/onboarded", headers={"Host": f"127.0.0.1:{port}"})
    assert json.loads(c.getresponse().read()) == []
    c.close()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/api/outbox/alert-20260101-000000.html", headers={"Host": f"127.0.0.1:{port}"})
    assert c.getresponse().status == 404


def test_the_hosted_demo_takes_no_mib_uploads(sandbox_srv):
    port, _ = sandbox_srv
    code, body = post(port, "/api/mibs", {"files": [{"name": "x.mib", "text": "X DEFINITIONS ::= BEGIN END"}]})
    assert code == 404
    code, _ = post(port, "/api/mibs", {"files": [{"name": "x", "text": "y" * 70_000}]})
    assert code == 413                                      # the big-body allowance is local only
