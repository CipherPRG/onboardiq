"""The onboarding agent: read the MIB, verify each pick on the device, re-read on failure.

    read MIB -> pick per family -> verify on device
                    ^                    |
                    +-- evidence --------+   (rejected: up to max_attempts)

Every step is recorded in a trace (for the dashboard, the video and the build log).
"""
import time

from . import reader
from .verify import Item, verify_item

NOT_SERVED = "is not served by this device"


def _verify(snmp, family, pick, lab, verify_kw):
    item = Item.from_pick(family, pick)
    res = verify_item(snmp, item, lab=lab, **verify_kw)
    return {"pick": pick, "verdict": res.verdict, "evidence": res.evidence,
            "suggested_scale": res.suggested_scale, "seconds": res.seconds, "series": res.series}


def onboard(device, cands, snmp, lab=False, families=reader.FAMILIES, max_attempts=3, model=None,
            effort="high", initial=None, verify_kw=None, log=print):
    """Returns {"spec": {family: final}, "trace": [...], "cost_usd": float}.

    initial: an existing spec to start from (e.g. a cached Reader answer); otherwise the Reader runs.
    Final verdict per family: proven-experiment | proven-static | unproven | not-supported | rejected.
    """
    verify_kw = verify_kw or {}
    trace, cost, t0 = [], 0.0, time.time()

    def note(kind, family=None, **kw):
        trace.append({"t": round(time.time() - t0, 1), "kind": kind, "family": family, **kw})

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
            a = _verify(snmp, fam, pick, lab, verify_kw)
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
            pick, why, r = reader.repick(device, cands, fam, attempts, model=model, effort=effort)
            cost += 0 if r.get("cached") else r["cost_usd"]
            log(f"    re-read: {why[:200]}")
            note("repick", fam, attempt=n + 1, object=pick.get("object_name"), reasoning=why,
                 cost_usd=r["cost_usd"])
        last = attempts[-1] if attempts else None
        if last is None:
            verdict = "unproven"
        elif last["verdict"] == "rejected" and all(
                any(NOT_SERVED in e["detail"] for e in a["evidence"]) for a in attempts):
            verdict = "not-supported"   # every candidate is absent on this device: not a reading error
        else:
            verdict = last["verdict"]
        final[fam] = {"verdict": verdict, "attempts": len(attempts),
                      "pick": last["pick"] if last else pick, "evidence": last["evidence"] if last else []}
        note("final", fam, verdict=verdict, attempts=len(attempts))
    return {"device": device, "spec": final, "trace": trace, "cost_usd": round(cost, 4),
            "seconds": round(time.time() - t0, 1), "snmp_calls": getattr(snmp, "calls", None)}
