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

Hosted sandbox (OIQ_SANDBOX=1, used by the public demo): the server ignores everything the browser sends
for a run. The target is this container's own read-only agent, the MIB modules, the device name and the load
mode are fixed in code, so nothing a visitor types reaches SNMP, a shell or the model prompt. One run at a
time, a cooldown between runs, a cap on runs per process, the daily dollar cap of spend_guard, no monitor mode,
and the model is called fresh every time (OIQ_NO_CACHE=1) so a run is never a cached answer. Also: a per-visitor
daily limit, the OIQ_PAID=off kill switch, random run ids, strict security headers, nothing written to disk.
See docs/SANDBOX.md.
"""
import ipaddress
import json
import os
import queue
import re
import secrets
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
from .snmp import Snmp, credentials
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

SANDBOX = os.environ.get("OIQ_SANDBOX") == "1"
# hosts the public demo answers to (comma-separated); empty = any *.run.app name (Cloud Run's front end checks it)
PUBLIC_HOSTS = {h.strip().lower() for h in os.environ.get("OIQ_PUBLIC_HOST", "").split(",") if h.strip()}
SANDBOX_DEVICE = "Linux host running the net-snmp agent (hosted demo device)"
SANDBOX_MODULES = ["UCD-SNMP-MIB", "HOST-RESOURCES-MIB"]
SANDBOX_COOLDOWN_S = int(os.environ.get("OIQ_SANDBOX_COOLDOWN", "60"))
SANDBOX_MAX_RUNS = int(os.environ.get("OIQ_SANDBOX_MAX_RUNS", "40"))
SANDBOX_PER_IP = int(os.environ.get("OIQ_SANDBOX_PER_IP", "3"))          # runs per visitor address per 24 h
SANDBOX_MIN_BUDGET = float(os.environ.get("OIQ_SANDBOX_MIN_BUDGET", "0.30"))   # dollars that must remain to start
SANDBOX_ATTEMPTS = int(os.environ.get("OIQ_SANDBOX_ATTEMPTS", "3"))      # model tries per metric (3 = two re-reads)
SANDBOX_EFFORT = os.environ.get("OIQ_SANDBOX_EFFORT") or EFFORT
TRUSTED_HOPS = int(os.environ.get("OIQ_TRUSTED_HOPS", "1"))             # proxies that append to X-Forwarded-For
# inside the container the agent listens on an unprivileged port, so nothing in the image needs root
LOCAL_SNMP_PORT = int(os.environ.get("OIQ_LOCAL_SNMP_PORT") or 0) or None
MAX_VIEWERS = 8                    # event-stream readers per run
DAY = 24 * 3600
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
       "form-action 'none'; frame-ancestors 'none'")


def host_allowed(host, port, sandbox=None, public_hosts=None):
    """Is this Host header one the server answers to? Locally: 127.0.0.1 / localhost on this port only
    (defeats DNS rebinding). In the sandbox the public name(s) are accepted too."""
    host = (host or "").lower()
    if host in {f"127.0.0.1:{port}", f"localhost:{port}"}:
        return True
    sandbox = SANDBOX if sandbox is None else sandbox
    hosts = PUBLIC_HOSTS if public_hosts is None else public_hosts
    if not sandbox:
        return False
    return host in hosts if hosts else host.endswith(".run.app")


def origin_allowed(origin, host, port, sandbox=None):
    """Same-origin check for state-changing requests."""
    if not origin:
        return True
    if origin in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
        return True
    sandbox = SANDBOX if sandbox is None else sandbox
    return bool(sandbox and host and origin == f"https://{host.lower()}")


def public_error(e):
    """What the page is told about a failed run. Locally the type and message help the operator; in the hosted
    sandbox the real error stays in the server log (paths and internals are not shown to visitors)."""
    if not SANDBOX:
        return type(e).__name__ + ": " + str(e)[:300]
    import sys
    print(f"sandbox run failed: {type(e).__name__}: {str(e)[:500]}", file=sys.stderr, flush=True)
    if type(e).__name__ == "SpendLimitError":
        return "The demo's budget for now is used up. Play a recorded session instead."
    return "The live run could not finish on the server. Try again in a minute, or play a recorded session."


def sandbox_options():
    """The only run the hosted demo allows. Nothing here comes from the request."""
    return {"device": SANDBOX_DEVICE, "modules": list(SANDBOX_MODULES), "host": "localhost",
            "port": LOCAL_SNMP_PORT, "families": list(FAMILIES), "lab": True, "load_by_hand": None,
            "snmp": credentials({}), "record": False}


def client_ip(xff, peer, hops=None):
    """The visitor's address. X-Forwarded-For is client-controlled on its left; only the entries appended by our
    own proxy (the right-most `hops`) can be trusted, so the visitor's address is the `hops`-th from the right.
    IPv6 visitors are grouped by /64 (one subscriber holds a whole /64, so per-address limits would be moot)."""
    hops = TRUSTED_HOPS if hops is None else hops
    parts = [p.strip() for p in (xff or "").split(",") if p.strip()]
    cand = parts[-hops] if hops > 0 and len(parts) >= hops else peer
    try:
        ip = ipaddress.ip_address(cand)
    except ValueError:
        ip = ipaddress.ip_address(peer)
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address)
    return str(ip)


def ip_gate(table, ip, now, per_ip=None):
    """None if this address may start another run today, else a message. `table` maps address -> start times."""
    per_ip = SANDBOX_PER_IP if per_ip is None else per_ip
    recent = [t for t in table.get(ip, []) if now - t < DAY]
    if recent:
        table[ip] = recent
    else:
        table.pop(ip, None)
    if len(recent) >= per_ip:
        return (f"This address has used its {per_ip} live runs for today. Play a recorded session, or run "
                "OnboardIQ yourself (README: docker compose up).")
    return None


def ip_note(table, ip, now, cap=5000):
    """Remember a started run. The table is bounded: past `cap` addresses the stalest entries are dropped."""
    table.setdefault(ip, []).append(now)
    if len(table) > cap:
        for k in sorted(table, key=lambda a: table[a][-1])[: len(table) - cap // 2]:
            del table[k]


def sandbox_gate(state, now=None, cooldown=None, max_runs=None, paid=None, budget_left=None, ip=None, per_ip=None):
    """None if a sandbox run may start, else (message, seconds to wait). Checked in this order: the owner's
    kill switch, the dollar budget, the run limit, this visitor's daily limit, the cooldown."""
    now = time.time() if now is None else now
    cooldown = SANDBOX_COOLDOWN_S if cooldown is None else cooldown
    max_runs = SANDBOX_MAX_RUNS if max_runs is None else max_runs
    paid = os.environ.get("OIQ_PAID", "on").lower() != "off" if paid is None else paid
    if not paid:
        return ("The live demo is paused by its owner. Play a recorded session instead.", 0)
    if budget_left is not None and budget_left < SANDBOX_MIN_BUDGET:
        return ("The demo's budget for today is used up. Play a recorded session instead.", 0)
    if state["runs"] >= max_runs:
        return ("The hosted demo has reached its run limit for now. Play a recorded session instead, or run "
                "OnboardIQ yourself (README: docker compose up).", 0)
    if ip is not None:
        why = ip_gate(state.setdefault("ips", {}), ip, now, per_ip)
        if why:
            return (why, 0)
    wait = state["last_end"] + cooldown - now
    if wait > 0:
        return (f"The demo device is cooling down after the last run. Try again in {int(wait) + 1} s.", int(wait) + 1)
    return None


def budget_left():
    """Dollars left today on the model account, from the same ledger the spend guard enforces."""
    try:
        from . import llm
        g = llm.guard("meta")
        return max(g.daily_usd - g.spent_today()["usd"], 0.0)
    except Exception:  # noqa: BLE001 - an unreadable ledger must stop runs, not allow them
        return 0.0


class Run:
    def __init__(self, rid, kind="onboard"):
        self.id, self.events, self.confirm = rid, [], queue.Queue()
        self.cond, self.finished = threading.Condition(), False
        self.kind, self.stop, self.opts, self.load = kind, threading.Event(), {}, []
        self.viewers = 0

    def emit(self, ev):
        with self.cond:
            self.events.append(ev)
            self.cond.notify_all()


STATE = {"sandbox": {"runs": 0, "last_end": 0.0}, "run": None, "lock": threading.Lock(), "port": 8765, "mib_dir": MIB_DIR}


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
    creds = credentials(body)
    if port is None and host in LOCAL and LOCAL_SNMP_PORT:      # this container's own agent
        port = LOCAL_SNMP_PORT
    return {"device": device, "modules": modules, "host": host, "port": port, "families": families,
            "lab": lab, "load_by_hand": load_by_hand, "snmp": creds,
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
    creds = credentials(body)
    interval = float(body.get("interval") or 10)
    if not 5 <= interval <= 300:
        raise ValueError("interval must be 5 to 300 seconds")
    local = host in LOCAL and port is None
    if local and LOCAL_SNMP_PORT:
        port = LOCAL_SNMP_PORT
    return {"onboarded": name, "host": host, "port": port, "snmp": creds, "interval": interval,
            "explain": bool(body.get("explain")), "record": bool(body.get("record", True)),
            "alert": bool(body.get("alert", True)),
            "local": local}


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
        snmp = Snmp(opts["host"], port=opts["port"], **opts["snmp"])
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
        snmp = Snmp(opts["host"], port=opts["port"], **opts["snmp"])
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
        extra = {"max_attempts": SANDBOX_ATTEMPTS} if SANDBOX else {}
        out = agent.onboard(opts["device"], cands, dev, lab=lab, families=opts["families"],
                            effort=SANDBOX_EFFORT if SANDBOX else EFFORT, log=lambda m: None, on_event=run.emit,
                            verify_kw=verify_kw, modules=opts["modules"], **extra)
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
        ob = None
        if not SANDBOX:                      # the hosted demo keeps nothing on disk
            RESULTS.mkdir(exist_ok=True)
            ob = f"onboard_{time.strftime('%Y%m%d_%H%M%S')}"
            args = {"device": opts["device"], "modules": ",".join(opts["modules"]), "host": opts["host"],
                    "port": opts["port"], "lab": lab, "families": ",".join(opts["families"])}
            (RESULTS / f"{ob}.json").write_text(json.dumps({"args": args, **out}, indent=2, default=str))
        run.emit({"kind": "end", "onboarded": ob, "summary": {f: v["verdict"] for f, v in out["spec"].items()},
                  "cost_usd": out["cost_usd"], "seconds": out["seconds"], "snmp_calls": out["snmp_calls"],
                  "saved": saved})
    except Exception as e:  # noqa: BLE001 - reported to the page without internals
        run.emit({"kind": "error", "message": public_error(e)})
    finally:
        run.finished = True
        if SANDBOX:
            STATE["sandbox"]["last_end"] = time.time()
        with run.cond:
            run.cond.notify_all()


class Handler(BaseHTTPRequestHandler):
    server_version = "OnboardIQ"
    timeout = 30            # a client that stalls mid-request frees its thread instead of holding it

    def log_message(self, fmt, *args):   # quiet; never logs bodies
        pass

    def _host_ok(self):
        return host_allowed(self.headers.get("Host"), STATE["port"])

    def _security_headers(self, csp=True):
        if csp:
            self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        if SANDBOX:
            self.send_header("Strict-Transport-Security", "max-age=31536000")

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        extra = extra or {}
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers(csp="Content-Security-Policy" not in extra)
        self.send_header("Cache-Control", "no-store")
        for k, v in extra.items():
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
            out = {"live": True, "running": bool(run and not run.finished),
                   "mib_dir_found": STATE["mib_dir"].is_dir()}
            if SANDBOX:
                sb = STATE["sandbox"]
                out.update({"sandbox": True, "model": MODEL, "device": SANDBOX_DEVICE,
                            "modules": SANDBOX_MODULES, "cooldown_s": SANDBOX_COOLDOWN_S,
                            "runs_left": max(SANDBOX_MAX_RUNS - sb["runs"], 0),
                            "per_visitor": SANDBOX_PER_IP,
                            "paused": os.environ.get("OIQ_PAID", "on").lower() == "off"})
            return self._json(200, out)
        if url.path == "/api/modules":
            q = (parse_qs(url.query).get("q") or [""])[0].upper()[:40]
            names = module_names()
            return self._json(200, [n for n in names if q in n.upper()][:40])
        m = re.fullmatch(r"/api/outbox/(alert-\d{8}-\d{6}\.html)", url.path)
        if m and not SANDBOX:
            f = (STATE.get("outbox") or OUTBOX) / m.group(1)
            if not f.is_file():
                return self._send(404, b'{"error":"not found"}')
            # the email is escaped HTML; the CSP also forbids scripts and any outside loads
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8",
                              {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"})
        if url.path == "/api/onboarded":
            return self._json(200, [] if SANDBOX else onboarded_list())
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
        if run.viewers >= MAX_VIEWERS:
            return self._send(429, b'{"error":"too many viewers"}')
        run.viewers += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
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
        except OSError:                 # reader went away or stalled (reset, broken pipe, timeout)
            return
        finally:
            run.viewers -= 1

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, b'{"error":"bad host"}')
        origin = self.headers.get("Origin")
        if not origin_allowed(origin, self.headers.get("Host"), STATE["port"]):
            return self._send(403, b'{"error":"bad origin"}')
        if self.headers.get("X-OnboardIQ") != "1" or \
                not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._send(403, b'{"error":"missing header"}')
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, b'{"error":"bad length"}')
        if length < 0:
            return self._send(400, b'{"error":"bad length"}')
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
                if SANDBOX:
                    ip = client_ip(self.headers.get("X-Forwarded-For"), self.client_address[0])
                    blocked = sandbox_gate(STATE["sandbox"], budget_left=budget_left(), ip=ip)
                    if blocked:
                        return self._json(429, {"error": blocked[0], "retry_after": blocked[1]})
                    opts = sandbox_options()               # the request body is ignored on purpose
                    STATE["sandbox"]["runs"] += 1
                    ip_note(STATE["sandbox"].setdefault("ips", {}), ip, time.time())
                else:
                    try:
                        opts = validate(body)
                    except (ValueError, TypeError) as e:
                        return self._json(400, {"error": str(e)})
                run = Run(secrets.token_hex(6))
                STATE["run"] = run
            threading.Thread(target=execute, args=(run, opts), daemon=True).start()
            return self._json(202, {"id": run.id})
        if url == "/api/monitor":
            if SANDBOX:
                return self._json(404, {"error": "monitoring is not available in the hosted demo"})
            with STATE["lock"]:
                if STATE["run"] and not STATE["run"].finished:
                    return self._json(409, {"error": "a run is already in progress"})
                try:
                    opts = validate_monitor(body)
                except (ValueError, TypeError) as e:
                    return self._json(400, {"error": str(e)})
                run = Run(secrets.token_hex(6), kind="monitor")
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


def main(port: int = 8765, bind: str = "127.0.0.1"):
    """bind is 127.0.0.1 unless the process runs inside the project's container (OIQ_IN_CONTAINER=1), where the
    published port is mapped to the host's 127.0.0.1 only by docker-compose.yml."""
    if bind != "127.0.0.1" and not (bind == "0.0.0.0" and os.environ.get("OIQ_IN_CONTAINER") == "1"):
        raise SystemExit("refusing to listen on anything but 127.0.0.1 (0.0.0.0 only inside the project's container)")
    STATE["port"] = port
    srv = ThreadingHTTPServer((bind, port), Handler)
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
    ap.add_argument("--bind", default="127.0.0.1", help=argparse.SUPPRESS)
    ap.add_argument("--mib-dir", help="folder of converted MIB modules (default: $OIQ_MIB_DIR or ~/data/mibs_json)")
    a = ap.parse_args()
    if a.mib_dir:
        STATE["mib_dir"] = Path(a.mib_dir).expanduser()
    main(a.port, a.bind)
