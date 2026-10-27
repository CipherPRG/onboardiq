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
from onboardiq import agent, mib, replay, report  # noqa: E402
from onboardiq.snmp import Snmp  # noqa: E402

LOCAL = {"localhost", "127.0.0.1", "::1"}
LABEL = {"proven-experiment": "PROVEN by experiment", "proven-witness": "PROVEN by a second object",
         "proven-static": "PROVEN (static checks)",
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
    ap.add_argument("--html", help="also write the onboarding record as an HTML page")
    ap.add_argument("--load-by-hand", metavar="CPU_INSTRUCTIONS",
                    help="prove CPU picks on a remote device: you start a load on the device when asked "
                         "(e.g. a RouterOS busy loop); the agent measures before/during")
    args = ap.parse_args()
    log = lambda m: print(m, flush=True)  # noqa: E731
    if args.replay:
        print(f"REPLAY of a recorded live session: {args.replay}\n")
        out = replay.replay(args.replay, log=log)
    else:
        if not (args.device and args.modules):
            raise SystemExit("--device and --modules are required (or use --replay)")
        if args.lab and (args.host not in LOCAL or args.port not in (None, 161)):
            raise SystemExit("lab experiments only run against this machine's own agent (--host localhost, default port);\n"
                             "a forwarded port is another device (VM/container): loading this machine proves nothing about it")
        cands = mib.candidates(Path(args.mib_dir).expanduser(), args.modules.split(","))
        snmp = Snmp(args.host, args.community, port=args.port)
        dev = replay.Recorder(snmp) if args.record else snmp
        verify_kw = {}
        lab = args.lab
        if args.load_by_hand:
            from onboardiq.verify import prompt_load
            verify_kw["load"] = prompt_load({"cpu": args.load_by_hand})
            lab = True    # experiments run, but the load is applied on the device by a person
        out = agent.onboard(args.device, cands, dev, lab=lab, families=args.families.split(","),
                            max_attempts=args.max_attempts, model=args.model, effort=args.effort,
                            verify_kw=verify_kw, log=log)
        if args.record:
            keep = ("device", "modules", "lab", "families", "effort", "model", "max_attempts")
            args.lab = lab
            dev.save(args.record, args={k: v for k, v in vars(args).items() if k in keep},
                     cands=cands, result={f: v["verdict"] for f, v in out["spec"].items()})
            print(f"  recorded session -> {args.record}")
    print("\n=== Verified spec")
    for fam, f in out["spec"].items():
        p = f["pick"]
        print(f"  {fam:12} {LABEL[f['verdict']]:30} {p.get('object_name')} x{p.get('scale_to_canonical')}"
              f"  ({f['attempts']} attempt{'s' if f['attempts'] != 1 else ''})")
    if "replay" in out:
        print(f"  replayed {out['replay']['virtual_seconds']}s of live session time, {out['snmp_calls']} SNMP "
              "responses from the recording, model answers from the recording (no API key used)")
    else:
        print(f"  {out['seconds']}s, {out['snmp_calls']} SNMP calls, model cost ${out['cost_usd']:.4f}")
    res = ROOT / "results" / f"onboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    res.parent.mkdir(exist_ok=True)
    res.write_text(json.dumps({"args": vars(args), **out}, indent=2))
    print(f"  saved {res.relative_to(ROOT)}")
    if args.html:
        Path(args.html).write_text(report.render(out, title=f"OnboardIQ: {out.get('device')}"))
        print(f"  report -> {args.html}")


if __name__ == "__main__":
    main()
