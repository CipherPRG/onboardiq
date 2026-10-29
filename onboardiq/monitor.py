"""Health monitoring of an onboarded device: poll only what was proven, score it, flag anomalies, explain.

Shared by scripts/monitor.py (terminal) and the local server's live panel, so both produce the same
readings. Read-only on the device (SNMP GET/WALK).
"""
import json
import re
import time
from collections import deque
from pathlib import Path

from . import explain, health, mib, profile

UPTIME = "1.3.6.1.2.1.1.3.0"        # sysUpTime, in hundredths of a second, a 32-bit TimeTicks that wraps at ~497 days
WRAP = 2 ** 32
_TICKS = re.compile(r"\((\d+)\)|^\s*(\d+)")


def uptime_ticks(snmp):
    """sysUpTime in ticks (1/100 s), or None when the device does not answer it."""
    try:
        vars_ = snmp.get(UPTIME)
    except Exception:  # noqa: BLE001 - a missing uptime must never stop a poll
        return None
    for v in vars_:
        if v.oid.lstrip(".") == UPTIME and v.type != "missing":
            m = _TICKS.search(v.value or "")
            if m:
                return int(m.group(1) or m.group(2))
    return None


def restarted(prev, now, elapsed_s):
    """True when sysUpTime shows the device restarted since the last poll. A drop is a restart, except
    the 32-bit wrap (uptime passing 497 days): then the new value is where the old one would have been
    after wrapping, give or take a minute."""
    if prev is None or now is None or now >= prev:
        return False
    expected = prev + elapsed_s * 100
    if expected >= WRAP and abs(now - (expected - WRAP)) <= 6000:
        return False
    return True


def load_onboarding(path):
    """(saved record, MIB module list) from a results/onboard_*.json file."""
    saved = json.loads(Path(path).read_text())
    mods = saved.get("args", {}).get("modules") or ""
    mods = mods if isinstance(mods, list) else [m for m in mods.split(",") if m]
    if not mods or not saved.get("spec"):
        raise ValueError("this onboarding record has no MIB modules or no verified picks; onboard the device again")
    return saved, mods


class Monitor:
    """One poll() = one reading: rebuild the verified profile from the device, score it, scan for anomalies,
    and (when asked) explain a change worth explaining: the level moved, or something unusual appeared while the device is not healthy.
    At most one explanation a minute and `max_explain` a run, so a noisy device cannot run up model calls."""

    def __init__(self, snmp, saved, mods, mib_dir, policy=None, explain_on=False, max_explain=20, alerter=None,
                 cands=None, clock=time.time):
        self.snmp, self.spec, self.mods, self.clock = snmp, saved["spec"], mods, clock
        self.cands = cands if cands is not None else mib.candidates(Path(mib_dir).expanduser(), mods)
        self.pol = health.load_policy(policy) if not isinstance(policy, dict) else policy
        self.base = health.Baseline()
        self.history = deque(maxlen=12)
        self.explain_on, self.max_explain, self.alerter = explain_on, max_explain, alerter
        self.last_level, self.last_explained, self.explained = None, 0.0, 0
        self.uptime, self.uptime_at = None, None

    def poll(self, explain_now=None):
        now = self.clock()
        ticks = uptime_ticks(self.snmp)
        events = []
        if restarted(self.uptime, ticks, now - self.uptime_at if self.uptime_at is not None else 0):
            events.append(f"device restarted: uptime fell from {self.uptime // 100} s to {ticks // 100} s; "
                          "what counts as normal for each row was reset")
            self.base = health.Baseline()      # readings from before a restart say nothing about the new normal
            self.history.clear()
        if ticks is not None:
            self.uptime, self.uptime_at = ticks, now
        prof = profile.build(self.snmp, self.spec, self.cands, modules=self.mods)
        res = health.score(prof, self.pol)
        res["anomalies"] = self.base.scan(prof)
        if events:
            res["events"] = events
        if ticks is not None:
            res["uptime_s"] = ticks // 100
        res["at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now))
        self.history.append((res["at"][11:], res["score"]))
        moved = res["level"] != self.last_level and self.last_level is not None
        if self.explain_on and res["score"] is not None and self.explained < self.max_explain \
                and now - self.last_explained >= 60 \
                and (moved or events or (res["anomalies"] and res["level"] != "ok")):
            ev = explain.evidence(res, res["anomalies"], prof.get("provenance"), self.history, res["at"][11:])
            if events:
                ev["events"] = events
            res["explanation"] = (explain_now or explain.explain)(ev)
            self.last_explained, self.explained = now, self.explained + 1
        self.last_level = res["level"]
        if self.alerter:
            sent = self.alerter.consider(res)    # an email (or outbox file) when the level changed
            if sent:
                res["alert"] = sent
        return res
