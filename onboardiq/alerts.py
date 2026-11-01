"""Health alert emails: one when a device's level changes, one when it recovers.

Sent over SMTP when configured, otherwise written to outbox/ as a viewable .html file (so a judge or anyone
without a mail account sees the real email). Settings come from the environment only, never from a file in
the repo and never from the web page:

    OIQ_SMTP_HOST   smtp.gmail.com            OIQ_ALERT_TO    you@example.com (comma-separated for several)
    OIQ_SMTP_PORT   587 (STARTTLS)            OIQ_ALERT_FROM  defaults to OIQ_SMTP_USER
    OIQ_SMTP_USER / OIQ_SMTP_PASS  (for Gmail: an app password you create yourself)

Rules: send when the level gets worse and when it returns to healthy, always; an improvement that is not yet
healthy (critical to warning) waits for a cooldown (default 5 minutes) so a flapping device cannot fill an inbox.
The email carries the score, the per-family table, the explanation card and what to check next. Every value is
HTML-escaped and headers are built with email.message, which refuses line breaks.
"""
import html
import os
import re
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

from .config import ROOT

OUTBOX = ROOT / "outbox"
SEVERITY = {"ok": 0, "warning": 1, "critical": 2}
WORD = {"ok": "Healthy", "warning": "Under strain", "critical": "In trouble"}      # the words the console uses
COLOR = {"ok": "#0E7A6B", "warning": "#A86A10", "critical": "#B8202E"}
ADDR = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")
FAMILY = {"cpu": "CPU", "cpu_load": "CPU load", "memory": "Memory", "disk": "Disk",
          "temperature": "Temperature", "fan": "Fan"}


def parse_recipients(text, limit=3):
    """The addresses in a comma-separated string. Raises ValueError for a bad address or more than `limit`."""
    out = [a for a in re.split(r"[,;]\s*", str(text or "").strip()) if a]
    if len(out) > limit:
        raise ValueError(f"at most {limit} email addresses")
    if not all(ADDR.fullmatch(a) and len(a) <= 254 for a in out):
        raise ValueError("that does not look like an email address")
    return out


def mail_configured(env=None):
    """True when this server has a mail account to send from (the recipient can then come from the monitor dialog)."""
    e = dict(env if env is not None else os.environ)
    e["OIQ_ALERT_TO"] = e.get("OIQ_ALERT_TO") or "someone@example.com"
    return smtp_settings(e) is not None


def smtp_settings(env=None, to=None):
    """The SMTP settings from the environment, or None when email is not configured. `to` replaces the
    recipients from OIQ_ALERT_TO (the account and its password still come only from the environment)."""
    raw = env if env is not None else os.environ
    e = {k: str(raw.get(k) or "").strip().strip('"').strip("'") for k in               # a stray space or quote in .env
         ("OIQ_SMTP_HOST", "OIQ_SMTP_PORT", "OIQ_SMTP_USER", "OIQ_SMTP_PASS", "OIQ_ALERT_FROM", "OIQ_ALERT_TO")}
    host = e.get("OIQ_SMTP_HOST")
    to = list(to) if to else [a for a in re.split(r"[,;]\s*", e.get("OIQ_ALERT_TO", "")) if a]
    if not host or not to or not all(ADDR.fullmatch(a) for a in to):
        return None
    user = e.get("OIQ_SMTP_USER", "")
    sender = e.get("OIQ_ALERT_FROM") or user
    if not sender or not ADDR.fullmatch(sender):
        return None
    try:
        port = int(e.get("OIQ_SMTP_PORT") or 587)
    except ValueError:
        return None
    return {"host": host, "port": port, "user": user, "password": e.get("OIQ_SMTP_PASS", ""), "from": sender, "to": to}


def subject_for(device, res, recovered=False):
    s = res.get("score")
    card = res.get("explanation") or {}
    if recovered:
        return f"[OnboardIQ] {device}: recovered, health {s}/100"
    head = card.get("headline") or (res.get("summary") or "")
    return f"[OnboardIQ] {device}: {WORD.get(res.get('level'), res.get('level'))}, health {s}/100" + (f" ({head})" if head else "")


def _rows(res):
    out = []
    for fam, f in (res.get("families") or {}).items():
        out.append((FAMILY.get(fam, fam), f["score"], f["worst"],
                    next((r["why"] for r in f["rows"] if r["name"] == f["worst"]), "")))
    return sorted(out, key=lambda r: r[1])


def render(device, res, recovered=False, previous=None):
    """(subject, plain text, html) for one alert."""
    card = res.get("explanation") or {}
    level, score = res.get("level"), res.get("score")
    when = (res.get("at") or "").replace("T", " ")
    subject = subject_for(device, res, recovered)
    head = card.get("headline") or ("Recovered" if recovered else res.get("summary") or "Health changed")
    what = card.get("what_happened") or ""
    why = card.get("why") or ""
    check = card.get("check") or ""
    src = card.get("source") or ""
    foot = (f"Written by {src}; every number is checked against the readings." if src and src != "template"
            else "Written from the measured readings (template).")
    rows = _rows(res)
    moved = (f"{WORD.get(previous, previous)} → {WORD.get(level, level)}" if previous and previous != level
             else WORD.get(level, str(level)))

    text = [subject, "", f"{head}", f"Health {score}/100 ({moved}) at {when} UTC", ""]
    for line in (what, why):
        if line:
            text.append(line)
    if check:
        text += ["", f"What to check: {check}"]
    text += ["", "Per metric:"] + [f"  {n:<12} {sc:>5.0f}/100  {w}: {why_}" for n, sc, w, why_ in rows]
    for a in (res.get("anomalies") or [])[:5]:
        text.append(f"  unusual: {a}")
    text += ["", foot]

    esc = html.escape
    color = COLOR.get(level, "#1B2A3A")
    trs = "".join(f"<tr><td>{esc(n)}</td><td style='text-align:right'>{sc:.0f}</td><td>{esc(str(w))}</td>"
                  f"<td style='color:#5A6B7B'>{esc(str(why_))}</td></tr>" for n, sc, w, why_ in rows)
    an = "".join(f"<li>{esc(a)}</li>" for a in (res.get("anomalies") or [])[:5])
    page = (
        "<!doctype html><html><head><meta charset='utf-8'><title>" + esc(subject) + "</title></head>"
        "<body style='margin:0;background:#EDF1F4;font:15px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#1B2A3A'>"
        "<div style='max-width:620px;margin:0 auto;padding:24px'>"
        "<div style='background:#fff;border:1px solid #C9D3DC;border-top:6px solid " + color + ";border-radius:4px;padding:22px'>"
        "<div style='color:#5A6B7B;font-size:13px'>OnboardIQ &middot; " + esc(device) + " &middot; " + esc(when) + " UTC</div>"
        "<div style='font-size:44px;font-weight:600;color:" + color + ";margin:8px 0 0'>" + esc(str(score)) +
        "<span style='font-size:18px;color:#5A6B7B;font-weight:400'>/100</span></div>"
        "<div style='font-weight:600;color:" + color + "'>" + esc(moved) + "</div>"
        "<h2 style='font-size:19px;margin:14px 0 6px'>" + esc(head) + "</h2>"
        + (f"<p style='margin:0 0 6px'>{esc(what)}</p>" if what else "")
        + (f"<p style='margin:0 0 6px'>{esc(why)}</p>" if why else "")
        + (f"<p style='margin:14px 0 0;padding:10px 12px;background:#F3F6F8;border-left:3px solid {color}'>"
           f"<b>What to check:</b> {esc(check)}</p>" if check else "")
        + "<table style='width:100%;border-collapse:collapse;margin-top:16px;font-size:14px'>"
          "<tr style='text-align:left;color:#5A6B7B'><th>Metric</th><th style='text-align:right'>Score</th>"
          "<th>Worst row</th><th>Reading</th></tr>" + trs + "</table>"
        + (f"<ul style='color:#A86A10;font-size:14px'>{an}</ul>" if an else "")
        + f"<p style='color:#5A6B7B;font-size:12px;margin:16px 0 0'>{esc(foot)}</p>"
        "</div></div></body></html>")
    return subject, "\n".join(text), page


def smtp_reason(e):
    """A short, password-free reason for a failed send, with what usually fixes it."""
    name = type(e).__name__
    hint = {"SMTPAuthenticationError": "the mail server refused the login: for Gmail use an app password",
            "SMTPServerDisconnected": "the mail server hung up: check OIQ_SMTP_PORT (587, or 465) and OIQ_SMTP_HOST",
            "gaierror": "the mail server name was not found: check OIQ_SMTP_HOST",
            "TimeoutError": "no answer from the mail server: the network may block outgoing mail",
            "ConnectionRefusedError": "the mail server refused the connection: check the port"}.get(name)
    return f"{name}: {hint}" if hint else name


RETRY_S = 3.0


def _send(cfg, subject, text, page):
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, cfg["from"], ", ".join(cfg["to"])
    msg.set_content(text)
    msg.add_alternative(page, subtype="html")
    if cfg["port"] == 465:                      # implicit TLS (465), not STARTTLS (587)
        conn = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30)
    else:
        conn = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
    with conn as s:
        if cfg["port"] != 465:
            s.starttls()
        if cfg["user"]:
            s.login(cfg["user"], cfg["password"])
        s.send_message(msg)


def deliver(subject, text, page, settings=None, outbox=None, now=None):
    """Send by SMTP when configured, else write to outbox/. Returns {"via", "file"?, "error"?}. Never raises:
    an SMTP failure falls back to the outbox so the alert is not lost."""
    cfg = settings if settings is not None else smtp_settings()
    err = None
    for attempt in range(3 if cfg else 0):       # alerts fire when the machine is busiest: retry a slow connection
        try:
            _send(cfg, subject, text, page)
            return {"via": "smtp", "to": len(cfg["to"]), **({"attempts": attempt + 1} if attempt else {})}
        except smtplib.SMTPAuthenticationError as e:     # wrong login: retrying will not help
            err = smtp_reason(e)
            break
        except Exception as e:  # noqa: BLE001 - fall back to the outbox after the last try
            err = smtp_reason(e)
            if attempt < 2:
                time.sleep(RETRY_S * (attempt + 1))
    out = Path(outbox or OUTBOX)
    out.mkdir(parents=True, exist_ok=True)
    name = time.strftime("alert-%Y%m%d-%H%M%S", time.gmtime(now or time.time())) + ".html"
    (out / name).write_text(page)
    (out / name.replace(".html", ".txt")).write_text(text)
    return {"via": "outbox", "file": name, **({"smtp_error": err} if err else {})}


class Alerter:
    """Decides when a reading is worth an email: the level changed (worse, or back to ok), outside the cooldown."""

    def __init__(self, device, cooldown=300.0, settings=None, outbox=None, clock=time.time):
        self.device, self.cooldown, self.settings, self.outbox, self.clock = device, cooldown, settings, outbox, clock
        self.level, self.last_at, self.last_level = None, None, None

    def consider(self, res):
        """None, or {"subject", "via", ...} after sending one alert."""
        level = res.get("level")
        if level is None or res.get("score") is None:
            return None
        prev, self.level = self.level, level
        if prev is None or prev == level:
            return None
        worse = SEVERITY[level] > SEVERITY.get(prev, 0)
        now = self.clock()
        recovered = level == "ok"
        # worse: always sent; back to healthy: always sent (the all-clear); in between: not within the cooldown
        if not worse and not recovered and self.last_at is not None and now - self.last_at < self.cooldown:
            return None
        subject, text, page = render(self.device, res, recovered=recovered, previous=prev)
        out = deliver(subject, text, page, settings=self.settings, outbox=self.outbox, now=now)
        self.last_at, self.last_level = now, level
        return {"subject": subject, "previous": prev, "level": level, **out}
