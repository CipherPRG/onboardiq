"""Build a multi-vendor answer key from LibreNMS device definitions.

LibreNMS (GPL) maintains, per device OS, the objects its poller reads for CPU,
memory, temperature and fan speed. We use those mappings as an independent,
community-maintained answer key. Nothing from LibreNMS is committed: the key is
written under data/ (gitignored) and rebuilt from a local LibreNMS checkout.

Answer-key format matches eval/ground_truth_day1.json, keyed by object name:
  {os: {"device", "modules", "cpu": {"MODULE::object": scale|None}, ...}}
scale = multiplier / divisor (raw * scale = canonical); None = not scored
(memory mixes percent/bytes in LibreNMS; idle-based CPU; unit conversion functions).

Usage:
  python scripts/build_benchmark.py --librenms ~/data/librenms --mib-dir ~/data/mibs_json
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from onboardiq import mib  # noqa: E402

NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*)::([A-Za-z][A-Za-z0-9-]*)")


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("tag:yaml.org,2002:value", lambda loader, node: loader.construct_scalar(node))


def _name(ref):
    """'MIB::object.{{ $index }}' -> ('MIB', 'object'); numeric or templated-only -> None."""
    if not isinstance(ref, str):
        return None
    m = NAME.match(ref.strip())
    return (m.group(1), m.group(2)) if m else None


def _num(x, default=1.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _sensor_scale(entry):
    if entry.get("user_func"):
        return None  # e.g. fahrenheit_to_celsius: not a pure scale
    div = _num(entry.get("divisor"), 1.0) or 1.0
    return _num(entry.get("multiplier"), 1.0) / div


def extract(defn):
    """Return {family: {(module, object): scale}} from one os_discovery definition."""
    mods = defn.get("modules") or {}
    out = {}

    proc = (mods.get("processors") or {}).get("data") or []
    for e in proc:
        n = _name(e.get("value")) or _name(e.get("oid"))
        if n:
            prec = _num(e.get("precision"), 1.0)
            # negative precision = idle percentage in LibreNMS: not a plain scale
            out.setdefault("cpu", {})[n] = None if prec < 0 else 1.0 / (prec or 1.0)

    mem = (mods.get("mempools") or {}).get("data") or []
    for e in mem:
        for key in ("percent_used", "used", "free"):
            n = _name(e.get(key))
            if n:
                out.setdefault("memory", {})[n] = None

    sensors = mods.get("sensors") or {}
    for fam, kind in (("temperature", "temperature"), ("fan", "fanspeed")):
        group = sensors.get(kind) or {}
        opts = group.get("options") or {}          # group-wide divisor/multiplier/user_func
        for e in group.get("data") or []:
            n = _name(e.get("value")) or _name(e.get("oid"))
            if n and not n[1].endswith("Table"):
                out.setdefault(fam, {})[n] = _sensor_scale({**opts, **e})
    return out


def find_defs(librenms: Path):
    for sub in ("resources/definitions/os_discovery", "includes/definitions/discovery"):
        d = librenms / sub
        if d.is_dir() and any(d.glob("*.yaml")):
            return d
    raise SystemExit(f"no os_discovery definitions under {librenms}")


def device_text(librenms: Path, os_name: str, defn: dict) -> str:
    """Human name ('A10 Networks', type 'network') from os_detection, else the os key."""
    det = librenms / "resources" / "definitions" / "os_detection" / f"{os_name}.yaml"
    info = defn
    if det.exists():
        try:
            info = yaml.load(det.read_text(), Loader=_Loader) or defn
        except yaml.YAMLError:
            pass
    text = info.get("text") or os_name
    kind = info.get("type")
    return f"{text} ({kind} device)" if kind else text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--librenms", default=str(Path.home() / "data" / "librenms"))
    ap.add_argument("--mib-dir", default=str(Path.home() / "data" / "mibs_json"))
    ap.add_argument("--min-families", type=int, default=2)
    ap.add_argument("--max-objects", type=int, default=1500,
                    help="skip devices whose MIB modules expose more pollable objects (prompt cost)")
    ap.add_argument("--out", default=str(ROOT / "data" / "benchmark_librenms.json"))
    args = ap.parse_args()

    defs_dir = find_defs(Path(args.librenms).expanduser())
    mib_dir = Path(args.mib_dir).expanduser()
    bench, skipped, bad = {}, Counter(), 0
    for f in sorted(defs_dir.glob("*.yaml")):
        try:
            defn = yaml.load(f.read_text(), Loader=_Loader) or {}
        except yaml.YAMLError:
            bad += 1
            continue
        fams = extract(defn)
        if len(fams) < args.min_families:
            skipped["too few families"] += 1
            continue
        modules = sorted({m for objs in fams.values() for (m, _) in objs})
        missing = [m for m in modules if not (mib_dir / f"{m}.json").exists()]
        if missing:
            skipped["MIB not converted"] += 1
            continue
        try:
            cands = mib.candidates(mib_dir, modules)
        except Exception:  # noqa: BLE001 - malformed JSON MIB
            skipped["MIB unreadable"] += 1
            continue
        names = {(c["module"], c["name"]): c["oid"] for c in cands}
        # keep only answers the model could actually see in the prompt
        clean = {fam: {f"{m}::{o}": s for (m, o), s in objs.items() if (m, o) in names}
                 for fam, objs in fams.items()}
        clean = {fam: objs for fam, objs in clean.items() if objs}
        if len(clean) < args.min_families:
            skipped["answer not in MIB objects"] += 1
            continue
        if len(cands) > args.max_objects:
            skipped["MIB too large"] += 1
            continue
        os_name = f.stem
        bench[os_name] = {
            "device": device_text(Path(args.librenms).expanduser(), os_name, defn),
            "modules": modules,
            "objects": len(cands),
            # deterministic split by OS name: tune prompts on dev only, report on test
            "split": "dev" if int(hashlib.sha256(os_name.encode()).hexdigest(), 16) % 5 == 0 else "test",
            **clean,
        }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bench, indent=1, sort_keys=True))
    fam_count = Counter(fam for b in bench.values() for fam in ("cpu", "memory", "temperature", "fan") if fam in b)
    splits = Counter(b["split"] for b in bench.values())
    print(f"devices: {len(bench)}  ({dict(splits)})  questions: {sum(fam_count.values())}  {dict(fam_count)}")
    print(f"skipped: {dict(skipped)}  unparseable yaml: {bad}")
    print(f"objects per device: median {sorted(b['objects'] for b in bench.values())[len(bench) // 2] if bench else 0}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
