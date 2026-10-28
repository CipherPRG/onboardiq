"""Health monitoring of an onboarded device: poll only what was proven, score it, flag anomalies, explain.

Shared by scripts/monitor.py (terminal) and the local server's live panel, so both produce the same
readings. Read-only on the device (SNMP GET/WALK).
"""
import json
import time
from collections import deque
from pathlib import Path

from . import explain, health, mib, profile


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
    and (when asked) explain a change worth explaining: the level moved or something unusual appeared.
    At most one explanation a minute and `max_explain` a run, so a noisy device cannot run up model calls."""

    def __init__(self, snmp, saved, mods, mib_dir, policy=None, explain_on=False, max_explain=20):
        self.snmp, self.spec, self.mods = snmp, saved["spec"], mods
        self.cands = mib.candidates(Path(mib_dir).expanduser(), mods)
        self.pol = health.load_policy(policy) if not isinstance(policy, dict) else policy
        self.base = health.Baseline()
        self.history = deque(maxlen=12)
        self.explain_on, self.max_explain = explain_on, max_explain
        self.last_level, self.last_explained, self.explained = None, 0.0, 0

    def poll(self, explain_now=None):
        prof = profile.build(self.snmp, self.spec, self.cands, modules=self.mods)
        res = health.score(prof, self.pol)
        res["anomalies"] = self.base.scan(prof)
        res["at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        self.history.append((res["at"][11:], res["score"]))
        moved = res["level"] != self.last_level and self.last_level is not None
        if self.explain_on and res["score"] is not None and self.explained < self.max_explain \
                and time.time() - self.last_explained >= 60 and (moved or res["anomalies"]):
            ev = explain.evidence(res, res["anomalies"], prof.get("provenance"), self.history, res["at"][11:])
            res["explanation"] = (explain_now or explain.explain)(ev)
            self.last_explained, self.explained = time.time(), self.explained + 1
        self.last_level = res["level"]
        return res
