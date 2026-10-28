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


def modules_in(folder):
    """{module name: file path} for every MIB module defined by a file in the folder (recursive)."""
    out = {}
    for p in sorted(Path(folder).expanduser().rglob("*")):
        if not p.is_file() or p.stat().st_size > 20_000_000:
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        for name in DEFINES.findall(text):
            out.setdefault(name, p)
    return out


def imports_of(path):
    """Module names a file imports from."""
    text = Path(path).read_text(errors="replace")
    text = re.sub(r"--[^\n]*", "", text)       # ASN.1 comments
    m = IMPORT_BLOCK.search(text)
    return sorted(set(FROM.findall(m.group(1)))) if m else []


def convert(folder, out_dir, only=None, extra_dirs=None):
    """Convert the folder's modules to JSON in out_dir. Imports are looked up in the folder first, then in
    extra_dirs (default: system_dirs()). Returns {"converted": [...], "failed": {module: reason},
    "missing_imports": {module: [imported modules found nowhere]}}."""
    mine = modules_in(folder)
    pool = {}
    for d in (extra_dirs if extra_dirs is not None else system_dirs()):
        pool.update(modules_in(d))
    pool.update(mine)                                  # the user's own files win
    mods = {k: v for k, v in mine.items() if not only or k in set(only)}
    if not mods:
        return {"converted": [], "failed": {}, "missing_imports": {}, "note": "no MIB modules found in the folder"}
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    missing = {}
    for m, p in mods.items():
        gone = [i for i in imports_of(p) if i not in pool]
        if gone:
            missing[m] = gone
    converted, failed = [], {}
    with tempfile.TemporaryDirectory() as tmp:
        # one flat source directory with file names equal to module names (what mibdump looks up)
        src = Path(tmp) / "src"
        src.mkdir()
        for name, p in pool.items():
            shutil.copyfile(p, src / name)
        for name in sorted(mods):
            if name in BUILTIN:
                continue                               # base SMI modules are used as import text only
            dest = Path(tmp) / "out"
            dest.mkdir(exist_ok=True)
            r = subprocess.run([_mibdump(), "--quiet", "--mib-source", f"file://{src}", "--destination-format", "json",
                                "--destination-directory", str(dest), "--generate-mib-texts", "--ignore-errors", name],
                               capture_output=True, text=True, timeout=120)
            produced = dest / f"{name}.json"
            if produced.exists() and produced.stat().st_size > 2:
                try:
                    json.loads(produced.read_text())
                except ValueError:
                    failed[name] = "converter wrote invalid JSON"
                    continue
                shutil.copyfile(produced, out_dir / produced.name)
                converted.append(name)
            else:
                failed[name] = ((r.stderr or r.stdout).strip().splitlines() or ["converter wrote nothing"])[-1][:200]
    return {"converted": converted, "failed": failed, "missing_imports": missing}
