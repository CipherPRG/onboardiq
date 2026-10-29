"""Record a live onboarding session and replay it without a device, an API key or waiting.

Recording wraps the SNMP client and keeps every response with the verifier step that asked
for it (static, observe, baseline, under load) and how long it took, plus the Reader's answers.
Replay serves the same responses to the same steps in order on a virtual clock, so the agent
sees the device exactly as it behaved live (including during load experiments) in seconds.
"""
import json
import re
import time as _time
from pathlib import Path

from . import agent, llm, verify
from .snmp import Var


class Recorder:
    def __init__(self, snmp):
        self.snmp, self.t0, self.log = snmp, _time.time(), []

    @property
    def calls(self):
        return self.snmp.calls

    def _rec(self, op, args, fn):
        start = _time.time()
        out = fn()
        self.log.append({"t": round(start - self.t0, 3), "dt": round(_time.time() - start, 3),
                           "phase": verify.PHASE["name"], "op": op,
                           "args": list(args), "vars": [[v.oid, v.type, v.value] for v in out]})
        return out

    def walk(self, oid):
        return self._rec("walk", [oid], lambda: self.snmp.walk(oid))

    def get(self, *oids):
        return self._rec("get", oids, lambda: self.snmp.get(*oids))

    def save(self, path, **meta):
        """Model answers are stored under short ids (a0, a1, ...) in call order, not cache hashes,
        so a recording contains nothing that looks like a token or key."""
        ids, cache, order = {}, {}, []
        for key in llm.USED_KEYS:
            if key not in ids:
                f = llm.CACHE_DIR / "llm" / f"{key}.json"
                if not f.exists():
                    continue
                ids[key] = f"a{len(ids)}"
                cache[ids[key]] = json.loads(f.read_text())
            order.append(ids[key])
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        meta.setdefault("recorded", _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime(self.t0)))
        Path(path).write_text(json.dumps({**meta, "snmp": self.log, "llm_cache": cache, "llm_order": order,
                                          "mem_total_kb": verify._meminfo_kb()}, indent=1))


IDENTITY_OIDS = ("1.3.6.1.2.1.1.5.0", "1.3.6.1.2.1.1.4.0", "1.3.6.1.2.1.1.6.0")     # sysName, sysContact, sysLocation


SYSDESCR_HOST = re.compile(r"^(?:Linux|FreeBSD|OpenBSD|NetBSD|Darwin|SunOS)\s+(\S{3,})\s")


def scrub_raw(d, extra_names=()):
    """Remove who-owns-it details from a raw health recording, in place: the device's own name wherever it
    appears in any answer, and sysContact / sysLocation. The name comes from sysName when the recording read it,
    from sysDescr for agents that write the host name there ("Linux <host> <kernel> ..."), and from extra_names.
    Returns d."""
    names = {n.strip() for n in extra_names if len(n.strip()) >= 3}
    for p in d.get("polls", []):
        for c in p["calls"]:
            for v in c["vars"]:
                if v[0] == IDENTITY_OIDS[0] and len(v[2].strip()) >= 3:
                    names.add(v[2].strip())
                elif v[0] == "1.3.6.1.2.1.1.1.0":
                    m = SYSDESCR_HOST.match(v[2] or "")
                    if m:
                        names.add(m.group(1))
    for p in d.get("polls", []):
        for c in p["calls"]:
            for v in c["vars"]:
                if v[0] in IDENTITY_OIDS[1:]:
                    v[2] = ""
                    continue
                for n in names:
                    v[2] = v[2].replace(n, "device")
    return d


def anonymise_answer_ids(path):
    """Rewrite an older recording's cache-hash ids to a0, a1, ... (same answers, same order)."""
    d = json.loads(Path(path).read_text())
    ids = {}
    for k in d["llm_order"]:
        ids.setdefault(k, f"a{len(ids)}")
    d["llm_cache"] = {ids[k]: v for k, v in d["llm_cache"].items() if k in ids}
    d["llm_order"] = [ids[k] for k in d["llm_order"]]
    Path(path).write_text(json.dumps(d, indent=1))


class VirtualClock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += max(s, 0)


class Replayer:
    """Drop-in for Snmp: answers from a recorded session.

    Responses are served per (query, phase) in recorded order, where phase is the verifier
    step that asked (static, observe, baseline, under load). Control flow is deterministic,
    so the replay sees the same values in the same steps as the live run; if a step asks
    more often than it did live, the last recorded value for that step is repeated.
    """

    def __init__(self, session, clock):
        self.clock, self.calls, self.next = clock, 0, {}
        self.index = {}
        for c in session["snmp"]:
            self.index.setdefault((c["op"], tuple(c["args"]), c.get("phase", "static")), []).append(c)

    def _answer(self, op, args):
        self.calls += 1
        key = (op, tuple(args), verify.PHASE["name"])
        recs = self.index.get(key)
        if not recs and verify.PHASE["name"] == "profile":
            # Recordings made before the profile existed: the profile is a snapshot, so the same
            # query's recorded static answer is the device's real reply.
            recs = self.index.get((op, tuple(args), "static"))
        if not recs:
            return []                              # never asked live in this step: absent
        i = self.next.get(key, 0)
        self.next[key] = i + 1
        c = recs[min(i, len(recs) - 1)]
        self.clock.sleep(c.get("dt", 0))           # the call takes as long as it did live
        return [Var(*v) for v in c["vars"]]

    def walk(self, oid):
        return self._answer("walk", [oid])

    def get(self, *oids):
        return self._answer("get", list(oids))


class _NoLoad:
    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


def replay(session_path, log=print, on_event=None):
    """Re-run the recorded onboarding. Returns the agent output (same shape as live).

    The Reader's answers are served in recorded order (not by prompt), and the recording
    machine's RAM size is used for the memory experiment, so the replay is deterministic.
    """
    session = json.loads(Path(session_path).read_text())
    answers = [session["llm_cache"][k] for k in session["llm_order"]]
    clock = VirtualClock()

    def recorded_chat(*a, **k):
        if not answers:
            raise RuntimeError("replay diverged from the recording: more model calls than recorded")
        return {**answers.pop(0), "cached": True}

    saved = (llm.chat, verify.time, agent.time, verify._meminfo_kb)
    llm.chat, verify.time, agent.time = recorded_chat, clock, clock
    verify._meminfo_kb = lambda key="MemTotal": session.get("mem_total_kb")
    try:
        a = session["args"]
        out = agent.onboard(a["device"], session["cands"], Replayer(session, clock), lab=a["lab"],
                            families=a["families"].split(","), max_attempts=a["max_attempts"],
                            model=a.get("model"), effort=a["effort"],
                            verify_kw={"load": lambda *x: _NoLoad()}, log=log, on_event=on_event)
        out["replay"] = {"virtual_seconds": round(clock.now, 1), "unused_answers": len(answers)}
        return out
    finally:
        llm.chat, verify.time, agent.time, verify._meminfo_kb = saved


# ---- health monitoring: raw readings in, the same scoring code out ---------------------------------------------

class PollRecorder:
    """Wraps the SNMP client during monitoring and keeps every raw answer, grouped by poll. The score, the
    levels and the anomalies are NOT stored: replay recomputes them from these answers with the real code."""

    def __init__(self, snmp, clock=_time.time):
        self.snmp, self.clock, self.t0, self.polls, self.cur = snmp, clock, clock(), [], None
        self.llm_start = len(llm.USED_KEYS)

    @property
    def calls(self):
        return self.snmp.calls

    def begin(self):
        self.cur = {"t": round(self.clock() - self.t0, 3), "calls": []}
        self.polls.append(self.cur)

    def _rec(self, op, args, fn):
        out = fn()
        if self.cur is not None:
            self.cur["calls"].append({"op": op, "args": list(args), "vars": [[v.oid, v.type, v.value] for v in out]})
        return out

    def walk(self, oid):
        return self._rec("walk", [oid], lambda: self.snmp.walk(oid))

    def get(self, *oids):
        return self._rec("get", oids, lambda: self.snmp.get(*oids))

    def save(self, path, **meta):
        """meta: device, spec, modules, cands, policy (dict), explain. Model answers are stored under short ids
        in call order, as in onboarding recordings."""
        ids, cache, order = {}, {}, []
        for key in llm.USED_KEYS[self.llm_start:]:
            if key not in ids:
                f = llm.CACHE_DIR / "llm" / f"{key}.json"
                if not f.exists():
                    continue
                ids[key] = f"a{len(ids)}"
                cache[ids[key]] = json.loads(f.read_text())
            order.append(ids[key])
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(scrub_raw(
            {"kind": "health_raw", **meta, "epoch": round(self.t0, 3), "polls": self.polls,
             "llm_cache": cache, "llm_order": order}), separators=(",", ":")))


class PollReplayer:
    """Drop-in for Snmp during one recorded poll: answers each query from that poll's recorded answers, in order."""

    def __init__(self, polls):
        self.polls, self.calls, self.q = polls, 0, {}

    def begin(self, i):
        self.q = {}
        for c in self.polls[i]["calls"]:
            self.q.setdefault((c["op"], tuple(c["args"])), []).append(c["vars"])

    def _answer(self, op, args):
        self.calls += 1
        recs = self.q.get((op, tuple(args)))
        if not recs:
            return []                       # not asked live in this poll: absent
        return [Var(*v) for v in (recs.pop(0) if len(recs) > 1 else recs[0])]

    def walk(self, oid):
        return self._answer("walk", [oid])

    def get(self, *oids):
        return self._answer("get", list(oids))


def replay_health(path):
    """Recompute every poll of a raw health recording: [result dicts]. No device, no key. The scoring, anomaly
    detection, restart detection and the check on the explanation all run on the recorded raw answers; only the
    model's wording is served from the recording (and is checked again against the recomputed readings)."""
    from . import monitor
    d = json.loads(Path(path).read_text())
    answers = [d["llm_cache"][k] for k in d["llm_order"]]
    clock = VirtualClock()

    def recorded_chat(*a, **k):
        if not answers:
            raise RuntimeError("health replay diverged from the recording: more model calls than recorded")
        return {**answers.pop(0), "cached": True}

    saved = llm.chat
    llm.chat = recorded_chat
    try:
        snmp = PollReplayer(d["polls"])
        mon = monitor.Monitor(snmp, {"spec": d["spec"]}, d["modules"], None, policy=d["policy"],
                              explain_on=d.get("explain", False), cands=d["cands"], clock=clock.time)
        out = []
        for i, p in enumerate(d["polls"]):
            snmp.begin(i)
            clock.now = d["epoch"] + p["t"]
            res = mon.poll()
            res["device"] = d.get("device")
            out.append(res)
        return out
    finally:
        llm.chat = saved
