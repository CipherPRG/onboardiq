"""Group the failures in an intake report by the pattern of their error message.

  python scripts/intake.py ~/data/librenms/mibs --out ~/data/mibs_json_test --report ~/intake_report.json
  python scripts/intake_causes.py ~/intake_report.json

Names, numbers and line numbers are replaced so that "Identifier should not end with '-': abc- at MIB X, line 7"
and the same error in another module count as one cause. For each cause: how many modules, which kind, an
example module and message. Also lists the modules that failed only because something they import failed.
"""
import json
import re
import sys
from collections import Counter, defaultdict


def pattern(msg):
    m = re.sub(r"'[^']*'", "'…'", msg or "")
    m = re.sub(r"\bat MIB \S+", "at MIB …", m)
    m = re.sub(r"\(.*?\bnear offset \d+", "(… near offset N", m)
    m = re.sub(r"line \d+", "line N", m)
    m = re.sub(r"(?<=: )\S+(?= at MIB)", "<name>", m)
    m = re.sub(r"\b[A-Za-z0-9_-]*-MIB\b", "<MODULE>", m)
    m = re.sub(r"\b\d+\b", "N", m)
    return m[:160]


def main(path):
    d = json.load(open(path))
    failed, missing = d["failed"], d.get("missing_imports", {})
    groups, example = Counter(), {}
    for mod, msg in failed.items():
        p = pattern(msg)
        groups[p] += 1
        example.setdefault(p, (mod, msg))
    # a module is "blocked" when it imports a module that itself failed (the root cause is elsewhere)
    blocked = {m for m in failed if any(i in failed for i in missing.get(m, []))}
    print(f"{len(failed)} failed; {len(blocked)} of them import a module that also failed or is missing\n")
    for p, n in groups.most_common(15):
        mod, msg = example[p]
        print(f"{n:5d}  {p}\n       e.g. {mod}: {msg[:140]}")
    roots = Counter(i for m in failed for i in missing.get(m, []))
    if roots:
        print("\nmost often missing (adding these would unblock the most modules):")
        for i, n in roots.most_common(12):
            print(f"{n:5d}  {i}")


if __name__ == "__main__":
    main(sys.argv[1])
