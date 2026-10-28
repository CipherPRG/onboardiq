"""Load MIB modules converted to JSON by pysmi's mibdump (--generate-mib-texts)."""
import json
from pathlib import Path

READABLE = {"read-only", "read-write", "read-create", "accessible-for-notify"}


def load_module(mib_dir: Path, module: str) -> dict:
    path = Path(mib_dir) / f"{module}.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found (convert the MIB with mibdump first)")
    return json.loads(path.read_text())


def _tcs(mods: dict) -> dict:
    """{TC name: its definition} across the loaded modules (first definition wins)."""
    out = {}
    for data in mods.values():
        for name, obj in data.items():
            if isinstance(obj, dict) and obj.get("class") == "textualconvention":
                out.setdefault(name, obj)
    return out


def resolve_syntax(syntax: dict, tcs: dict) -> dict:
    """Follow textual conventions to the declared range, enum labels and display hint.

    Returns {"range": [{min, max}] | None, "enums": {label: int} | None, "display_hint": str | None}.
    Only what the MIB text declares; nothing is inferred.
    """
    rng = enums = hint = None
    seen = set()
    cur = syntax or {}
    while cur:
        cons = cur.get("constraints") or {}
        rng = rng or cons.get("range")
        enums = enums or cons.get("enumeration")
        tc = tcs.get(cur.get("type"))
        if not tc or cur.get("type") in seen:
            break
        seen.add(cur.get("type"))
        hint = hint or tc.get("displayhint")
        cur = tc.get("type") if isinstance(tc.get("type"), dict) else None
    return {"range": rng, "enums": enums, "display_hint": hint}


def candidates(mib_dir: Path, modules: list[str], desc_chars: int = 300) -> list[dict]:
    """Every pollable object (scalar or table column) in the given modules.

    "range" is the object's own constraint (it appears in the Reader's prompt, so it stays as it was);
    "mib_range", "enums" and "display_hint" also follow textual conventions and feed the device profile.
    "entry" and "index" describe the table row a column belongs to (for row names and joins).
    """
    mods = {m: load_module(mib_dir, m) for m in modules}
    tcs = _tcs(mods)
    out = []
    for module, data in mods.items():
        rows = {o.get("oid"): o for o in data.values()
                if isinstance(o, dict) and o.get("nodetype") == "row"}
        for name, obj in data.items():
            if not isinstance(obj, dict) or obj.get("class") != "objecttype":
                continue
            if obj.get("nodetype") not in ("scalar", "column"):
                continue
            if obj.get("maxaccess") not in READABLE:
                continue
            syntax = obj.get("syntax") or {}
            rng = (syntax.get("constraints") or {}).get("range")
            res = resolve_syntax(syntax, tcs)
            entry = obj["oid"].rsplit(".", 1)[0] if obj["nodetype"] == "column" else None
            row = rows.get(entry) or {}
            index = [i.get("object") for i in row.get("indices") or [] if isinstance(i, dict)]
            aug = row.get("augmention") or {}
            if not index and isinstance(aug, dict) and aug.get("object"):
                index = [f"augments:{aug['object']}"]
            out.append({
                "module": module,
                "name": name,
                "oid": obj["oid"],
                "kind": obj["nodetype"],
                "type": syntax.get("type"),
                "units": obj.get("units"),
                "range": rng,
                "status": obj.get("status"),
                "description": " ".join((obj.get("description") or "").split())[:desc_chars],
                "mib_range": res["range"],
                "enums": res["enums"],
                "display_hint": res["display_hint"],
                "entry": entry,
                "index": index or None,
            })
    return out


def as_prompt_lines(cands: list[dict]) -> str:
    lines = []
    for c in cands:
        extra = []
        if c["units"]:
            extra.append(f"units={c['units']}")
        if c["range"]:
            extra.append(f"range={c['range'][0].get('min')}..{c['range'][0].get('max')}")
        if c["status"] and c["status"] != "current":
            extra.append(f"status={c['status']}")
        lines.append(
            f"{c['oid']} {c['module']}::{c['name']} [{c['kind']} {c['type']}"
            + (" " + " ".join(extra) if extra else "") + f"] {c['description']}"
        )
    return "\n".join(lines)
