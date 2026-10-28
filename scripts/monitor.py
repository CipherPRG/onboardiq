"""Monitor a device onboarded with OnboardIQ: poll only what was proven, score its health 0-100.

  python scripts/monitor.py results/onboard_<stamp>.json --host 192.0.2.10 --community public
  python scripts/monitor.py results/onboard_<stamp>.json --once --json      # one reading as JSON

Read-only (SNMP GET/WALK). Every poll rebuilds the verified profile from the device, scores it
(see onboardiq/health.py for the rules) and flags readings far from each row's own normal.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from collections import deque  # noqa: E402

from onboardiq import explain, health, mib, profile  # noqa: E402
from onboardiq.snmp import Snmp  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("onboarded", help="results/onboard_*.json written by scripts/onboard.py")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--community", default="public")
    ap.add_argument("--mib-dir", default=str(Path.home() / "data" / "mibs_json"))
    ap.add_argument("--interval", type=float, default=60)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--explain", action="store_true",
                    help="when the level changes or something unusual appears, explain it in plain English "
                         "(Muse Spark Open via OIQ_EXPLAIN_MODEL; checked against the evidence)")
    ap.add_argument("--record", help="append every reading as one JSON line (for the replay player)")
    ap.add_argument("--policy", help="health policy JSON (default: onboardiq/health_policy.json)")
    args = ap.parse_args()
    saved = json.loads(Path(args.onboarded).read_text())
    mods = saved.get("args", {}).get("modules") or ""
    mods = mods if isinstance(mods, list) else [m for m in mods.split(",") if m]
    if not mods:
        raise SystemExit("this onboarding record has no MIB modules; rerun scripts/onboard.py")
    cands = mib.candidates(Path(args.mib_dir).expanduser(), mods)
    snmp = Snmp(args.host, args.community, port=args.port)
    pol = health.load_policy(args.policy)
    base = health.Baseline()
    history, last_level, last_explained, explained = deque(maxlen=12), None, 0.0, 0
    while True:
        prof = profile.build(snmp, saved["spec"], cands, modules=mods)
        res = health.score(prof, pol)
        res["anomalies"] = base.scan(prof)
        res["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        history.append((res["at"][11:], res["score"]))
        # explain on a change worth explaining: the level moved or something unusual appeared (at most one
        # explanation a minute, at most 20 a run, so a noisy device cannot run up model calls)
        if args.explain and res["score"] is not None and explained < 20 and time.time() - last_explained >= 60 \
                and (res["level"] != last_level and last_level is not None or res["anomalies"]):
            ev = explain.evidence(res, res["anomalies"], prof.get("provenance"), history, res["at"][11:])
            res["explanation"] = explain.explain(ev)
            last_explained, explained = time.time(), explained + 1
        last_level = res["level"]
        if args.record:
            with open(args.record, "a") as fh:
                fh.write(json.dumps(res) + "\n")
        if args.json:
            print(json.dumps(res), flush=True)
        else:
            s = res["score"]
            print(f"{res['at']}  health {'n/a' if s is None else str(s) + '/100 ' + res['level']}"
                  f"  | {res.get('summary', res.get('note'))}  | {res['coverage']}", flush=True)
            for fam, f in res["families"].items():
                print(f"   {fam:12} {f['score']:>5.0f}/100  worst: {f['worst']}"
                      + ("  (some rows skipped)" if f["partial"] else ""), flush=True)
            for e in res["excluded"]:
                print(f"   not scored: {e['family']}{' ' + str(e['row']) if e.get('row') else ''}: {e['reason']}",
                      flush=True)
            for a in res["anomalies"]:
                print(f"   anomaly: {a}", flush=True)
            if res.get("explanation"):
                x = res["explanation"]
                print(f"   explanation [{x['source']}]: {x['text']}", flush=True)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
