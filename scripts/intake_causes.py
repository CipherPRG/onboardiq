"""Group the failures in an intake report by the pattern of their error message, with the evidence behind each.

  python scripts/intake.py ~/data/librenms/mibs --out ~/data/mibs_json_test --report ~/intake_report.json
  python scripts/intake_causes.py ~/intake_report.json            # counts per cause
  python scripts/intake_causes.py ~/intake_report.json --detail   # + the source lines behind the top causes
  python scripts/intake_causes.py ~/intake_report.json ~/data/librenms/mibs --detail   # older report: give the MIB folder too

Names, numbers and line numbers are replaced so the same error in different modules counts as one cause.
A module that failed only because something it needs also failed is counted as a "chain" under its root.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from onboardiq import intake  # noqa: E402


def pattern(msg):
    m = re.sub(r"'[^']*'", "'…'", msg or "")
    m = re.sub(r"\bat MIB \S+", "at MIB …", m)
    m = re.sub(r"near offset \d+", "near offset N", m)
    m = re.sub(r"line \d+", "line N", m)
    m = re.sub(r'(Duplicate symbol found|Unknown parents? for symbols?|Identifier should not end with [^:]*): [^()]*?(?= at MIB)', r"\1: <name>", m)
    m = re.sub(r'no (module|symbol) "[^"]*"', r'no \1 "<name>"', m)
    m = re.sub(r'in module "[^"]*"', 'in module "<M>"', m)
    m = re.sub(r"^Failed MIBs: \S+ ", "Failed MIBs: <M> ", m)
    m = re.sub(r"\b\d+\b", "N", m)
    return m[:150]


def chain_root(mod, failed, files, seen=()):
    """Follow imports of a failed module to the deepest failed module it needs (its root), else itself."""
    if mod in seen or mod not in files:
        return mod
    try:
        imps = [i for i in intake.imports_of(files[mod]) if i in failed and i != mod]
    except OSError:
        return mod
    return chain_root(imps[0], failed, files, seen + (mod,)) if imps else mod


def grep(path, word, n=3):
    out = []
    try:
        for i, ln in enumerate(Path(path).read_text(errors="replace").splitlines(), 1):
            if re.search(r"(?<![\w-])" + re.escape(word) + r"(?![\w-])", ln):
                out.append(f"line {i}: {ln.strip()[:110]}")
                if len(out) == n:
                    break
    except OSError:
        pass
    return out


def line_at(path, n):
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
        return [f"line {k}: {lines[k - 1].strip()[:110]}" for k in range(max(n - 1, 1), min(n + 1, len(lines)) + 1)]
    except OSError:
        return []


def detail(mod, msg, failed, files):
    f = files.get(mod)
    out = []
    m = re.search(r'no module "([^"]+)" in symbolTable', msg)
    if m:
        dep = m.group(1)
        out.append(f"needs {dep}: " + (f"it also FAILED ({failed[dep][:90]})" if dep in failed else "not in the failed list"))
    m = re.search(r"Duplicate symbol found: (\S+)", msg)
    if m and f:
        out += grep(f, m.group(1))
    m = re.search(r"Unknown parents? (?:for symbols?: )?(\w+)", msg)
    if m and f:
        out += grep(f, m.group(1), 2)
    m = re.search(r"line (\d+)", msg)
    if m and f and "grammar" in msg:
        out += line_at(f, int(m.group(1)))
    return out


def main(path, show_detail=False, folder=None):
    d = json.load(open(path))
    failed, missing, files = d["failed"], d.get("missing_imports", {}), d.get("files", {})
    if folder and not files:                       # an older report: find each failed module's file again
        found = intake.modules_in(folder)
        files = {m: str(found[m]) for m in failed if m in found}
    groups, examples = Counter(), {}
    for mod, msg in failed.items():
        p = pattern(msg)
        groups[p] += 1
        examples.setdefault(p, []).append((mod, msg))
    roots = Counter()
    for mod in failed:
        r = chain_root(mod, failed, files)
        if r != mod:
            roots[r] += 1
    chained = sum(roots.values())
    print(f"{len(failed)} failed; {chained} of them import another failed module (a chain; the root is the real fault)\n")
    for p, n in groups.most_common(12):
        print(f"{n:5d}  {p}")
        for mod, msg in examples[p][:3 if show_detail else 1]:
            print(f"       e.g. {mod}")
            if show_detail:
                for ln in detail(mod, msg, failed, files):
                    print(f"            {ln}")
    if roots:
        print("\nroot modules whose failure blocks the most others (fix these first):")
        for r, n in roots.most_common(10):
            print(f"{n:5d}  {r}: {failed[r][:100]}")
    gone = Counter(i for m in failed for i in missing.get(m, []))
    if gone:
        print("\nmost often missing (adding these would unblock the most modules):")
        for i, n in gone.most_common(8):
            print(f"{n:5d}  {i}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], "--detail" in sys.argv, args[1] if len(args) > 1 else None)
