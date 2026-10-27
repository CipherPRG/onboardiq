"""The live server only answers this machine, refuses cross-site requests and never leaves web/."""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from onboardiq import server


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    mibs = tmp_path_factory.mktemp("mibs")
    (mibs / "TEST-MIB.json").write_text("{}")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    server.STATE.update(port=port, mib_dir=mibs)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield port
    httpd.shutdown()


def req(port, method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    return r.status, r.read()


def test_status_and_page(srv):
    assert req(srv, "GET", "/api/status")[0] == 200
    code, page = req(srv, "GET", "/")
    assert code == 200 and b"OnboardIQ" in page


def test_other_hosts_refused(srv):
    assert req(srv, "GET", "/api/status", host="evil.example:80")[0] == 403      # DNS rebinding
    assert req(srv, "POST", "/api/runs", {}, host="evil.example:80")[0] == 403


def test_posts_need_header_and_same_origin(srv):
    good = {"Content-Type": "application/json", "X-OnboardIQ": "1"}
    assert req(srv, "POST", "/api/runs", {"device": "x"}, {"Content-Type": "application/json"})[0] == 403
    assert req(srv, "POST", "/api/runs", {"device": "x"},
               {**good, "Origin": "https://evil.example"})[0] == 403
    assert req(srv, "POST", "/api/runs", {"device": "x"}, {**good, "Content-Type": "text/plain"})[0] == 403


def test_validation(srv):
    h = {"Content-Type": "application/json", "X-OnboardIQ": "1", "Origin": f"http://127.0.0.1:{srv}"}
    code, body = req(srv, "POST", "/api/runs", {"device": "box", "modules": ["NOPE-MIB"]}, h)
    assert code == 400 and b"unknown MIB" in body
    code, body = req(srv, "POST", "/api/runs", {"device": "box", "modules": ["../../etc/passwd"]}, h)
    assert code == 400
    code, body = req(srv, "POST", "/api/runs", {"device": "box", "modules": ["TEST-MIB"], "lab": True,
                                                "host": "127.0.0.1", "port": 16161}, h)
    assert code == 400 and b"lab" in body
    code, body = req(srv, "POST", "/api/runs", {"device": "a\nb", "modules": ["TEST-MIB"]}, h)
    assert code == 400


@pytest.mark.parametrize("path", ["/../onboardiq/config.py", "/..%2f.env", "/sessions/../../.env",
                                  "/%2e%2e/%2e%2e/etc/passwd", "/player.py"])
def test_static_stays_in_web(srv, path):
    assert req(srv, "GET", path)[0] == 404


def test_body_size_limit(srv):
    h = {"Content-Type": "application/json", "X-OnboardIQ": "1"}
    code, _ = req(srv, "POST", "/api/runs", {"device": "x" * 70000}, h)
    assert code == 413


def test_module_prefix_resolution(tmp_path):
    for n in ("HOST-RESOURCES-MIB", "HOST-RESOURCES-TYPES", "UCD-SNMP-MIB"):
        (tmp_path / f"{n}.json").write_text("{}")
    saved = server.STATE["mib_dir"]
    server.STATE["mib_dir"] = tmp_path
    try:
        assert server.resolve_module("ucd") == "UCD-SNMP-MIB"
        assert server.resolve_module("HOST-RESOURCES-MIB") == "HOST-RESOURCES-MIB"
        import pytest
        with pytest.raises(ValueError, match="several"):
            server.resolve_module("HOST-RES")
        with pytest.raises(ValueError, match="did you mean UCD-SNMP-MIB"):
            server.resolve_module("SNMP")
        with pytest.raises(ValueError, match="invalid"):
            server.resolve_module("../x")
    finally:
        server.STATE["mib_dir"] = saved
