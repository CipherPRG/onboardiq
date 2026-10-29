"""MIB intake: turn a folder of MIB files the user already has into the JSON the agent reads.

    python scripts/intake.py ~/vendor_mibs --out ~/data/mibs_json

What it does, and what it never does:
  - finds every module defined in the folder (any file name or extension), converts them with pysmi
    (the same `mibdump` step used for the benchmark) and resolves IMPORTS from the same folder;
  - reports what it could not convert (parse errors) and which imported modules are missing, so a
    stranger sees exactly which extra MIB to add (e.g. SNMPv2-TC, IF-MIB);
  - never downloads anything and never copies the user's MIB text anywhere else. MIB files and their
    converted JSON carry the vendor's copyright, so they stay on the user's machine (the output folder
    is local, and the project's .gitignore keeps data/ and mibs/ out of the repository).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFINES = re.compile(r"^\s*([A-Za-z][A-Za-z0-9-]*)\s+DEFINITIONS\s*(?:(?:EXPLICIT|IMPLICIT|AUTOMATIC)\s+TAGS\s*)?::=\s*BEGIN",
                     re.M)
IMPORT_BLOCK = re.compile(r"\bIMPORTS\b(.*?);", re.S)
FROM = re.compile(r"\bFROM\s+([A-Za-z][A-Za-z0-9-]*)")
# Base SMI modules the converter carries itself: not converted, never reported as missing or failed.
BUILTIN = {"SNMPv2-SMI", "SNMPv2-TC", "SNMPv2-CONF", "RFC1155-SMI", "RFC-1212", "RFC-1215", "RFC1065-SMI"}


def system_dirs():
    """Places a machine with net-snmp usually keeps the standard IETF MIB texts (SNMPv2-SMI, SNMPv2-TC, ...),
    plus anything in OIQ_MIB_DIRS. Read only, on the user's own machine; the base modules the folder's MIBs
    import are taken from here when the folder does not have them."""
    cand = ["/usr/share/snmp/mibs", "/usr/share/mibs", "/usr/local/share/snmp/mibs",
            str(Path.home() / ".snmp" / "mibs")] + [d for d in os.environ.get("OIQ_MIB_DIRS", "").split(":") if d]
    return [d for d in cand if Path(d).is_dir()]


def _mibdump():
    """The mibdump next to this Python (the project's virtual environment), else the one on PATH."""
    here = Path(sys.executable).parent / "mibdump"
    return str(here) if here.exists() else (shutil.which("mibdump") or "mibdump")


def modules_in(folder, duplicates=None):
    """{module name: file path} for every MIB module defined by a file in the folder (recursive). When two files
    define the same module the first (by path) wins; pass a dict as `duplicates` to learn the others."""
    out = {}
    for p in sorted(Path(folder).expanduser().rglob("*")):
        if not p.is_file() or p.stat().st_size > 20_000_000:
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        for name in DEFINES.findall(text):
            if name in out and duplicates is not None:
                duplicates.setdefault(name, [str(out[name])]).append(str(p))
            out.setdefault(name, p)
    return out


def imports_of(path):
    """Module names a file imports from."""
    text = Path(path).read_text(errors="replace")
    text = re.sub(r"--[^\n]*", "", text)       # ASN.1 comments
    m = IMPORT_BLOCK.search(text)
    return sorted(set(FROM.findall(m.group(1)))) if m else []


def _dump(name, src, tmp):
    """Run mibdump for one module into its own folder. Returns (name, json path or None, error line)."""
    dest = Path(tmp) / "out" / name
    dest.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run([_mibdump(), "--mib-source", f"file://{src}", *no_download(tmp), "--destination-format", "json",
                            "--destination-directory", str(dest), "--generate-mib-texts", "--ignore-errors", name],
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return name, None, "converter timed out after 120 s"
    produced = dest / f"{name}.json"
    if produced.exists() and produced.stat().st_size > 2:
        return name, produced, ""
    return name, None, reason_from(r.stdout + "\n" + r.stderr)


def no_download(tmp):
    """mibdump borrows a missing or failed module's compiled JSON from https://mibs.pysnmp.com unless it is given
    other places to borrow from. An empty local folder keeps the whole conversion on this machine."""
    empty = Path(tmp) / "noborrow"
    empty.mkdir(exist_ok=True)
    return ["--mib-borrower", f"file://{empty}/@mib@"]


REASON = re.compile(r"(error|fail|missing|unresolved|cannot|can't|not found|expect|unexpected|invalid|illegal)", re.I)


def reason_from(output):
    """The most telling line of mibdump's output: the first line that reports an error, else the last line."""
    lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
    if not lines:
        return "converter wrote nothing"
    for ln in lines:                                   # mibdump's own summary lines name the cause
        for key in ("Failed MIBs:", "Missing source MIBs:"):
            if ln.startswith(key) and ln[len(key):].strip():
                return ln[:200]
    for ln in lines:
        if REASON.search(ln) and "compiled" not in ln.lower() and not ln.startswith(("Borrow", "Ignore", "Missing source MIBs:", "Failed MIBs:")):
            return ln[:200]
    return "converter wrote nothing (" + lines[-1][:150] + ")"


def why(folder, module, extra_dirs=None):
    """The full converter output for one module (for a person to read): imports it needs and what was found."""
    pool = {}
    for d in (extra_dirs if extra_dirs is not None else system_dirs()):
        pool.update(modules_in(d))
    mine = modules_in(folder)
    pool.update(mine)
    if module not in pool:
        return f"{module} is not defined in {folder}"
    lines = [f"{module}: {pool[module]}"]
    for imp in imports_of(pool[module]):
        lines.append(f"  imports {imp}: " + ("builtin" if imp in BUILTIN else str(pool[imp]) if imp in pool else "NOT FOUND"))
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        src.mkdir()
        for name, p in pool.items():
            shutil.copyfile(p, src / name)
        dest = Path(tmp) / "out"
        dest.mkdir()
        r = subprocess.run([_mibdump(), "--mib-source", f"file://{src}", *no_download(tmp), "--destination-format", "json",
                            "--destination-directory", str(dest), "--generate-mib-texts", "--ignore-errors", module],
                           capture_output=True, text=True, timeout=120)
        lines.append("converter said:")
        lines += ["  " + ln for ln in (r.stdout + r.stderr).strip().splitlines()[-25:]] or ["  (nothing)"]
        lines.append("output written: " + ("yes" if (dest / f"{module}.json").exists() else "no"))
    return "\n".join(lines)


def convert(folder, out_dir, only=None, extra_dirs=None, workers=4):
    """Convert the folder's modules to JSON in out_dir. Imports are looked up in the folder first, then in
    extra_dirs (default: system_dirs()). Returns
      {"converted": [...], "failed": {module: reason}, "missing_imports": {module: [imported modules found nowhere]},
       "incomplete": [converted modules that import something not found: some names may be unresolved],
       "duplicates": {module: [files defining it]}}.
    Modules are converted `workers` at a time; one failing module never stops the others."""
    duplicates = {}
    mine = modules_in(folder, duplicates)
    pool = {}
    for d in (extra_dirs if extra_dirs is not None else system_dirs()):
        pool.update(modules_in(d))
    pool.update(mine)                                  # the user's own files win
    mods = {k: v for k, v in mine.items() if not only or k in set(only)}
    if not mods:
        return {"converted": [], "failed": {}, "missing_imports": {}, "incomplete": [], "duplicates": {},
                "note": "no MIB modules found in the folder"}
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    missing = {}
    for m, p in mods.items():
        gone = [i for i in imports_of(p) if i not in pool and i not in BUILTIN]
        if gone:
            missing[m] = gone
    converted, failed = [], {}
    with tempfile.TemporaryDirectory() as tmp:
        # one flat source directory with file names equal to module names (what mibdump looks up)
        src = Path(tmp) / "src"
        src.mkdir()
        for name, p in pool.items():
            shutil.copyfile(p, src / name)
        todo = sorted(n for n in mods if n not in BUILTIN)    # base SMI modules are used as import text only
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            results = list(ex.map(lambda n: _dump(n, src, tmp), todo))
        for name, produced, err in results:
            if produced is None:
                failed[name] = err
                continue
            try:
                json.loads(produced.read_text())
            except ValueError:
                failed[name] = "converter wrote invalid JSON"
                continue
            shutil.copyfile(produced, out_dir / produced.name)
            converted.append(name)
    return {"converted": converted, "failed": failed, "missing_imports": missing,
            "incomplete": sorted(m for m in converted if m in missing),
            "duplicates": {k: v for k, v in duplicates.items() if not only or k in set(only)}}


def failure_kind(reason):
    """A short cause for a failure line, for counting: missing import / syntax / timeout / nothing written / other."""
    r = (reason or "").lower()
    if "timed out" in r:
        return "timeout"
    if "invalid json" in r:
        return "invalid output"
    if "wrote nothing" in r:
        return "nothing written"
    if any(w in r for w in ("not found", "unresolved", "no module", "failed to locate", "missing source")):
        return "missing import"
    if any(w in r for w in ("syntax", "parse", "lex", "unexpected", "expecting", "error at", "grammar")):
        return "syntax"
    return "other"


def summary(r):
    """Counts for a report: modules tried, converted cleanly, converted but incomplete, failed (by cause),
    modules that import something missing, and duplicate definitions."""
    from collections import Counter
    total = len(r["converted"]) + len(r["failed"])
    return {"modules": total, "converted": len(r["converted"]),
            "converted_clean": len(r["converted"]) - len(r.get("incomplete", [])),
            "converted_incomplete": len(r.get("incomplete", [])), "failed": len(r["failed"]),
            "failed_by_cause": dict(Counter(failure_kind(v) for v in r["failed"].values())),
            "most_missing_imports": Counter(i for v in r["missing_imports"].values() for i in v).most_common(10),
            "duplicate_modules": len(r.get("duplicates", {}))}
