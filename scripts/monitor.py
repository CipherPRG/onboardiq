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
from onboardiq.alerts import Alerter  # noqa: E402
from onboardiq.monitor import Monitor, load_onboarding  # noqa: E402
from onboardiq import snmp as snmp_mod  # noqa: E402
from onboardiq.snmp import Snmp  # noqa: E402


def show(res, as_json=False):
    if as_json:
        print(json.dumps(res), flush=True)
        return
    s = res["score"]
    print(f"{res['at']}  health {'n/a' if s is None else str(s) + '/100 ' + res['level']}"
          f"  | {res.get('summary', res.get('note'))}  | {res['coverage']}", flush=True)
    for e in res.get("events") or []:
        print(f"   EVENT: {e}", flush=True)
    for fam, f in res["families"].items():
        print(f"   {fam:12} {f['score']:>5.0f}/100  worst: {f['worst']}"
              + ("  (some rows skipped)" if f["partial"] else ""), flush=True)
    for e in res["excluded"]:
        print(f"   not scored: {e['family']}{' ' + str(e['row']) if e.get('row') else ''}: {e['reason']}", flush=True)
    for a in res["anomalies"]:
        print(f"   anomaly: {a}", flush=True)
    if res.get("explanation"):
        x = res["explanation"]
        print(f"   explanation [{x['source']}] {x.get('headline', '')}", flush=True)
        print(f"     {x['text']}", flush=True)
        if x.get("check"):
            print(f"     check: {x['check']}", flush=True)
    if res.get("alert"):
        a = res["alert"]
        print(f"   alert sent via {a['via']}: {a['subject']}" + (f" ({a['file']})" if a.get("file") else ""),
              flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("onboarded", nargs="?", help="results/onboard_*.json written by scripts/onboard.py")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--community", default="public")
    snmp_mod.add_cli_args(ap)
    ap.add_argument("--mib-dir", default=str(Path.home() / "data" / "mibs_json"))
    ap.add_argument("--interval", type=float, default=60)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--explain", action="store_true",
                    help="when the level changes or something unusual appears, explain it in plain English "
                         "(Muse Spark Open via OIQ_EXPLAIN_MODEL; checked against the evidence)")
    ap.add_argument("--alert", action="store_true",
                    help="email when the level changes (SMTP from OIQ_SMTP_* / OIQ_ALERT_TO), else write to outbox/")
    ap.add_argument("--record", help="append every reading as one JSON line (for the replay player)")
    ap.add_argument("--record-raw", help="save every raw SNMP answer of every poll (and the model's wording) so the "
                                         "run can be replayed and re-scored later with no device and no key")
    ap.add_argument("--replay-raw", help="recompute a --record-raw recording with the real scoring code: no device, no key")
    ap.add_argument("--policy", help="health policy JSON (default: onboardiq/health_policy.json)")
    args = ap.parse_args()
    if args.replay_raw:
        from onboardiq import replay
        results = replay.replay_health(args.replay_raw)
        print(f"REPLAY: {len(results)} polls recomputed from the raw SNMP answers in {args.replay_raw}\n")
        for res in results:
            show(res, args.json)
        return
    if not args.onboarded:
        raise SystemExit("give an onboarding record (results/onboard_*.json), or --replay-raw FILE")
    try:
        saved, mods = load_onboarding(args.onboarded)
    except ValueError as e:
        raise SystemExit(str(e))
    snmp = Snmp(args.host, port=args.port, **snmp_mod.cli_credentials(args))
    rawrec = None
    if args.record_raw:
        from onboardiq.replay import PollRecorder
        rawrec = snmp = PollRecorder(snmp)
    device = str(saved.get("device") or saved.get("args", {}).get("device") or "device")
    mon = Monitor(snmp, saved, mods, args.mib_dir, policy=args.policy, explain_on=args.explain,
                  alerter=Alerter(device) if args.alert else None)
    while True:
        if rawrec:
            rawrec.begin()
        res = mon.poll()
        if rawrec:
            rawrec.save(args.record_raw, device=device, spec=mon.spec, modules=mon.mods, cands=mon.cands,
                        policy=mon.pol, explain=args.explain, interval=args.interval)
        if args.record:
            with open(args.record, "a") as fh:
                fh.write(json.dumps(res) + "\n")
        show(res, args.json)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
