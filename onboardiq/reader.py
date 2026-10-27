"""Reader: Muse Spark reads a device's MIB objects and picks what to monitor.

Shared by the product (agent loop) and the accuracy tests, so the prompts are identical
and cached answers are reused.
"""
import json

from . import mib

FAMILIES = ["cpu", "memory", "temperature", "fan"]

PICK = {
    "type": ["object"],
    "additionalProperties": False,
    "required": ["oid", "object_name", "value_type", "raw_unit", "scale_to_canonical",
                 "canonical_unit", "row_filter", "evidence", "confidence"],
    "properties": {
        "oid": {"type": ["string", "null"], "description": "Numeric OID; for table columns no index"},
        "object_name": {"type": ["string", "null"]},
        "value_type": {"type": "string", "enum": ["gauge", "counter", "string", "status", "none"]},
        "raw_unit": {"type": ["string", "null"]},
        "scale_to_canonical": {"type": ["number", "null"],
                               "description": "raw * scale = canonical value; null for raw counters"},
        "canonical_unit": {"type": "string", "enum": ["percent", "bytes", "celsius", "rpm", "status", "ticks", "none"]},
        "row_filter": {"type": ["string", "null"],
                       "description": "How to pick the right table rows, e.g. sensor name contains 'Temp'"},
        "evidence": {"type": ["string", "null"], "description": "Exact quote from the MIB description"},
        "confidence": {"type": "number"},
    },
}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": FAMILIES + ["rejected"],
    "properties": {
        **{f: PICK for f in FAMILIES},
        "rejected": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["oid", "reason"],
                "properties": {"oid": {"type": "string"}, "reason": {"type": "string"}},
            },
        },
    },
}

SYSTEM = """You are a senior SNMP monitoring engineer onboarding a new device.
Choose ONE object for each health metric family a monitoring system should poll:
- cpu: device-wide CPU utilisation
- memory: device-wide memory (utilisation percent, or used/total size)
- temperature: a temperature measurement (not a threshold)
- fan: fan speed or fan status
Rules:
- Prefer current objects over deprecated ones when a current replacement exists.
- Prefer device-wide totals over per-process, per-VDOM, per-cluster-member or per-module objects.
- For table columns give the column OID without an index; explain any row filter needed.
- scale_to_canonical makes raw * scale = canonical unit (percent, bytes, celsius, rpm, status=1).
  Read the units and the description text: e.g. kilobytes -> 1024, millidegrees -> 0.001.
- If the device exposes no object for a family, return oid null. Never invent OIDs.
- evidence must quote the description text that justifies the choice.
- List notable look-alike objects you rejected, with the reason."""



REPICK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["pick", "reasoning"],
                 "properties": {"pick": PICK, "reasoning": {"type": "string"}}}


def _parse(content):
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`").split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(content)


def read(device, cands, with_mib, model, effort, anonymised=False):
    from onboardiq import llm

    if with_mib:
        note = ("\nObject identifiers and names are anonymised (X0001, X0002, ...). Put the X-id of each "
                "chosen object in both the oid and object_name fields.\n" if anonymised else "")
        user = (f"Device: {device}\n{note}Pollable objects from its MIB modules "
                f"({len(cands)} objects; format: OID MODULE::name [kind type extras] description):\n\n"
                + mib.as_prompt_lines(cands))
    else:
        user = (f"Device: {device}\nNo MIB text is provided. From your own knowledge, give the "
                "standard SNMP objects for each health metric family. Use null if unsure.")
    r = llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                 model=model, max_tokens=16000, json_schema=SCHEMA, reasoning_effort=effort)
    return _parse(r["content"]), r




def repick(device, cands, family, attempts, model=None, effort="high"):
    """Choose again for one family after live-device evidence rejected earlier choices.

    attempts: [{"pick": {...}, "verdict": str, "evidence": [...]}] in order.
    Returns (pick, reasoning, llm_result).
    """
    from . import llm

    tried = []
    for a in attempts:
        p = a["pick"]
        why = "; ".join(f"{e['check']}: {e['detail']}" for e in a["evidence"] if e["ok"] is False) or a["verdict"]
        tried.append(f"- {p.get('object_name')} ({p.get('oid')}, scale {p.get('scale_to_canonical')}): "
                     f"{a['verdict'].upper()} on the live device. Evidence: {why}")
    user = (f"Device: {device}\nMetric family: {family}\n\n"
            "Your earlier choice(s) were tested on the live device and failed:\n" + "\n".join(tried) +
            "\n\nUsing the evidence, choose again from the objects below. Do not repeat a failed object "
            "unless the evidence shows only its scale was wrong (then correct the scale). If no object fits, "
            "return oid null. Explain in reasoning what the evidence told you.\n\n"
            f"Pollable objects ({len(cands)}; format: OID MODULE::name [kind type extras] description):\n\n"
            + mib.as_prompt_lines(cands))
    r = llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                 model=model, max_tokens=12000, json_schema=REPICK_SCHEMA, reasoning_effort=effort)
    out = _parse(r["content"])
    return out["pick"], out.get("reasoning", ""), r
