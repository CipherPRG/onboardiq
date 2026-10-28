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



# The agent asks for one more field per pick: the model's own prediction of how the value moves
# under load, which the verifier then tests (instead of guessing from the object's name).
# The accuracy benchmarks keep the original schema so their results stay comparable.
PICK_AGENT = {**PICK, "required": PICK["required"] + ["under_load"],
              "properties": {**PICK["properties"], "under_load": {
                  "type": "string", "enum": ["rise", "fall", "none"],
                  "description": "How this value should move when the device is put under the matching load "
                                 "(cpu: all cores busy; memory: 1 GiB allocated): rise, fall, or none"}}}
# A witness is a second, independent object for the same quantity; on the device both are read
# and must agree. It proves picks that cannot be loaded (temperature, fans, remote devices).
WITNESS = {"type": ["object", "null"], "additionalProperties": False,
           "required": ["oid", "object_name", "scale_to_canonical", "why_independent"],
           "properties": {"oid": {"type": ["string", "null"]}, "object_name": {"type": ["string", "null"]},
                          "scale_to_canonical": {"type": ["number", "null"]},
                          "why_independent": {"type": ["string", "null"]}}}
PICK_AGENT["required"] = PICK_AGENT["required"] + ["witness"]
PICK_AGENT["properties"] = {**PICK_AGENT["properties"], "witness": WITNESS}
SCHEMA_AGENT = {**SCHEMA, "properties": {**SCHEMA["properties"], **{f: PICK_AGENT for f in FAMILIES}}}
AGENT_NOTE = ("\nFor each pick also predict under_load: how the value moves when the device is loaded "
              "(cpu: all cores busy; memory: 1 GiB allocated). It will be tested on the live device.\n"
              "For each pick also name a witness: a different object in these modules that reports the same "
              "quantity in the same canonical unit after its own scale (for example a vendor temperature table "
              "and ENTITY-SENSOR-MIB, or a vendor CPU percentage and hrProcessorLoad). Both will be read on the "
              "device and must agree. Use null if no such second object exists; never repeat the pick itself.\n")

REPICK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["pick", "reasoning"],
                 "properties": {"pick": PICK_AGENT, "reasoning": {"type": "string"}}}


ALIASES = {"object_name": ("name", "module_name", "object"), "scale_to_canonical": ("scale",)}


def _normalise(pick):
    """Providers that do not enforce the JSON schema (Baseten) rename fields; map the known aliases
    back. Only renames: a value is never invented, and unknown extra fields are ignored downstream."""
    if not isinstance(pick, dict):
        return pick
    for field, alts in ALIASES.items():
        if pick.get(field) is None:
            for a in alts:
                if pick.get(a) is not None:
                    pick[field] = pick[a]
                    break
    # a second object ("witness") must be an object with an oid; a bare name or list has nothing to test,
    # so it counts as no witness (never a guessed oid)
    if "witness" in pick and not isinstance(pick["witness"], dict):
        pick["witness"] = None
    name = pick.get("object_name")
    if isinstance(name, str) and "::" in name:
        pick["object_name"] = name.split("::", 1)[1]
    return pick


def _parse(content):
    content = content.strip()
    if content.startswith("```"):
        parts = content.strip("`").split("\n", 1)
        content = parts[-1].rsplit("```", 1)[0]
    try:
        data = json.loads(content)
    except ValueError:
        i = content.find("{")
        if i < 0:
            raise
        data, _ = json.JSONDecoder().raw_decode(content[i:])
    if isinstance(data, dict):
        for k, v in data.items():
            if k in FAMILIES:
                # a model that ignores the JSON schema may answer a family with a bare string; that is not a
                # pick (no oid to test), so it counts as no answer for the family, never a guessed value
                data[k] = _normalise(v) if isinstance(v, dict) or v is None else None
            elif k == "pick":
                _normalise(v)
    return data


def _read_once(device, cands, with_mib, model, effort, anonymised=False, agent=False, use_cache=True):
    """agent=True: the product path, which also asks for each pick's predicted direction under load."""
    from onboardiq import llm

    if with_mib:
        note = ("\nObject identifiers and names are anonymised (X0001, X0002, ...). Put the X-id of each "
                "chosen object in both the oid and object_name fields.\n" if anonymised else "")
        note += AGENT_NOTE if agent else ""
        user = (f"Device: {device}\n{note}Pollable objects from its MIB modules "
                f"({len(cands)} objects; format: OID MODULE::name [kind type extras] description):\n\n"
                + mib.as_prompt_lines(cands))
    else:
        user = (f"Device: {device}\nNo MIB text is provided. From your own knowledge, give the "
                "standard SNMP objects for each health metric family. Use null if unsure.")
    r = llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                 model=model, max_tokens=16000, json_schema=SCHEMA_AGENT if agent else SCHEMA,
                 reasoning_effort=effort, use_cache=use_cache)
    return r


def read(device, cands, with_mib, model, effort, anonymised=False, agent=False):
    """One model call, retried once if the reply is not valid JSON. After two bad replies the
    product path (agent=True) raises; benchmark mode records a miss for every family."""
    err = None
    for attempt in range(2):
        # The retry must skip the cache: a bad reply is cached too, and would come straight back.
        r = _read_once(device, cands, with_mib, model, effort, anonymised, agent, use_cache=attempt == 0)
        try:
            return _parse(r["content"]), r
        except ValueError as e:
            c = r.get("content") or ""
            err = f"{e}; reply was {len(c)} chars, ends: {c[-80:]!r}"
    if agent:
        raise ValueError(f"model reply was not valid JSON twice: {err}")
    r["parse_failed"] = True
    return {f: None for f in FAMILIES}, r




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
            "unless the evidence shows only its scale or your predicted direction (under_load) was wrong; then "
            "keep it and correct that. If no object fits, "
            "return oid null. Predict under_load for the new pick. Explain in reasoning what the evidence "
            "told you.\n\n"
            f"Pollable objects ({len(cands)}; format: OID MODULE::name [kind type extras] description):\n\n"
            + mib.as_prompt_lines(cands))
    err = None
    for attempt in range(2):
        # the retry skips the cache: a bad reply is cached too and would come straight back
        r = llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                     model=model, max_tokens=12000, json_schema=REPICK_SCHEMA, reasoning_effort=effort,
                     use_cache=attempt == 0)
        try:
            out = _parse(r["content"])
            if isinstance(out.get("pick"), dict):
                return out["pick"], out.get("reasoning", ""), r
            err = "reply has no pick object"
        except ValueError as e:
            c = r.get("content") or ""
            err = f"{e}; reply was {len(c)} chars, ends: {c[-60:]!r}"
    # two unusable replies: report no pick for this family (the agent records "no object chosen")
    r["parse_failed"] = True
    return ({"oid": None, "object_name": None, "scale_to_canonical": None},
            f"model reply unusable twice: {err}", r)
