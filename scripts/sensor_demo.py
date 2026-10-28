"""Find recorded real devices that serve temperature AND fan sensors, and onboard one of them end to end.

  python scripts/sensor_demo.py --list                       # candidates (from the benchmark + your MIB folder)
  python scripts/sensor_demo.py --os linux --record /tmp/sensors.json   # onboard one, show what was proven

The device is a recording of a real unit (LibreNMS test walks, GPL: used here for testing only, never committed).
Load experiments need a live device, so CPU/memory are proven by a second object; temperature and fan are
proven by a second object too (witness). Use --record to save the session for the web player.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from onboardiq import agent, mib, replay  # noqa: E402
from onboardiq.recorded import RecordedSnmp  # noqa: E402
from scripts.recorded_eval import FAST, recording  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", default=str(ROOT / "data" / "benchmark_librenms.json"))
    ap.add_argument("--librenms", default=str(Path.home() / "data" / "librenms"))
    ap.add_argument("--mib-dir", default=str(Path.home() / "data" / "mibs_json"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--os")
    ap.add_argument("--recording", help="any .snmprec file (instead of --os), with --modules and --device")
    ap.add_argument("--modules", help="comma-separated MIB modules, with --recording")
    ap.add_argument("--device", help="what the device is, with --recording, e.g. 'Cisco Catalyst IOS-XE switch'")
    ap.add_argument("--record")
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--check-key", action="store_true",
                    help="with --os: print, for the answer key's temperature/fan objects, whether the recording serves them")
    a = ap.parse_args()
    sim = Path(a.librenms).expanduser() / "tests" / "snmpsim"
    mibs = Path(a.mib_dir).expanduser()
    bench = json.loads(Path(a.bench).read_text())
    if a.recording:
        if not (a.modules and a.device):
            raise SystemExit("--recording needs --modules and --device")
        d = {"device": a.device, "modules": a.modules.split(","), "cpu": True, "memory": True,
             "temperature": True, "fan": True}
        rec = Path(a.recording).expanduser()
    elif a.list or not a.os:
        print("os                     temp-rows fan-rows  modules")
        for os_name, d in sorted(bench.items()):
            if not (d.get("temperature") and d.get("fan") and recording(sim, os_name)):
                continue
            mods = d["modules"] if isinstance(d["modules"], list) else d["modules"].split(",")
            if not all((mibs / f"{m}.json").exists() for m in mods):
                continue
            byname = {f"{c['module']}::{c['name']}": c["oid"].lstrip(".").removesuffix(".0") for c in mib.candidates(mibs, mods)}
            rows = RecordedSnmp(recording(sim, os_name)).rows

            def served(fam):
                n = 0
                for name in d.get(fam) or {}:
                    oid = byname.get(name)
                    if oid:
                        pre = tuple(int(x) for x in oid.split("."))
                        n += sum(1 for k in rows if k[:len(pre)] == pre)
                return n
            t, f = served("temperature"), served("fan")
            print(f"{os_name:22} {t:9} {f:8}  {','.join(mods)}{'   <-- both served' if t and f else ''}")
        return
    else:
        d = bench[a.os]
        rec = recording(sim, a.os)
    mods = d["modules"] if isinstance(d["modules"], list) else d["modules"].split(",")
    cands = mib.candidates(mibs, mods)
    dev = replay.Recorder(RecordedSnmp(rec))
    fams = [f for f in ("cpu", "memory", "temperature", "fan") if d.get(f)]
    if a.check_key:
        byname = {f"{c['module']}::{c['name']}": c["oid"].lstrip(".").removesuffix(".0") for c in cands}
        for fam in fams:
            for name, scale in (d.get(fam) or {}).items():
                oid = byname.get(name)
                n = 0
                if oid:
                    pre = tuple(int(x) for x in oid.split("."))
                    n = sum(1 for k in dev.snmp.rows if k[:len(pre)] == pre)
                print(f"  {fam:12} {name:55} scale {scale}  oid {oid}  rows served: {n}")
        return
    out = agent.onboard(d["device"], cands, dev, lab=False, families=fams, model=a.model, max_attempts=a.max_attempts,
                        verify_kw=dict(FAST), log=print, modules=mods)
    print("\n=== What was proven")
    for fam, f in out["spec"].items():
        p = f["pick"]
        print(f"  {fam:12} {f['verdict']:18} {p.get('object_name')} x{p.get('scale_to_canonical')}")
    if a.record:
        dev.save(a.record, args={"device": d["device"], "modules": mods, "lab": False, "families": fams},
                 cands=cands, result={f: v["verdict"] for f, v in out["spec"].items()})
        print(f"recorded -> {a.record}")


if __name__ == "__main__":
    main()
