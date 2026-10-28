"""Local live server: the same web page as the static replays, plus live onboarding runs.

    python -m onboardiq.server            # http://127.0.0.1:8765

Security model (this server can start load experiments and holds an API key):
- listens on 127.0.0.1 only; the Host header must name 127.0.0.1/localhost on this port
  (defeats DNS rebinding);
- state-changing requests must be POST with application/json, a custom X-OnboardIQ header
  and, when the browser sends one, a same-origin Origin header (a cross-site page cannot set
  the header without a CORS preflight, which this server never approves);
- request bodies are capped at 64 KB and validated field by field; one run at a time;
- the API key never leaves the process; the SNMP community is kept in memory for the run
  only and is never echoed, logged or recorded;
- static files are served only from web/, with the resolved path checked to stay inside it.
"""
import json
import os
import queue
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import agent, mib, replay
from .playback import export_all
from .config import MODEL, ROOT
from .snmp import Snmp
from .verify import prompt_load

WEB = (ROOT / "web").resolve()
DEMO = ROOT / "demo"
MIB_DIR = Path(os.environ.get("OIQ_MIB_DIR", Path.home() / "data" / "mibs_json")).expanduser()
EFFORT = os.environ.get("OIQ_EFFORT", "high")
LOCAL = {"localhost", "127.0.0.1", "::1"}
FAMILIES = ("cpu", "memory", "temperature", "fan")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".png": "image/png",
         ".svg": "image/svg+xml"}
MAX_BODY = 64 * 1024


class Run:
    def __init__(self, rid):
        self.id, self.events, self.confirm = rid, [], queue.Queue()
        self.cond, self.finished = threading.Condition(), False

    def emit(self, ev):
        with self.cond:
            self.events.append(ev)
            self.cond.notify_all()


STATE = {"run": None, "lock": threading.Lock(), "port": 8765, "mib_dir": MIB_DIR}


def _clean_text(x, n=200):
    x = str(x or "").strip()
    if not x or len(x) > n or any(ord(c) < 32 for c in x):
        raise ValueError("invalid text field")
    return x


def module_names():
    d = STATE["mib_dir"]
    return sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []


def resolve_module(m) -> str:
    """Exact name, else a case-insensitive unique prefix (HOST-RES -> HOST-RESOURCES-MIB).
    Names come only from the MIB folder listing, so the result is always a real file stem."""
    m = str(m).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,80}", m):
        raise ValueError(f"invalid MIB module name {m[:40]!r}")
    names = module_names()
    if m in names:
        return m
    hits = [n for n in names if n.upper() == m.upper()] or [n for n in names if n.upper().startswith(m.upper())]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise ValueError(f"'{m}' matches several modules: {', '.join(hits[:6])}{'…' if len(hits) > 6 else ''}")
    near = [n for n in names if m.upper() in n.upper()][:6]
    raise ValueError(f"unknown MIB module '{m}'" + (f"; did you mean {', '.join(near)}?" if near else ""))


def validate(body: dict) -> dict:
    device = _clean_text(body.get("device"))
    modules = body.get("modules") or []
    if not isinstance(modules, list) or not 1 <= len(modules) <= 12:
        raise ValueError("choose 1 to 12 MIB modules")
    modules = [resolve_module(m) for m in modules]
    host = _clean_text(body.get("host") or "localhost", 253)
    port = body.get("port")
    port = None if port in (None, "", 161) else int(port)
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("bad port")
    families = [f for f in (body.get("families") or FAMILIES) if f in FAMILIES] or list(FAMILIES)
    lab = bool(body.get("lab"))
    if lab and (host not in LOCAL or port is not None):
        raise ValueError("lab experiments only run against this machine's own agent (localhost, default port)")
    load_by_hand = body.get("load_by_hand")
    load_by_hand = _clean_text(load_by_hand, 300) if load_by_hand else None
    community = str(body.get("community") or "public")
    if len(community) > 64 or any(ord(c) < 32 for c in community):
        raise ValueError("bad community")
    return {"device": device, "modules": modules, "host": host, "port": port, "families": families,
            "lab": lab, "load_by_hand": load_by_hand, "community": community,
            "record": bool(body.get("record", True))}


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "device"


def execute(run: Run, opts: dict):
    try:
        cands = mib.candidates(STATE["mib_dir"], opts["modules"])
        snmp = Snmp(opts["host"], opts["community"], port=opts["port"])
        dev = replay.Recorder(snmp) if opts["record"] else snmp
        verify_kw, lab = {}, opts["lab"]
        if opts["load_by_hand"]:
            def ask(_msg):
                run.emit({"kind": "prompt-wait"})
                run.confirm.get(timeout=900)        # the browser confirms start / stop

            def say(msg):
                if msg.strip():
                    run.emit({"kind": "prompt", "text": msg.strip().lstrip(">").strip()})
            verify_kw["load"] = prompt_load({"cpu": opts["load_by_hand"]}, ask=ask, say=say)
            lab = True
        run.emit({"kind": "start", "device": opts["device"], "objects": len(cands),
                  "modules": ",".join(opts["modules"]), "model": MODEL, "live": True})
        out = agent.onboard(opts["device"], cands, dev, lab=lab, families=opts["families"],
                            effort=EFFORT, log=lambda m: None, on_event=run.emit, verify_kw=verify_kw,
                            modules=opts["modules"])
        saved = None
        if opts["record"]:
            name = f"{slug(opts['device'])}-{time.strftime('%Y%m%d-%H%M%S')}"
            path = DEMO / f"{name}.json"
            keep = {"device": opts["device"], "modules": ",".join(opts["modules"]), "lab": lab,
                    "families": ",".join(opts["families"]), "effort": EFFORT, "model": MODEL, "max_attempts": 3}
            dev.save(path, args=keep, cands=cands, result={f: v["verdict"] for f, v in out["spec"].items()})
            export_all()
            saved = name
        run.emit({"kind": "end", "summary": {f: v["verdict"] for f, v in out["spec"].items()},
                  "cost_usd": out["cost_usd"], "seconds": out["seconds"], "snmp_calls": out["snmp_calls"],
                  "saved": saved})
    except Exception as e:  # noqa: BLE001 - reported to the page without internals
        run.emit({"kind": "error", "message": type(e).__name__ + ": " + str(e)[:300]})
    finally:
        run.finished = True
        with run.cond:
            run.cond.notify_all()


class Handler(BaseHTTPRequestHandler):
    server_version = "OnboardIQ"

    def log_message(self, fmt, *args):   # quiet; never logs bodies
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in {f"127.0.0.1:{STATE['port']}", f"localhost:{STATE['port']}"}

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode())

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b'{"error":"bad host"}')
        url = urlparse(self.path)
        if url.path == "/api/status":
            run = STATE["run"]
            return self._json(200, {"live": True, "running": bool(run and not run.finished),
                                    "mib_dir_found": STATE["mib_dir"].is_dir()})
        if url.path == "/api/modules":
            q = (parse_qs(url.query).get("q") or [""])[0].upper()[:40]
            names = module_names()
            return self._json(200, [n for n in names if q in n.upper()][:40])
        m = re.fullmatch(r"/api/runs/([a-f0-9]{12})/events", url.path)
        if m:
            return self._stream(m.group(1))
        return self._static(url.path)

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB / rel).resolve()
        if WEB not in target.parents or not target.is_file():
            return self._send(404, b'{"error":"not found"}')
        ctype = TYPES.get(target.suffix)
        if not ctype:
            return self._send(404, b'{"error":"not found"}')
        self._send(200, target.read_bytes(), ctype)

    def _stream(self, rid):
        run = STATE["run"]
        if not run or run.id != rid:
            return self._send(404, b'{"error":"no such run"}')
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        sent = 0
        try:
            while True:
                with run.cond:
                    while sent >= len(run.events) and not run.finished:
                        run.cond.wait(timeout=15)
                        if sent >= len(run.events) and not run.finished:
                            self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                    batch = run.events[sent:]
                for ev in batch:
                    self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
                sent += len(batch)
                self.wfile.flush()
                if run.finished and sent >= len(run.events):
                    return
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, b'{"error":"bad host"}')
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{STATE['port']}", f"http://localhost:{STATE['port']}"}:
            return self._send(403, b'{"error":"bad origin"}')
        if self.headers.get("X-OnboardIQ") != "1" or \
                not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._send(403, b'{"error":"missing header"}')
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._send(413, b'{"error":"too large"}')
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, b'{"error":"bad json"}')
        url = urlparse(self.path).path
        if url == "/api/runs":
            with STATE["lock"]:
                if STATE["run"] and not STATE["run"].finished:
                    return self._json(409, {"error": "a run is already in progress"})
                try:
                    opts = validate(body)
                except (ValueError, TypeError) as e:
                    return self._json(400, {"error": str(e)})
                run = Run(f"{int(time.time() * 1000) & 0xFFFFFFFFFFFF:012x}")
                STATE["run"] = run
            threading.Thread(target=execute, args=(run, opts), daemon=True).start()
            return self._json(202, {"id": run.id})
        m = re.fullmatch(r"/api/runs/([a-f0-9]{12})/confirm", url)
        if m:
            run = STATE["run"]
            if not run or run.id != m.group(1) or run.finished:
                return self._json(404, {"error": "no such run"})
            run.confirm.put(True)
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})


def main(port: int = 8765):
    STATE["port"] = port
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"OnboardIQ live: http://127.0.0.1:{port}  (localhost only; Ctrl+C to stop)")
    print(f"  model {MODEL}, effort {EFFORT}, MIB folder {STATE['mib_dir']}"
          + ("" if STATE["mib_dir"].is_dir() else "  (NOT FOUND)"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--mib-dir", help="folder of converted MIB modules (default: $OIQ_MIB_DIR or ~/data/mibs_json)")
    a = ap.parse_args()
    if a.mib_dir:
        STATE["mib_dir"] = Path(a.mib_dir).expanduser()
    main(a.port)
