"""Onboard a device: read its MIB, verify every pick live, re-read on failure.

  python scripts/onboard.py --device "Linux server running the net-snmp agent" \
      --modules HOST-RESOURCES-MIB,UCD-SNMP-MIB --lab --record demo/laptop.json
  # judge mode: replay a recorded session (no device, no API key, a few seconds)
  python scripts/onboard.py --replay demo/laptop.json

Lab mode (load experiments) only runs against this machine's own agent.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from onboardiq import agent, mib, replay  # noqa: E402
from onboardiq.snmp import Snmp  # noqa: E402

LOCAL = {"localhost", "127.0.0.1", "::1"}
LABEL = {"proven-experiment": "PROVEN by experiment", "proven-static": "PROVEN (static checks)",
         "unproven": "UNPROVEN", "not-supported": "NOT SUPPORTED on this device", "rejected": "REJECTED"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", help="what the device is, e.g. 'Arista EOS switch'")
    ap.add_argument("--modules", help="comma-separated MIB modules (converted JSON)")
    ap.add_argument("--record", help="save the session (SNMP responses + model answers) for replay")
    ap.add_argument("--replay", help="replay a recorded session: no device or API key needed")
    ap.add_argument("--mib-dir", default=str(Path.home() / "data" / "mibs_json"))
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--community", default="public")
    ap.add_argument("--lab", action="store_true", help="CPU/memory load experiments (local agent only)")
    ap.add_argument("--families", default="cpu,memory,temperature,fan")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-attempts", type=int, default=3)
    args = ap.parse_args()
    log = lambda m: print(m, flush=True)  # noqa: E731
    if args.replay:
        print(f"REPLAY of a recorded live session: {args.replay}\n")
        out = replay.replay(args.replay, log=log)
    else:
        if not (args.device and args.modules):
            raise SystemExit("--device and --modules are required (or use --replay)")
        if args.lab and args.host not in LOCAL:
            raise SystemExit("lab experiments only run against this machine's own agent (--host localhost)")
        cands = mib.candidates(Path(args.mib_dir).expanduser(), args.modules.split(","))
        snmp = Snmp(args.host, args.community, port=args.port)
        dev = replay.Recorder(snmp) if args.record else snmp
        out = agent.onboard(args.device, cands, dev, lab=args.lab, families=args.families.split(","),
                            max_attempts=args.max_attempts, model=args.model, effort=args.effort, log=log)
        if args.record:
            dev.save(args.record, args={k: v for k, v in vars(args).items() if k not in ("record", "replay")},
                     cands=cands, result={f: v["verdict"] for f, v in out["spec"].items()})
            print(f"  recorded session -> {args.record}")
    print("\n=== Verified spec")
    for fam, f in out["spec"].items():
        p = f["pick"]
        print(f"  {fam:12} {LABEL[f['verdict']]:30} {p.get('object_name')} x{p.get('scale_to_canonical')}"
              f"  ({f['attempts']} attempt{'s' if f['attempts'] != 1 else ''})")
    print(f"  {out['seconds']}s, {out['snmp_calls']} SNMP calls, model cost ${out['cost_usd']:.4f}")
    res = ROOT / "results" / f"onboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    res.parent.mkdir(exist_ok=True)
    res.write_text(json.dumps({"args": vars(args), **out}, indent=2))
    print(f"  saved {res.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
