"""IP-only onboarding: ask the device what it is, find which MIB modules it actually serves.

    device --GET sysDescr/sysObjectID--> identity
    device --WALK enterprises + standard health subtrees--> the OIDs it really answers
    MIB folder index (module -> its pollable object OIDs) x answered OIDs --> modules ranked
    by how many of their objects the device serves

Nothing vendor-specific is written here: the module index is built from the MIB files themselves,
and the walked subtrees are standard roots (RFC 1155 enterprises, RFC 2790 HOST-RESOURCES, RFC 6933
ENTITY-MIB, RFC 3433 ENTITY-SENSOR-MIB). A module only counts if the device answers its objects,
so a wrong guess about the vendor cannot add modules the device does not serve.
"""
import bisect
import json
from pathlib import Path

from .mib import READABLE

SYS_DESCR, SYS_OBJECT_ID = "1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.2.0"
ENTERPRISES = "1.3.6.1.4.1"
STANDARD_ROOTS = ("1.3.6.1.2.1.25", "1.3.6.1.2.1.47.1.1.1", "1.3.6.1.2.1.99")
INDEX_VERSION = 1


def build_index(mib_dir: Path, cache: Path | None = None) -> dict:
    """{module: [pollable object OIDs]} for every converted module; cached next to the MIBs."""
    mib_dir = Path(mib_dir)
    files = sorted(mib_dir.glob("*.json"))
    stamp = [INDEX_VERSION, len(files), max((f.stat().st_mtime for f in files), default=0)]
    cache = Path(cache or mib_dir / ".onboardiq_index.json")
    if cache.exists():
        try:
            data = json.loads(cache.read_text())
            if data.get("stamp") == stamp:
                return data["modules"]
        except (ValueError, KeyError):
            pass
    modules = {}
    for f in files:
        try:
            d = json.loads(f.read_text())
        except ValueError:
            continue
        oids = [o["oid"] for o in d.values() if isinstance(o, dict) and o.get("class") == "objecttype"
                and o.get("nodetype") in ("scalar", "column") and o.get("maxaccess") in READABLE]
        if oids:
            modules[f.stem] = sorted(oids)
    try:
        cache.write_text(json.dumps({"stamp": stamp, "modules": modules}))
    except OSError:
        pass
    return modules


def identity(snmp) -> dict:
    got = {v.oid: v for v in snmp.get(SYS_DESCR, SYS_OBJECT_ID)}
    descr = got.get(SYS_DESCR)
    sysobj = got.get(SYS_OBJECT_ID)
    obj = sysobj.value.lstrip(".") if sysobj and sysobj.type != "missing" else None
    ent = None
    if obj and obj.startswith(ENTERPRISES + "."):
        ent = int(obj[len(ENTERPRISES) + 1:].split(".")[0])
    return {"sysDescr": descr.value if descr and descr.type != "missing" else None,
            "sysObjectID": obj, "enterprise": ent}


def served_oids(snmp) -> list[str]:
    seen = set()
    for root in (ENTERPRISES,) + STANDARD_ROOTS:
        seen.update(v.oid for v in snmp.walk(root) if v.type != "missing")
    return sorted(seen, key=lambda o: tuple(int(x) for x in o.split(".")))


def _key(oid):
    return tuple(int(x) for x in oid.split("."))


def rank_modules(index: dict, served: list[str]) -> list[dict]:
    """Modules whose objects the device answers, most answered objects first."""
    keys = [_key(o) for o in served]
    out = []
    for module, oids in index.items():
        hit = 0
        for oid in oids:
            k = _key(oid)
            i = bisect.bisect_left(keys, k)
            if i < len(keys) and keys[i][:len(k)] == k:   # the object itself or a row under it
                hit += 1
        if hit:
            out.append({"module": module, "served": hit, "objects": len(oids)})
    out.sort(key=lambda m: (-m["served"], m["module"]))
    return out


def discover(snmp, mib_dir: Path) -> dict:
    ident = identity(snmp)
    served = served_oids(snmp)
    ranked = rank_modules(build_index(mib_dir), served)
    return {"identity": ident, "served_oids": len(served), "modules": ranked}


def select_modules(ranked: list[dict], max_objects: int = 1500) -> list[str]:
    """Most-served modules first, until the prompt budget (pollable objects) is used."""
    chosen, total = [], 0
    for m in ranked:
        if total + m["objects"] > max_objects and chosen:
            continue
        chosen.append(m["module"])
        total += m["objects"]
    return chosen


def describe(ident: dict) -> str:
    """Device text for the reader, from the device's own words (sysDescr), never invented."""
    text = " ".join((ident.get("sysDescr") or "").split())[:200]
    ent = ident.get("enterprise")
    tail = f" (sysObjectID {ident['sysObjectID']})" if ident.get("sysObjectID") else ""
    return (text or (f"device of IANA enterprise {ent}" if ent else "unidentified SNMP device")) + tail
