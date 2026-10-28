"""Plain-English explanation of a health reading, grounded in the proven evidence.

The model (Muse Spark Open by default) sees only a compact JSON of facts the agent measured: the
score and its rule-based reasons, which rows are low, the anomaly notes (current value vs the row's
own usual value), what was not scored, and the recent score history. It may not add facts.

The answer is then checked by code, not trusted:
  - every number in it must occur in the evidence (a number the model made up fails the check);
  - it must be short plain text.
If the check fails, or no model is reachable, a template written from the same facts is returned and
labelled as such. The result always says who wrote it: {"text", "source": model id | "template", "checked"}.
"""
import json
import re

from . import config, llm

SYSTEM = (
    "You explain a network device's health to the person who runs it. You are given JSON facts that were "
    "measured on the device. Use ONLY those facts: do not invent causes, hardware faults, advice or numbers, and "
    "quote numbers exactly as they appear in the facts. Reply with one JSON object and nothing else, with "
    "exactly these string fields: "
    '"headline" (at most 8 words naming the problem and the part affected, no full stop), '
    '"what_happened" (one sentence: the score and how it changed), '
    '"why" (one or two sentences: the measured readings and the limits they were compared with). '
    "If the device is healthy, say so plainly. No markdown, no lists."
)
FIELDS = ("headline", "what_happened", "why")
LABEL = {"cpu": "CPU", "cpu_load": "CPU load", "memory": "Memory", "disk": "Disk", "temperature": "Temperature",
         "fan": "Fan"}
# What to check next is chosen by code from the worst family, never written by the model, so the advice
# cannot be invented. Plain, safe first steps only.
CHECKS = {
    "cpu": "Find the process using the CPU (top, or the device's process list) and check whether a scheduled job "
           "or a traffic spike started at this time.",
    "memory": "Find the process holding the memory and check whether it keeps growing, which points to a leak.",
    "disk": "Find the largest directories or logs on the full volume, then clear space or expand it.",
    "temperature": "Check airflow, the fans and the room temperature; blocked or dusty vents are the usual cause.",
    "fan": "Check that the fan is spinning and not blocked; replace it if it stays stopped.",
    "default": "Check the device's own logs around this time.",
}
CHECKS["cpu_load"] = CHECKS["cpu"]
HEALTHY_CHECK = "No action needed: every proven reading is within its limits."
NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")


TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")


def _nums(text):
    """Numbers in a text, with clock times (15:37:40) taken out whole so their parts cannot vouch for a
    number the model made up."""
    return {float(x) for x in NUM.findall(TIME.sub(" ", text or ""))}


def evidence(result, anomalies=(), provenance=None, history=(), at=None):
    """Compact facts for the explainer from health.score() output (plus optional provenance and history)."""
    fams = {}
    for fam, f in (result.get("families") or {}).items():
        low = sorted((r for r in f["rows"] if r["score"] < 100), key=lambda r: r["score"])[:4]
        fams[fam] = {"score": f["score"], "rows": len(f["rows"]), "worst": f["worst"],
                     "lowest_rows": [{"row": r["name"], "score": r["score"], "why": r["why"]} for r in low]}
    ev = {"score_out_of_100": result.get("score"), "level": result.get("level"),
          "capped_by": result.get("capped_by"), "summary": result.get("summary"), "families": fams,
          "anomalies": list(anomalies)[:6],
          "not_scored": sorted({f"{e['family']}: {e['reason']}" for e in result.get("excluded") or []})[:5],
          "policy": result.get("policy"), "coverage": result.get("coverage")}
    if at:
        ev["time"] = at
    if history:
        ev["recent_scores"] = [{"time": t, "score": s} for t, s in list(history)[-8:]]
    if provenance:
        ev["device"] = {k: provenance.get(k) for k in ("sysDescr", "firmware") if provenance.get(k)}
    return ev


def _worst(ev):
    """(family, lowest row dict) with the lowest score, or (None, None) when everything is full marks."""
    low = [(fam, r) for fam, f in (ev.get("families") or {}).items() for r in f["lowest_rows"]]
    return min(low, key=lambda t: t[1]["score"]) if low else (None, None)


def next_step(ev):
    fam, _ = _worst(ev)
    if fam is None:
        return HEALTHY_CHECK
    return CHECKS.get(fam, CHECKS["default"])


def template_card(ev):
    """Deterministic card from the same facts (used when the model fails the check or is unreachable)."""
    s = ev.get("score_out_of_100")
    if s is None:
        return {"headline": "Nothing proven yet", "what_happened": "No health score can be given.",
                "why": "Nothing on this device has been proven yet."}
    fam, row = _worst(ev)
    level = ev.get("level")
    what = f"Health is {s} out of 100 ({level})"
    if ev.get("capped_by"):
        what += f", held down by a {ev['capped_by']} reading"
    hist = ev.get("recent_scores") or []
    if len(hist) > 1 and hist[0]["score"] != s:
        what += f"; it was {hist[0]['score']} a few readings ago"
    if fam is None:
        return {"headline": "Healthy: all readings within limits", "what_happened": what + ".",
                "why": "Every proven reading is within its limits."}
    why = f"{LABEL.get(fam, fam)} {row['row']} scores {row['score']:g}: {row['why']}."
    if ev.get("anomalies"):
        why += " Unusual for this device: " + "; ".join(ev["anomalies"][:2]) + "."
    return {"headline": f"{LABEL.get(fam, fam)} {level}: {row['row']}"[:80], "what_happened": what + ".", "why": why}


def template(ev):
    c = template_card(ev)
    return f"{c['what_happened']} {c['why']}"


def _flat(card):
    """The card as plain text (what happened, then why), for alert emails and older consumers."""
    return " ".join(card[k] for k in ("what_happened", "why") if card.get(k))


def check(text, ev):
    """(ok, reason) for plain text. Numbers must come from the evidence; text must be short plain prose."""
    if not text or not text.strip():
        return False, "empty"
    if len(text) > 900 or "\n\n" in text.strip() or re.search(r"^\s*([-*#]|\d+\.)\s", text, re.M):
        return False, "not short plain prose"
    if text.strip()[-1] not in ".!?\"')":
        return False, "cut off mid-sentence"
    allowed = _nums(json.dumps(ev)) | {100.0}
    extra = sorted(n for n in _nums(text) if n not in allowed)
    if extra:
        return False, f"numbers not in the evidence: {extra[:3]}"
    return True, "ok"


def parse_card(content):
    """The model's JSON reply as {"headline", "what_happened", "why"}; ValueError if it is not one."""
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    i = content.find("{")
    if i < 0:
        raise ValueError("no JSON object")
    data, _ = json.JSONDecoder().raw_decode(content[i:])
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and data[k].strip() for k in FIELDS):
        raise ValueError("missing fields")
    for k in FIELDS:
        v = data[k]
        if "\n" in v or re.match(r"\s*([-*#]|\d+\.)\s", v) or "**" in v or "`" in v:
            raise ValueError(f"{k} is not plain prose")
    return {k: " ".join(data[k].split()) for k in FIELDS}


def check_card(card, ev):
    """(ok, reason): every field short, plain and complete; every number from the evidence."""
    if len(card["headline"]) > 80 or card["headline"][-1] in ".!?":
        return False, "headline too long or ends with a full stop"
    for k in ("what_happened", "why"):
        if len(card[k]) > 320:
            return False, f"{k} too long"
        if card[k][-1] not in ".!?":
            return False, f"{k} cut off mid-sentence"
    return check(card["headline"] + ". " + _flat(card), ev)


def explain(ev, model=None):
    """A card: {"headline", "what_happened", "why", "check", "text", "source", "checked", "note"?}.
    Never raises: a failure returns the template card. "check" (what to look at next) always comes from code."""
    def card(c, source, **extra):
        c = {**c, "check": next_step(ev)}
        return {**c, "text": _flat(c), "source": source, "checked": True, **extra}

    try:
        r = llm.chat([{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": "Facts:\n" + json.dumps(ev, indent=1)}],
                     model=model or config.EXPLAIN_MODEL, max_tokens=1500, reasoning_effort="low")
    except Exception as e:  # noqa: BLE001 - no key, out of budget, outage: the monitor must keep running
        return card(template_card(ev), "template", note=f"model unavailable ({type(e).__name__})")
    try:
        c = parse_card(r.get("content"))
        ok, why = check_card(c, ev)
    except ValueError as e:
        ok, why = False, f"not the requested JSON ({e})" if (r.get("content") or "").strip() else "empty"
    if ok:
        return card(c, r.get("model"), cost_usd=r.get("cost_usd", 0))
    return card(template_card(ev), "template", note=f"{r.get('model')} answer rejected: {why}",
                cost_usd=r.get("cost_usd", 0))
