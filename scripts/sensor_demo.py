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
    ap.add_argument("--record")
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    sim = Path(a.librenms).expanduser() / "tests" / "snmpsim"
    mibs = Path(a.mib_dir).expanduser()
    bench = json.loads(Path(a.bench).read_text())
    if a.list or not a.os:
        print("os                     modules (all in your MIB folder?)")
        for os_name, d in sorted(bench.items()):
            if not (d.get("temperature") and d.get("fan") and recording(sim, os_name)):
                continue
            mods = d["modules"] if isinstance(d["modules"], list) else d["modules"].split(",")
            have = all((mibs / f"{m}.json").exists() for m in mods)
            print(f"{os_name:22} {','.join(mods)}  {'READY' if have else 'missing MIB'}")
        return
    d = bench[a.os]
    mods = d["modules"] if isinstance(d["modules"], list) else d["modules"].split(",")
    cands = mib.candidates(mibs, mods)
    dev = replay.Recorder(RecordedSnmp(recording(sim, a.os)))
    fams = [f for f in ("cpu", "memory", "temperature", "fan") if d.get(f)]
    out = agent.onboard(d["device"], cands, dev, lab=False, families=fams, model=a.model,
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
