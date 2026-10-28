"""Convert a folder of MIB files you already have into the JSON OnboardIQ reads.

  python scripts/intake.py ~/vendor_mibs --out ~/data/mibs_json
  python scripts/intake.py ~/vendor_mibs --out ~/data/mibs_json --only MIKROTIK-MIB,HOST-RESOURCES-MIB

Nothing is downloaded or uploaded. Where to get MIBs: your device vendor's download page, or run
`make mibs` in a clone of prometheus/snmp_exporter's generator (you then accept each vendor's own terms).
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from onboardiq import intake  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default=str(Path.home() / "data" / "mibs_json"))
    ap.add_argument("--only", help="comma-separated module names (default: every module found)")
    a = ap.parse_args()
    r = intake.convert(a.folder, a.out, a.only.split(",") if a.only else None)
    print(f"converted {len(r['converted'])} module(s) into {a.out}")
    for m, why in r["failed"].items():
        print(f"  FAILED {m}: {why}")
    for m, miss in r["missing_imports"].items():
        print(f"  {m} imports modules not in the folder (add them): {', '.join(miss)}")
    if r.get("note"):
        print(" ", r["note"])
    print("MIB files and the converted JSON carry the vendors' copyright: keep them local, do not commit them.")


if __name__ == "__main__":
    main()
