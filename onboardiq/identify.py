"""Identify a device from photos (label, front panel) with Muse Spark vision, then choose MIB modules.

    photos -> {vendor, model, device_type, model_visible, confidence, evidence}
           -> MIB modules chosen ONLY from the modules actually present in the MIB folder

Honesty rules, enforced in code as well as asked for in the prompt:
- the model reports what it can read; if no model number is visible it says so (model_visible=false)
  and the device text says "model not visible" instead of a guess;
- module names it returns that are not in the MIB folder are dropped (and reported), never used;
- serial numbers and MAC addresses are never kept: the prompt asks the model not to transcribe them
  and anything shaped like one is removed from every returned text field.
Photos are sent to the Meta Model API and are not written to disk, recordings or logs by this module.
"""
import base64
import json
import re

from . import llm

MAX_PHOTOS = 4
MAX_BYTES = 8 * 1024 * 1024
MIME = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png", b"RIFF": "image/webp", b"GIF8": "image/gif"}

MAC = re.compile(r"\b[0-9A-Fa-f]{2}(?:[:\-.]?[0-9A-Fa-f]{2}){5}\b")
SERIAL = re.compile(r"\b(?:S/?N|SN|Serial(?: No\.?)?)\s*[:#]?\s*[A-Z0-9-]{5,}\b", re.I)

SEE = {
    "type": "object", "additionalProperties": False,
    "required": ["vendor", "model", "model_visible", "device_type", "confidence", "evidence", "ask_for"],
    "properties": {
        "vendor": {"type": ["string", "null"], "description": "manufacturer, from a logo or printed text"},
        "model": {"type": ["string", "null"], "description": "model number exactly as printed; null if not legible"},
        "model_visible": {"type": "boolean", "description": "true only if a model number is legible in a photo"},
        "device_type": {"type": ["string", "null"],
                        "description": "e.g. access point, switch, router, firewall, camera, UPS, server"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "evidence": {"type": "string", "description": "what in the photos supports this (logo, printed text, "
                                                      "shape, ports); never serial numbers or MAC addresses"},
        "ask_for": {"type": ["string", "null"],
                    "description": "if something is missing, which photo would settle it (e.g. the rear label)"},
    },
}

SEE_SYSTEM = """You identify network and IT equipment from photos so it can be onboarded into SNMP monitoring.
Report only what the photos show. A logo or printed text is evidence; a shape alone is a weak hint.
If the model number is not legible, set model to null and model_visible to false, and say which photo
would show it. Never transcribe serial numbers, MAC addresses, passwords or Wi-Fi keys, even if visible.
Asset tags and room labels (e.g. "AP-9") are not model numbers."""

PICK = {
    "type": "object", "additionalProperties": False, "required": ["modules", "why"],
    "properties": {
        "modules": {"type": "array", "items": {"type": "string"}, "maxItems": 8,
                    "description": "MIB module names copied exactly from the list"},
        "why": {"type": "string"},
    },
}

PICK_SYSTEM = """You choose which SNMP MIB modules to read to monitor a device's CPU, memory, temperature
and fan speed. Choose only names that appear in the list, copied exactly. Prefer the vendor's own
health/system/sensor modules plus the standard ones the device type usually implements
(HOST-RESOURCES-MIB for hosts, ENTITY-SENSOR-MIB for chassis sensors). Up to 8 modules."""


def _scrub(text):
    if not isinstance(text, str):
        return text
    return SERIAL.sub("[serial removed]", MAC.sub("[MAC removed]", text))


def _mime(data: bytes) -> str:
    for magic, mime in MIME.items():
        if data.startswith(magic):
            return mime
    raise ValueError("photo must be JPEG, PNG, WebP or GIF")


def see(photos: list[bytes], hint: str | None = None, model=None, effort="high") -> tuple[dict, dict]:
    """Muse Spark vision: what device is this? Returns (identity, llm result)."""
    if not 1 <= len(photos) <= MAX_PHOTOS:
        raise ValueError(f"give 1 to {MAX_PHOTOS} photos")
    content = [{"type": "text", "text": "Identify this device." + (f" Operator's note: {hint}" if hint else "")}]
    for data in photos:
        if len(data) > MAX_BYTES:
            raise ValueError("photo larger than 8 MB")
        url = f"data:{_mime(data)};base64,{base64.b64encode(data).decode()}"
        content.append({"type": "image_url", "image_url": {"url": url}})
    r = llm.chat([{"role": "system", "content": SEE_SYSTEM}, {"role": "user", "content": content}],
                 model=model, max_tokens=4000, json_schema=SEE, reasoning_effort=effort)
    ident = json.loads(r["content"])
    ident = {k: _scrub(v) for k, v in ident.items()}
    if not ident.get("model_visible"):
        ident["model"] = None          # a model number the model could not read is not reported
    return ident, r


def describe(ident: dict) -> str:
    """Device text for the reader, stating exactly what is and is not known."""
    parts = [ident.get("vendor") or "unknown vendor", ident.get("device_type") or "network device"]
    text = " ".join(parts)
    return f"{text}, model {ident['model']}" if ident.get("model") else f"{text} (model not visible)"


def choose_modules(ident: dict, available: list[str], model=None, effort="minimal") -> tuple[list[str], dict]:
    """Pick MIB modules from the names actually present; anything else is dropped and reported."""
    listing = "\n".join(available)
    user = (f"Device: {describe(ident)}\nEvidence: {ident.get('evidence')}\n\n"
            f"MIB modules available ({len(available)}):\n{listing}")
    r = llm.chat([{"role": "system", "content": PICK_SYSTEM}, {"role": "user", "content": user}],
                 model=model, max_tokens=2000, json_schema=PICK, reasoning_effort=effort)
    out = json.loads(r["content"])
    have = set(available)
    chosen = [m for m in dict.fromkeys(out.get("modules") or []) if m in have]
    dropped = [m for m in out.get("modules") or [] if m not in have]
    return chosen, {"why": out.get("why"), "dropped": dropped, "cost_usd": r["cost_usd"]}


def identify(photos, available, hint=None, model=None):
    ident, r1 = see(photos, hint, model=model)
    modules, info = choose_modules(ident, available, model=model) if ident.get("vendor") else ([], {})
    return {"identity": ident, "device": describe(ident), "modules": modules,
            "modules_why": info.get("why"), "modules_dropped": info.get("dropped", []),
            "cost_usd": round(r1["cost_usd"] + info.get("cost_usd", 0), 4)}
