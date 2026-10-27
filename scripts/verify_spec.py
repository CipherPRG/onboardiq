"""Verify a monitoring spec on a device.

The spec is the Reader's output: {"cpu": pick, "memory": pick, "temperature": pick, "fan": pick},
where each pick has oid, object_name, value_type, scale_to_canonical, canonical_unit, row_filter.

  # verify the Reader's picks for one device from an accuracy-test results file
  python scripts/verify_spec.py --results results/day1_X.json --vendor linux --mode with-mib --lab
  # or a spec file
  python scripts/verify_spec.py --spec spec.json --host 10.0.0.5 --community public

Lab mode (CPU and memory load experiments) only runs against this machine's own agent.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from onboardiq.snmp import Snmp  # noqa: E402
from onboardiq.verify import Item, verify_item  # noqa: E402

FAMILIES = ["cpu", "memory", "temperature", "fan"]
LOCAL = {"localhost", "127.0.0.1", "::1"}
ICON = {"proven-experiment": "PROVEN (experiment)", "proven-static": "PROVEN (static)",
        "unproven": "UNPROVEN", "rejected": "REJECTED"}


def load_spec(args):
    if args.spec:
        return json.loads(Path(args.spec).read_text())
    for r in json.loads(Path(args.results).read_text()):
        if r["vendor"] == args.vendor and r["mode"] == args.mode:
            return r["prediction"]
    raise SystemExit(f"no {args.vendor}/{args.mode} prediction in {args.results}")


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--spec")
    src.add_argument("--results")
    ap.add_argument("--vendor", default="linux")
    ap.add_argument("--mode", default="with-mib")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--community", default="public")
    ap.add_argument("--lab", action="store_true", help="run CPU/memory load experiments (local agent only)")
    ap.add_argument("--families", default=",".join(FAMILIES))
    args = ap.parse_args()
    if args.lab and (args.host not in LOCAL or args.port not in (None, 161)):
        raise SystemExit("lab experiments only run against this machine's own agent (--host localhost, default port);\n"
                         "a forwarded port is another device (VM/container): loading this machine proves nothing about it")

    spec = load_spec(args)
    snmp = Snmp(args.host, args.community, port=args.port)
    results = []
    for fam in args.families.split(","):
        pick = spec.get(fam)
        if not pick or not pick.get("oid"):
            print(f"\n{fam:12} (no object chosen)")
            continue
        item = Item.from_pick(fam, pick)
        print(f"\n{fam:12} {item.name} {item.oid} x{item.scale} {item.canonical_unit}", flush=True)
        res = verify_item(snmp, item, lab=args.lab)
        print(f"  -> {ICON[res.verdict]}  ({res.seconds}s)"
              + (f"  suggested scale {res.suggested_scale:g}" if res.suggested_scale else ""))
        for e in res.evidence:
            print(f"     {'ok ' if e['ok'] else ('BAD' if e['ok'] is False else ' ? ')} {e['check']:10} {e['detail']}")
        results.append({"family": fam, "item": vars(item), "verdict": res.verdict, "evidence": res.evidence,
                        "suggested_scale": res.suggested_scale, "seconds": res.seconds})

    out = ROOT / "results" / f"verify_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"host": args.host, "lab": args.lab, "snmp_calls": snmp.calls,
                               "results": results}, indent=2))
    counts = {v: sum(r["verdict"] == v for r in results) for v in ICON}
    print(f"\n{counts}  snmp calls: {snmp.calls}  saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
