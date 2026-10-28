"""The onboarding agent: read the MIB, verify each pick on the device, re-read on failure.

    read MIB -> pick per family -> verify on device
                    ^                    |
                    +-- evidence --------+   (rejected: up to max_attempts)

Every step is recorded in a trace (for the dashboard, the video and the build log).
"""
import time

from . import profile as profile_mod
from . import reader
from .verify import Item, verify_item

NOT_SERVED = "is not served by this device"


def _verify(snmp, family, pick, lab, verify_kw, types=None):
    item = Item.from_pick(family, pick)
    item.mib_type = (types or {}).get(item.oid)
    res = verify_item(snmp, item, lab=lab, **verify_kw)
    return {"pick": pick, "verdict": res.verdict, "evidence": res.evidence,
            "suggested_scale": res.suggested_scale, "seconds": res.seconds, "series": res.series}


def trigger(evidence):
    """What made the verifier reject a pick: absent | type | range | experiment | witness | ..."""
    bad = [e for e in evidence if e["ok"] is False]
    if not bad:
        return None
    return "absent" if NOT_SERVED in bad[-1]["detail"] else bad[-1]["check"]


def onboard(device, cands, snmp, lab=False, families=reader.FAMILIES, max_attempts=3, model=None,
            effort="high", initial=None, verify_kw=None, log=print, on_event=None, profile=True,
            modules=None):
    """Returns {"spec": {family: final}, "trace": [...], "cost_usd": float, "profile": {...}}.

    initial: an existing spec to start from (e.g. a cached Reader answer); otherwise the Reader runs.
    Final verdict per family: proven-experiment | proven-static | unproven | not-supported | rejected.
    profile: also build the verified device profile (kinds, row names, status meanings, validity
    filter, provenance; see profile.py).
    """
    verify_kw = verify_kw or {}
    trace, cost, t0 = [], 0.0, time.time()

    current = {"family": None, "attempt": None}

    def note(kind, family=None, **kw):
        entry = {"t": round(time.time() - t0, 1), "kind": kind, "family": family, **kw}
        trace.append(entry)
        if on_event:
            on_event(entry)

    def sample(ev):   # live measurement samples from the verifier (not kept in the trace)
        if on_event:
            on_event({"t": round(time.time() - t0, 2), "family": current["family"],
                      "attempt": current["attempt"], **ev})

    types = {c["oid"]: c.get("type") for c in cands}   # declared SYNTAX per object, from the MIB
    from . import verify as _v
    saved_hook, _v.EVENTS["hook"] = _v.EVENTS["hook"], sample

    try:
        if initial is None:
            log(f"Reading {len(cands)} MIB objects for: {device}")
            spec, r = reader.read(device, cands, True, model, effort, agent=True)
            cost += 0 if r.get("cached") else r["cost_usd"]
            note("read", objects=len(cands), cost_usd=r["cost_usd"], cached=r.get("cached", False))
        else:
            spec = initial
            note("read", objects=len(cands), from_cache=True)

        final = {}
        for fam in families:
            pick = spec.get(fam) or {}
            attempts = []
            for n in range(1, max_attempts + 1):
                if not pick.get("oid"):
                    log(f"  {fam}: no object chosen")
                    note("no-pick", fam, attempt=n, reasoning=pick.get("evidence"))
                    break
                log(f"  {fam} attempt {n}: {pick.get('object_name')} x{pick.get('scale_to_canonical')}")
                current.update(family=fam, attempt=n)
                note("verify-start", fam, attempt=n, object=pick.get("object_name"), oid=pick.get("oid"), pick=pick)
                a = _verify(snmp, fam, pick, lab, verify_kw, types)
                attempts.append(a)
                bad = [e for e in a["evidence"] if e["ok"] is False]
                log(f"    -> {a['verdict']}" + (f": {bad[-1]['detail']}" if bad else ""))
                note("verify", fam, attempt=n, object=pick.get("object_name"), oid=pick.get("oid"),
                     scale=pick.get("scale_to_canonical"), verdict=a["verdict"], evidence=a["evidence"],
                     seconds=a["seconds"], pick=pick, series=a["series"])
                if a["verdict"] != "rejected":
                    break
                if n == max_attempts:
                    break
                why_trigger = trigger(a["evidence"])
                pick, why, r = reader.repick(device, cands, fam, attempts, model=model, effort=effort)
                cost += 0 if r.get("cached") else r["cost_usd"]
                log(f"    re-read ({why_trigger}): {why[:200]}")
                note("repick", fam, attempt=n + 1, object=pick.get("object_name"), reasoning=why,
                     cost_usd=r["cost_usd"], trigger=why_trigger)
            last = attempts[-1] if attempts else None
            keep = last
            if last is None:
                verdict = "unproven"
            elif last["verdict"] == "rejected" and all(
                    any(NOT_SERVED in e["detail"] for e in a["evidence"]) for a in attempts):
                # Every candidate is absent on this device: not a reading error. Absence says nothing
                # about which object the MIB means, so report the Reader's first, best-reasoned answer
                # rather than the last re-read (which was only a search for something served).
                verdict = "not-supported"
                keep = attempts[0]
            else:
                verdict = last["verdict"]
            final[fam] = {"verdict": verdict, "attempts": len(attempts),
                          "pick": keep["pick"] if keep else pick, "evidence": keep["evidence"] if keep else [],
                          "triggers": [trigger(a["evidence"]) for a in attempts[:-1]]}
            note("final", fam, verdict=verdict, attempts=len(attempts))
        prof = None
        if profile:
            try:
                prof = profile_mod.build(snmp, final, cands, modules=modules, lab=lab,
                                         load=verify_kw.get("load"))
                note("profile", summary={f: {"kind": m.get("kind"), "rows": len(m.get("rows") or []),
                                             "partial": m.get("partial")} for f, m in prof["metrics"].items()})
            except Exception as e:  # noqa: BLE001 - the profile must never lose a finished onboarding
                prof = {"error": f"{type(e).__name__}: {e}"}
                log(f"  profile failed: {prof['error']}")
    finally:
        _v.EVENTS["hook"] = saved_hook
    note("done", summary={f: v["verdict"] for f, v in final.items()})
    return {"device": device, "spec": final, "trace": trace, "cost_usd": round(cost, 4),
            "seconds": round(time.time() - t0, 1), "snmp_calls": getattr(snmp, "calls", None),
            "profile": prof}
