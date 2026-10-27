"""Load MIB modules converted to JSON by pysmi's mibdump (--generate-mib-texts)."""
import json
from pathlib import Path

READABLE = {"read-only", "read-write", "read-create", "accessible-for-notify"}


def load_module(mib_dir: Path, module: str) -> dict:
    path = Path(mib_dir) / f"{module}.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found (convert the MIB with mibdump first)")
    return json.loads(path.read_text())


def candidates(mib_dir: Path, modules: list[str], desc_chars: int = 300) -> list[dict]:
    """Every pollable object (scalar or table column) in the given modules."""
    out = []
    for module in modules:
        for name, obj in load_module(mib_dir, module).items():
            if not isinstance(obj, dict) or obj.get("class") != "objecttype":
                continue
            if obj.get("nodetype") not in ("scalar", "column"):
                continue
            if obj.get("maxaccess") not in READABLE:
                continue
            syntax = obj.get("syntax") or {}
            rng = (syntax.get("constraints") or {}).get("range")
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
