"""Agree / disagree table for two scripts/accuracy_test.py runs (for example Muse Spark 1.3 and Muse Spark Open).

    python scripts/compare_accuracy.py results/day1_A.json results/day1_B.json [--mode with-mib]

Only questions answered in both runs are used, and the count is printed. When the two models pick the same
object, how often is it right? When they differ, which one is right?
"""
import argparse
import json
import sys
from pathlib import Path


def load(path, mode):
    d = json.loads(Path(path).read_text())
    rows = {}
    for r in d["results"] if "results" in d else d:
        if r.get("mode") != mode:
            continue
        for fam, s in (r.get("scores") or {}).items():
            rows[(r["vendor"], fam)] = s
    return rows


def compare(a, b):
    keys = sorted(set(a) & set(b))
    t = {"n": len(keys), "agree": 0, "agree_right": 0, "differ": 0, "only_a": 0, "only_b": 0, "neither": 0}
    for k in keys:
        x, y = a[k], b[k]
        if x["oid"] and x["oid"] == y["oid"]:
            t["agree"] += 1
            t["agree_right"] += bool(x["oid_ok"])
        else:
            t["differ"] += 1
            if x["oid_ok"] and not y["oid_ok"]:
                t["only_a"] += 1
            elif y["oid_ok"] and not x["oid_ok"]:
                t["only_b"] += 1
            elif not x["oid_ok"] and not y["oid_ok"]:
                t["neither"] += 1
    return t


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_a", help="first run, for example Muse Spark 1.3")
    ap.add_argument("run_b", help="second run, for example Muse Spark Open")
    ap.add_argument("--mode", default="with-mib")
    args = ap.parse_args(argv)
    t = compare(load(args.run_a, args.mode), load(args.run_b, args.mode))
    if not t["n"]:
        sys.exit("no questions answered in both runs")
    print(f"questions answered in both runs: {t['n']}")
    print(f"  same object picked:      {t['agree']:3}  (right: {t['agree_right']}/{t['agree']})")
    print(f"  different object picked: {t['differ']:3}  (only run A right: {t['only_a']}, only run B right: {t['only_b']}, neither: {t['neither']})")


if __name__ == "__main__":
    main()
