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
    ap.add_argument("--workers", type=int, default=4, help="modules converted at the same time (default 4)")
    ap.add_argument("--why", metavar="MODULE", help="show the full converter output for one module and exit")
    ap.add_argument("--report", help="also write the counts and every failure to this JSON file")
    a = ap.parse_args()
    if a.why:
        print(intake.why(a.folder, a.why))
        return
    r = intake.convert(a.folder, a.out, a.only.split(",") if a.only else None, workers=a.workers)
    print(f"converted {len(r['converted'])} module(s) into {a.out}")
    sm = intake.summary(r)
    print(f"  {sm['modules']} tried: {sm['converted_clean']} clean, {sm['converted_incomplete']} converted but missing an "
          f"import, {sm['failed']} failed {sm['failed_by_cause'] or ''}")
    if sm["most_missing_imports"]:
        print("  most often missing:", ", ".join(f"{m} ({n})" for m, n in sm["most_missing_imports"]))
    if sm["repaired"]:
        print(f"  {sm['repaired']} module(s) converted after fixing a typo in a temporary copy (your files are not changed):")
        for m, note in list(r["repaired"].items())[:5]:
            print(f"    {m}: {note}")
    if sm["duplicate_modules"]:
        print(f"  {sm['duplicate_modules']} module name(s) defined in more than one file (the first one is used)")
    if a.report:
        import json
        Path(a.report).write_text(json.dumps({"summary": sm, **r}, indent=1))
        print(f"  full report: {a.report}")
    for m, why in r["failed"].items():
        print(f"  FAILED {m}: {why}")
    for m, miss in r["missing_imports"].items():
        print(f"  {m} imports modules not in the folder (add them): {', '.join(miss)}")
    if r.get("note"):
        print(" ", r["note"])
    print("MIB files and the converted JSON carry the vendors' copyright: keep them local, do not commit them.")


if __name__ == "__main__":
    main()
