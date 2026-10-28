"""Compare recorded-eval runs made with different models or hosts.

    python scripts/compare_runs.py results/recorded_eval_A.json results/recorded_eval_B.json [...]

Prints one line per run (model, questions, first/final pick right, cost) and, for the first two runs,
the cross-check: do two models agreeing on a pick mean it is more likely right than disagreeing?
Only questions answered in both runs are used, and the count is printed, so a run with errors is visible.
Two hosts of the same open weights are the same model, not two opinions: use a different model for the cross-check.
"""
import json
import sys
from pathlib import Path


def load(path):
    d = json.loads(Path(path).read_text())
    rows = {(r["os"], r["family"]): r for r in d["rows"]}
    return {"path": Path(path).name, "model": (d.get("args") or {}).get("model") or "default (Meta API)",
            "rows": rows, "cost": d.get("cost_usd", 0.0)}


def pick_name(r):
    p = r.get("first_pick")
    return p["object"] if p else None


def line(run):
    rows = list(run["rows"].values())
    n = len(rows)
    if not n:
        return f"{run['model']:<48} no rows"
    first = sum(r["first"] == "right" for r in rows)
    final = sum(r["final"] == "right" for r in rows)
    devices = len({r["os"] for r in rows})
    return (f"{run['model']:<48} {n:>4} questions / {devices:>3} devices   first {first / n:>4.0%}   "
            f"final {final / n:>4.0%}   cost ${run['cost']:.2f}")


def cross_check(a, b):
    both = sorted(set(a["rows"]) & set(b["rows"]))
    agree = [k for k in both if pick_name(a["rows"][k]) == pick_name(b["rows"][k])]
    differ = [k for k in both if k not in set(agree)]

    def acc(keys, run):
        return (sum(run["rows"][k]["first"] == "right" for k in keys), len(keys))
    print(f"\nCross-check: {a['model']}  vs  {b['model']}  ({len(both)} questions answered by both)")
    for name, keys in (("agree", agree), ("disagree", differ)):
        if not keys:
            print(f"  {name:<9} 0 questions")
            continue
        ra, na = acc(keys, a)
        rb, nb = acc(keys, b)
        print(f"  {name:<9} {na:>4} questions   first pick right: A {ra / na:.0%}   B {rb / nb:.0%}")
    print("  Reading it: if A is right far more often when they agree, disagreement is a usable warning "
          "sign (verify or ask a person). If the two rates are close, say so and do not claim it.")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    runs = [load(p) for p in sys.argv[1:]]
    print("Runs:")
    for r in runs:
        print("  " + line(r))
    if len(runs) >= 2:
        cross_check(runs[0], runs[1])


if __name__ == "__main__":
    main()
