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


def culprit(mod, msg):
    """The module the converter actually blames: 'Failed MIBs: X (...)' names X, which is the importer's own name
    only when the fault is in its own file. For a module that imports a broken one, X is the broken dependency."""
    m = re.match(r"Failed MIBs: (\S+) \(", msg or "")
    return m.group(1) if m else mod


def main(path, show_detail=False, folder=None):
    d = json.load(open(path))
    failed, missing, files = d["failed"], d.get("missing_imports", {}), dict(d.get("files", {}))
    folder_index = {}
    if folder:                                     # find every module's file again (also the culprits)
        found = intake.modules_in(folder)
        folder_index = {k: str(v) for k, v in found.items()}
        files = {**folder_index, **files}
    own, dependents = {}, Counter()                 # culprit -> message; culprit -> how many modules it blames
    for mod, msg in failed.items():
        c = culprit(mod, msg)
        own.setdefault(c, msg)
        if c != mod:
            dependents[c] += 1
    print(f"{len(failed)} modules failed, but only {len(own)} distinct files are blamed by the converter "
          f"({sum(dependents.values())} modules failed because of a file they import)\n")
    groups, examples = Counter(), {}
    for c, msg in own.items():
        p = pattern(msg)
        groups[p] += 1
        examples.setdefault(p, []).append((c, msg))
    print("causes, counted by the distinct broken file (not by how many modules import it):")
    for p, n in groups.most_common(12):
        victims = sum(dependents[c] for c, _ in examples[p])
        print(f"{n:5d} file(s), plus {victims} importing module(s)  {p}")
        ex = sorted(examples[p], key=lambda t: -dependents[t[0]])[:3 if show_detail else 1]
        for c, msg in ex:
            print(f"       e.g. {c}  (blamed for {dependents[c]} importer(s))")
            if show_detail:
                for ln in detail(c, msg, failed, files, folder_index):
                    print(f"            {ln}")
    gone = Counter(i for m in failed for i in missing.get(m, []))
    if gone:
        print("\nmost often missing (adding these would unblock the most modules):")
        for i, n in gone.most_common(8):
            print(f"{n:5d}  {i}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], "--detail" in sys.argv, args[1] if len(args) > 1 else None)
