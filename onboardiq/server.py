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
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import agent, mib, replay
from .alerts import Alerter, OUTBOX
from .monitor import Monitor, load_onboarding
from .playback import export_all
from .config import MODEL, ROOT
from .snmp import Snmp
from .verify import prompt_load

WEB = (ROOT / "web").resolve()
DEMO = ROOT / "demo"
RESULTS = ROOT / "results"
ONBOARDED = re.compile(r"onboard_\d{8}_\d{6}")
MAX_MONITOR_S = 30 * 60      # a live monitor stops by itself after 30 minutes
MIB_DIR = Path(os.environ.get("OIQ_MIB_DIR", Path.home() / "data" / "mibs_json")).expanduser()
EFFORT = os.environ.get("OIQ_EFFORT", "high")
LOCAL = {"localhost", "127.0.0.1", "::1"}
FAMILIES = ("cpu", "memory", "temperature", "fan")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".png": "image/png",
         ".svg": "image/svg+xml"}
MAX_BODY = 64 * 1024


class Run:
    def __init__(self, rid, kind="onboard"):
        self.id, self.events, self.confirm = rid, [], queue.Queue()
        self.cond, self.finished = threading.Condition(), False
        self.kind, self.stop, self.opts, self.load = kind, threading.Event(), {}, []

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


def onboarded_list():
    """Saved onboardings (results/onboard_*.json), newest first: name, device, modules, host and port only.
    The SNMP community stored with a terminal onboarding is never sent to the page."""
    out = []
    for p in sorted(RESULTS.glob("onboard_*.json"), reverse=True)[:30]:
        if not ONBOARDED.fullmatch(p.stem):
            continue
        try:
            d = json.loads(p.read_text())
        except ValueError:
            continue
        a = d.get("args") or {}
        if not d.get("spec") or not a.get("modules"):
            continue
        out.append({"name": p.stem, "device": str(d.get("device") or a.get("device") or "")[:200],
                    "modules": a["modules"] if isinstance(a["modules"], str) else ",".join(a["modules"]),
                    "host": a.get("host") or "localhost", "port": a.get("port"),
                    "summary": {f: v.get("verdict") for f, v in d["spec"].items()}})
    return out


def validate_monitor(body: dict) -> dict:
    name = str(body.get("onboarded") or "")
    if not ONBOARDED.fullmatch(name) or not (RESULTS / f"{name}.json").is_file():
        raise ValueError("choose a saved onboarding")
    host = _clean_text(body.get("host") or "localhost", 253)
    port = body.get("port")
    port = None if port in (None, "", 161) else int(port)
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("bad port")
    community = str(body.get("community") or "public")
    if len(community) > 64 or any(ord(c) < 32 for c in community):
        raise ValueError("bad community")
    interval = float(body.get("interval") or 10)
    if not 5 <= interval <= 300:
        raise ValueError("interval must be 5 to 300 seconds")
    return {"onboarded": name, "host": host, "port": port, "community": community, "interval": interval,
            "explain": bool(body.get("explain")), "record": bool(body.get("record", True)),
            "alert": bool(body.get("alert", True)),
            "local": host in LOCAL and port is None}


def start_cpu_load(seconds: int) -> list:
    """CPU load on this machine for `seconds`: stress-ng when installed, else one `yes` per core.
    Every process carries its own timeout, so nothing outlives the request even if the server dies."""
    n = max(1, os.cpu_count() or 2)
    if shutil.which("stress-ng"):
        cmd = [["stress-ng", "--cpu", str(n), "--timeout", f"{seconds}s", "--quiet"]]
    else:
        cmd = [["timeout", str(seconds), "yes"]] * n
    return [subprocess.Popen(c, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for c in cmd]


def stop_load(run):
    for p in run.load:
        if p.poll() is None:
            p.terminate()
    run.load = []


def watch(run: Run, opts: dict):
    """Live health monitor: one reading every `interval` seconds until stopped (or 30 minutes)."""
    rec = None
    try:
        saved, mods = load_onboarding(RESULTS / f"{opts['onboarded']}.json")
        device = str(saved.get("device") or saved.get("args", {}).get("device") or "device")[:200]
        snmp = Snmp(opts["host"], opts["community"], port=opts["port"])
        mon = Monitor(snmp, saved, mods, STATE["mib_dir"], explain_on=opts["explain"],
                      alerter=Alerter(device, outbox=STATE.get("outbox")) if opts.get("alert") else None)
        if opts["record"]:
            rec = DEMO / f"{slug(device)}-health-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        run.emit({"kind": "monitor-start", "device": device, "interval": opts["interval"],
                  "policy": f"{mon.pol['name']} ({mon.pol['source']})", "local": opts["local"],
                  "explain": opts["explain"], "record": bool(rec)})
        began, fails, first = time.time(), 0, True
        while not run.stop.is_set() and time.time() - began < MAX_MONITOR_S:
            t0 = time.time()
            try:
                res = mon.poll()
                fails = 0
            except Exception as e:  # noqa: BLE001 - a missed poll is reported, the monitor carries on
                fails += 1
                run.emit({"kind": "poll-error", "message": type(e).__name__ + ": " + str(e)[:200]})
                if fails >= 5:
                    raise RuntimeError("the device did not answer 5 polls in a row") from e
                res = None
            if res is not None:
                if rec:
                    line = {**res, "device": device} if first else res
                    with open(rec, "a") as fh:
                        fh.write(json.dumps(line) + "\n")
                first = False
                run.emit({"kind": "health", **res})
            run.stop.wait(max(0.0, opts["interval"] - (time.time() - t0)))
        saved_as = None
        if rec and rec.exists():
            export_all([rec])
            saved_as = rec.stem
        run.emit({"kind": "end", "monitor": True, "saved": saved_as,
                  "reason": "stopped" if run.stop.is_set() else "30-minute limit reached"})
    except Exception as e:  # noqa: BLE001
        run.emit({"kind": "error", "message": type(e).__name__ + ": " + str(e)[:300]})
    finally:
        stop_load(run)
        run.finished = True
        with run.cond:
            run.cond.notify_all()


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
        # saved like a terminal onboarding, so the live monitor can pick it up; no community is stored
        RESULTS.mkdir(exist_ok=True)
        ob = f"onboard_{time.strftime('%Y%m%d_%H%M%S')}"
        args = {"device": opts["device"], "modules": ",".join(opts["modules"]), "host": opts["host"],
                "port": opts["port"], "lab": lab, "families": ",".join(opts["families"])}
        (RESULTS / f"{ob}.json").write_text(json.dumps({"args": args, **out}, indent=2, default=str))
        run.emit({"kind": "end", "onboarded": ob, "summary": {f: v["verdict"] for f, v in out["spec"].items()},
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
        m = re.fullmatch(r"/api/outbox/(alert-\d{8}-\d{6}\.html)", url.path)
        if m:
            f = (STATE.get("outbox") or OUTBOX) / m.group(1)
            if not f.is_file():
                return self._send(404, b'{"error":"not found"}')
            # the email is escaped HTML; the CSP also forbids scripts and any outside loads
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8",
                              {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"})
        if url.path == "/api/onboarded":
            return self._json(200, onboarded_list())
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
        if url == "/api/monitor":
            with STATE["lock"]:
                if STATE["run"] and not STATE["run"].finished:
                    return self._json(409, {"error": "a run is already in progress"})
                try:
                    opts = validate_monitor(body)
                except (ValueError, TypeError) as e:
                    return self._json(400, {"error": str(e)})
                run = Run(f"{int(time.time() * 1000) & 0xFFFFFFFFFFFF:012x}", kind="monitor")
                run.opts = opts
                STATE["run"] = run
            threading.Thread(target=watch, args=(run, opts), daemon=True).start()
            return self._json(202, {"id": run.id})
        m = re.fullmatch(r"/api/runs/([a-f0-9]{12})/(stop|load)", url)
        if m:
            run = STATE["run"]
            if not run or run.id != m.group(1) or run.finished or run.kind != "monitor":
                return self._json(404, {"error": "no such monitor"})
            if m.group(2) == "stop":
                run.stop.set()
                return self._json(200, {"ok": True})
            if not run.opts.get("local"):
                return self._json(400, {"error": "load runs only on this machine's own agent (localhost, default port)"})
            if any(p.poll() is None for p in run.load):
                return self._json(409, {"error": "load is already running"})
            secs = int(body.get("seconds") or 120)
            if not 10 <= secs <= 300:
                return self._json(400, {"error": "load seconds must be 10 to 300"})
            run.load = start_cpu_load(secs)
            run.emit({"kind": "load", "seconds": secs, "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())})
            return self._json(200, {"ok": True, "seconds": secs})
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
