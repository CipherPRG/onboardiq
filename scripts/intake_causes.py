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


def raw_text(path):
    """The file as the converter reads it: line endings kept (CRLF files shift every later offset by one per line)."""
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def around_offset(path, off, before=70, after=40):
    """The text where the converter says it stopped (a character offset into the file), on one line."""
    try:
        t = raw_text(path)
    except OSError:
        return []
    a = t[max(off - before, 0):off].replace("\r", "").replace("\n", " ⏎ ")
    b = t[off:off + after].replace("\r", "").replace("\n", " ⏎ ")
    crlf = t.count("\r\n")
    return [f"offset {off}{' (CRLF file, ' + str(crlf) + ' lines)' if crlf else ''}: …{a[-before:]} ▶ {b}"]


def where_defined(mod, symbol, files, folder_index):
    """Which file really holds a symbol the converter complained about: the module itself, or one it imports."""
    hits = []
    for name in [mod] + (intake.imports_of(files[mod]) if mod in files else []):
        path = files.get(name) or folder_index.get(name)
        if path and grep(path, symbol, 1):
            hits.append(name + (" (itself)" if name == mod else " (imported)"))
    return hits


def def_block(path, symbol, lines=7):
    """The definition of a symbol, from its name to the first '::=' line (where its parent is named)."""
    try:
        text = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return []
    for i, ln in enumerate(text):
        if re.match(r"\s*" + re.escape(symbol) + r"\s+(OBJECT|MODULE|NOTIFICATION|TRAP|OBJECT-)", ln) or \
                (re.match(r"\s*" + re.escape(symbol) + r"\s*$", ln) and i + 1 < len(text) and "OBJECT" in text[i + 1]):
            out = []
            for k in range(i, min(i + 40, len(text))):
                if "::=" in text[k]:
                    out.append(f"line {k + 1}: {text[k].strip()[:110]}")
                    return [f"defined at line {i + 1}"] + out
            return [f"defined at line {i + 1}, no '::=' within 40 lines"]
    return ["no definition line found"]


def detail(mod, msg, failed, files, folder_index=None):
    f = files.get(mod)
    folder_index = folder_index or {}
    out = []
    m = re.search(r'no module "([^"]+)" in symbolTable', msg)
    if m:
        dep = m.group(1)
        out.append(f"needs {dep}: " + (f"it also FAILED ({failed[dep][:90]})" if dep in failed else "not in the failed list"))
    m = re.search(r"Duplicate symbol found: (\S+)", msg)
    if m and f:
        sym = m.group(1)
        out += grep(f, sym)
        out.append(f"{sym} appears in: " + (", ".join(where_defined(mod, sym, files, folder_index)) or "no file I can see"))
    m = re.search(r"Unknown parents? (?:for symbols?: )?(\w+)", msg)
    if m and f:
        blk = def_block(f, m.group(1))
        out += blk
        pm = re.search(r"::=\s*\{\s*([A-Za-z][\w-]*)", " ".join(blk))
        if pm:
            parent = pm.group(1)
            out.append(f"its parent {parent} is defined in: " + (", ".join(where_defined(mod, parent, files, folder_index)) or "NOWHERE I can see")
                       + "; lines: " + " | ".join(grep(f, parent, 2)))
    m = re.search(r"Unknown parent symbol: (\S+)", msg)
    if m and f:
        out += grep(f, m.group(1), 2)
        out.append("imported in this file: " + ("yes" if any("IMPORTS" in x or "FROM" in x for x in grep(f, m.group(1), 6)) else "not obviously"))
    m = re.search(r"no symbol \"([^\"]+)\" in module \"([^\"]+)\"", msg)
    if m:
        out.append(f"{m.group(2)}: " + ("FAILED " + failed[m.group(2)][:70] if m.group(2) in failed else
                                         "exists, but does not define " + m.group(1) + " (a different version of that file?)"))
    m = re.search(r"near offset (\d+)", msg)
    if m and f and "grammar" in msg:
        out += around_offset(f, int(m.group(1)))
        m2 = re.search(r"line (\d+)", msg)
        if m2:
            out += line_at(f, int(m2.group(1)))
    return out


def main(path, show_detail=False, folder=None):
    folder_index = {}
    d = json.load(open(path))
    failed, missing, files = d["failed"], d.get("missing_imports", {}), d.get("files", {})
    if folder and not files:                       # an older report: find each failed module's file again
        found = intake.modules_in(folder)
        files = {m: str(found[m]) for m in failed if m in found}
        folder_index = {k: str(v) for k, v in found.items()}
    groups, examples = Counter(), {}
    for mod, msg in failed.items():
        p = pattern(msg)
        groups[p] += 1
        examples.setdefault(p, []).append((mod, msg))
    roots = Counter()
    for mod, msg in failed.items():
        m = re.search(r'no module "([^"]+)" in symbolTable', msg)
        if m and m.group(1) in failed:                 # direct evidence only: it says which module it needed
            roots[m.group(1)] += 1
    chained = sum(roots.values())
    print(f"{len(failed)} failed; {chained} say they could not find a module that itself failed (direct chain)\n")
    for p, n in groups.most_common(12):
        print(f"{n:5d}  {p}")
        for mod, msg in examples[p][:3 if show_detail else 1]:
            print(f"       e.g. {mod}")
            if show_detail:
                for ln in detail(mod, msg, failed, files, folder_index):
                    print(f"            {ln}")
    if roots:
        print("\nmodules whose failure was named by others as the missing piece (upper bound on what fixing them clears):")
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
