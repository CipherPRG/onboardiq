"""IP-only discovery on recordings of real devices: does it find the right MIB modules unaided?

For each benchmark device with a LibreNMS recording, run discovery (identity + walk + module
index) and check whether the modules holding the answer key's objects are among the modules the
device serves. No model calls, no cost.

    python scripts/discover_eval.py [--split test]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from onboardiq import discover  # noqa: E402
from onboardiq.recorded import RecordedSnmp  # noqa: E402
from scripts.recorded_eval import recording  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", default=str(ROOT / "data" / "benchmark_librenms.json"))
    ap.add_argument("--librenms", default=str(Path.home() / "data" / "librenms"))
    ap.add_argument("--mib-dir", default=str(Path.home() / "data" / "mibs_json"))
    ap.add_argument("--split", default="test")
    a = ap.parse_args()
    sim = Path(a.librenms).expanduser() / "tests" / "snmpsim"
    bench = json.loads(Path(a.bench).read_text())
    index = discover.build_index(Path(a.mib_dir).expanduser())
    n = found_all = found_any = 0
    sizes, misses = [], []
    for os_name, d in sorted(bench.items()):
        if a.split != "all" and d["split"] != a.split:
            continue
        rec = recording(sim, os_name)
        if not rec:
            continue
        dev = RecordedSnmp(rec)
        ranked = discover.rank_modules(index, discover.served_oids(dev))
        names = [m["module"] for m in ranked]
        want = sorted({k.split("::")[0] for f in ("cpu", "memory", "temperature", "fan") for k in (d.get(f) or {})})
        got = [m for m in want if m in names]
        n += 1
        found_all += len(got) == len(want)
        found_any += bool(got)
        sizes.append(len(names))
        if len(got) < len(want):
            misses.append((os_name, [m for m in want if m not in names], discover.identity(dev)["enterprise"]))
    sizes.sort()
    print(f"{n} recorded devices ({a.split})")
    print(f"  all answer modules found: {found_all}/{n} = {found_all / n:.0%}")
    print(f"  at least one found:       {found_any}/{n} = {found_any / n:.0%}")
    print(f"  modules served per device: median {sizes[len(sizes) // 2]}, max {sizes[-1]}")
    for m in misses[:15]:
        print("  miss:", m)


if __name__ == "__main__":
    main()
