"""Record a live onboarding session and replay it without a device, an API key or waiting.

Recording wraps the SNMP client and keeps every response with the verifier step that asked
for it (static, observe, baseline, under load) and how long it took, plus the Reader's answers.
Replay serves the same responses to the same steps in order on a virtual clock, so the agent
sees the device exactly as it behaved live (including during load experiments) in seconds.
"""
import json
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
        Path(path).write_text(json.dumps({**meta, "snmp": self.log, "llm_cache": cache, "llm_order": order,
                                          "mem_total_kb": verify._meminfo_kb()}, indent=1))


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


def replay(session_path, log=print):
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
                            verify_kw={"load": lambda *x: _NoLoad()}, log=log)
        out["replay"] = {"virtual_seconds": round(clock.now, 1), "unused_answers": len(answers)}
        return out
    finally:
        llm.chat, verify.time, agent.time, verify._meminfo_kb = saved
