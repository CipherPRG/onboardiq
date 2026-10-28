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
    "measured on the device. Use ONLY those facts. Do not invent causes, hardware faults or numbers. If the "
    "facts do not show why something happened, say what is known and what to check next. Write 2 to 4 short "
    "plain sentences. Quote numbers exactly as they appear in the facts. No markdown, no lists, no headings."
)
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


def template(ev):
    """Deterministic explanation from the same facts (used when the model fails the check or is unreachable)."""
    s = ev.get("score_out_of_100")
    if s is None:
        return "Nothing on this device has been proven yet, so no health score can be given."
    parts = [f"Health is {s} out of 100 ({ev.get('level')})."]
    if ev.get("capped_by"):
        parts[0] = parts[0][:-1] + f", held down by a {ev['capped_by']} reading."
    low = [(fam, r) for fam, f in ev["families"].items() for r in f["lowest_rows"]]
    for fam, r in sorted(low, key=lambda t: t[1]["score"])[:2]:
        parts.append(f"{fam} {r['row']} scores {r['score']}: {r['why']}.")
    if not low:
        parts.append("Every proven reading is within its limits.")
    if ev.get("anomalies"):
        parts.append("Unusual for this device: " + "; ".join(ev["anomalies"][:2]) + ".")
    return " ".join(parts)


def check(text, ev):
    """(ok, reason). Numbers must come from the evidence; text must be short plain prose."""
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


def explain(ev, model=None):
    """{"text", "source", "checked", "note"?}. Never raises: a failure returns the template."""
    try:
        r = llm.chat([{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": "Facts:\n" + json.dumps(ev, indent=1)}],
                     model=model or config.EXPLAIN_MODEL, max_tokens=1200, reasoning_effort="low")
    except Exception as e:  # noqa: BLE001 - no key, out of budget, outage: the monitor must keep running
        return {"text": template(ev), "source": "template", "checked": True,
                "note": f"model unavailable ({type(e).__name__})"}
    text = (r.get("content") or "").strip()
    ok, why = check(text, ev)
    if ok:
        return {"text": text, "source": r.get("model"), "checked": True, "cost_usd": r.get("cost_usd", 0)}
    return {"text": template(ev), "source": "template", "checked": True,
            "note": f"{r.get('model')} answer rejected: {why}", "cost_usd": r.get("cost_usd", 0)}
