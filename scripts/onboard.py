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
from onboardiq import agent, health, mib, replay, report  # noqa: E402
from onboardiq import snmp as snmp_mod  # noqa: E402
from onboardiq.snmp import Snmp  # noqa: E402
from onboardiq.config import MODEL  # noqa: E402

LOCAL = {"localhost", "127.0.0.1", "::1"}
LABEL = {"proven-experiment": "PROVEN by experiment", "proven-witness": "PROVEN by a second object",
         "proven-static": "PROVEN (static checks)",
         "unproven": "UNPROVEN", "not-supported": "NOT SUPPORTED on this device", "rejected": "REJECTED"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", help="what the device is, e.g. 'Arista EOS switch' (default: discovered)")
    ap.add_argument("--modules", help="comma-separated MIB modules (default: discovered from what the "
                                      "device serves)")
    ap.add_argument("--record", help="save the session (SNMP responses + model answers) for replay")
    ap.add_argument("--replay", help="replay a recorded session: no device or API key needed")
    ap.add_argument("--mib-dir", default=str(Path.home() / "data" / "mibs_json"))
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--community", default="public")
    snmp_mod.add_cli_args(ap)
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
        if not args.modules or not args.device:
            from onboardiq import discover
            probe = Snmp(args.host, port=args.port, **snmp_mod.cli_credentials(args))
            found = discover.discover(probe, Path(args.mib_dir).expanduser())
            if not found["modules"]:
                raise SystemExit("discovery found no MIB module this device serves; pass --modules")
            args.device = args.device or discover.describe(found["identity"])
            args.modules = args.modules or ",".join(discover.select_modules(found["modules"]))
            print(f"Discovered: {args.device}\n  serves {found['served_oids']} objects; modules: {args.modules}")
        if args.lab and (args.host not in LOCAL or args.port not in (None, 161)):
            raise SystemExit("lab experiments only run against this machine's own agent (--host localhost, default port);\n"
                             "a forwarded port is another device (VM/container): loading this machine proves nothing about it")
        cands = mib.candidates(Path(args.mib_dir).expanduser(), args.modules.split(","))
        snmp = Snmp(args.host, port=args.port, **snmp_mod.cli_credentials(args))
        dev = replay.Recorder(snmp) if args.record else snmp
        verify_kw = {}
        lab = args.lab
        if args.load_by_hand:
            from onboardiq.verify import prompt_load
            verify_kw["load"] = prompt_load({"cpu": args.load_by_hand})
            lab = True    # experiments run, but the load is applied on the device by a person
        out = agent.onboard(args.device, cands, dev, lab=lab, families=args.families.split(","),
                            max_attempts=args.max_attempts, model=args.model, effort=args.effort,
                            verify_kw=verify_kw, log=log, modules=args.modules.split(","))
        if args.record:
            keep = ("device", "modules", "lab", "families", "effort", "model", "max_attempts")
            args.lab = lab
            args.model = args.model or MODEL
            dev.save(args.record, args={k: v for k, v in vars(args).items() if k in keep},
                     cands=cands, result={f: v["verdict"] for f, v in out["spec"].items()})
            print(f"  recorded session -> {args.record}")
    print("\n=== Verified spec")
    for fam, f in out["spec"].items():
        p = f["pick"]
        print(f"  {fam:12} {LABEL[f['verdict']]:30} {p.get('object_name')} x{p.get('scale_to_canonical')}"
              f"  ({f['attempts']} attempt{'s' if f['attempts'] != 1 else ''})")
    prof = out.get("profile") or {}
    if prof.get("metrics"):
        pv = prof["provenance"]
        print(f"\n=== Device profile  (sysObjectID {pv.get('sysObjectID')}, firmware {pv.get('firmware')}"
              f"{' via ' + pv['firmware_from'] if pv.get('firmware_from') else ''})")
        for fam, m in prof["metrics"].items():
            rows = m.get("rows") or []
            named = [r["name"] for r in rows if r.get("name")][:3]
            print(f"  {fam:12} kind {m.get('kind')}, {len(rows)} row(s)"
                  + (f", {len(m['skipped'])} skipped (partial)" if m.get("partial") else "")
                  + (f": {', '.join(named)}" if named else "") + (f"  [{m['note']}]" if m.get("note") else ""))
        h = health.score(prof)
        if h["score"] is not None:
            print(f"\n=== Health now: {h['score']}/100 ({h['level']})  {h['summary']}  [{h['coverage']}]")
            for e in h["excluded"][:4]:
                print(f"  not scored: {e['family']}{' ' + str(e['row']) if e.get('row') else ''}: {e['reason']}")
    elif prof.get("error"):
        print(f"\n  device profile failed: {prof['error']}")
    if "replay" in out:
        print(f"  replayed {out['replay']['virtual_seconds']}s of live session time, {out['snmp_calls']} SNMP "
              "responses from the recording, model answers from the recording (no API key used)")
    else:
        print(f"  {out['seconds']}s, {out['snmp_calls']} SNMP calls, model cost ${out['cost_usd']:.4f}")
    res = ROOT / "results" / f"onboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    res.parent.mkdir(exist_ok=True)
    # the SNMP community is a credential: it is never written to disk
    res.write_text(json.dumps({"args": {k: v for k, v in vars(args).items() if k not in ("community", "v3_user", "snmp_version")}, **out}, indent=2))
    print(f"  saved {res.relative_to(ROOT)}")
    if args.html:
        Path(args.html).write_text(report.render(out, title=f"OnboardIQ: {out.get('device')}"))
        print(f"  report -> {args.html}")


if __name__ == "__main__":
    main()
