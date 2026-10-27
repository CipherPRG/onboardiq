"""Day-1 accuracy test: can Muse Spark pick the right health OIDs from a real vendor MIB?

Modes compared on the same ground truth (eval/ground_truth_day1.json):
  baseline  keyword parser, no AI (first object whose name matches cpu/mem/temp/fan)
  with-mib  Muse Spark reads every pollable object of the vendor's MIB modules
  no-mib    Muse Spark gets only the device name (memorisation check)
  scrambled Muse Spark reads the MIB with every object name, OID and module replaced by
            neutral ids (X0001...) and camelCase names scrubbed from descriptions, so only
            types, units and description text remain: is it reading or recognising names?

Usage:
  python scripts/accuracy_test.py                      # all vendors, all modes
  python scripts/accuracy_test.py --modes baseline     # free, no API calls
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from onboardiq import mib  # noqa: E402

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


def norm(oid):
    return None if not oid else oid.strip().lstrip(".")


# ---------- baseline: keyword parser, no AI ----------
PATTERNS = {
    "cpu": re.compile(r"cpu", re.I),
    "memory": re.compile(r"mem", re.I),
    "temperature": re.compile(r"temp", re.I),
    "fan": re.compile(r"fan", re.I),
}


def baseline(cands):
    out = {}
    for fam, pat in PATTERNS.items():
        hit = next((c for c in cands if pat.search(c["name"])), None)
        if not hit:
            out[fam] = {"oid": None, "scale_to_canonical": None, "object_name": None}
            continue
        units = (hit["units"] or "").lower()
        scale = 1024 if units in ("kilo-bytes", "kb", "kilobytes") else 1
        out[fam] = {"oid": hit["oid"], "scale_to_canonical": scale, "object_name": hit["name"]}
    return out


# ---------- Muse Spark modes ----------
def ask_model(device, cands, with_mib, model, effort, anonymised=False):
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
    content = r["content"].strip()
    if content.startswith("```"):
        content = content.strip("`").split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(content), r


CAMEL = re.compile(r"\b[a-z][a-z0-9]*[A-Z][A-Za-z0-9]*\b")


def scramble(cands):
    """Neutral ids for every object; returns (scrambled candidates, id -> real candidate)."""
    back, out = {}, []
    for i, c in enumerate(cands, 1):
        xid = f"X{i:04d}"
        back[xid] = c
        out.append({**c, "oid": xid, "module": "DEVICE-MIB", "name": xid,
                    "description": CAMEL.sub("<obj>", c["description"])})
    return out, back


def unscramble(pred, back):
    for fam in FAMILIES:
        p = pred.get(fam) or {}
        xid = (p.get("oid") or p.get("object_name") or "").split("::")[-1].split(".")[0].strip()
        real = back.get(xid)
        p["oid"] = real["oid"] if real else None
        p["object_name"] = f"{real['module']}::{real['name']}" if real else None
    return pred


def _obj(name):
    """'MODULE::object' or 'object' -> 'object' (lower case) for name matching."""
    return None if not name else name.split("::")[-1].strip().lower()


def score(pred, truth):
    """Per family: oid_ok, and scale as correct / wrong / flagged (model said null).

    Day-1 keys are numeric OIDs; benchmark keys are 'MODULE::object' names.
    Families absent from the answer key are not scored.
    """
    rows = {}
    for fam in FAMILIES:
        if fam not in truth:
            continue
        p = pred.get(fam) or {}
        oid = norm(p.get("oid"))
        by_oid = {norm(k): v for k, v in truth[fam].items() if "::" not in k}
        by_name = {_obj(k): v for k, v in truth[fam].items() if "::" in k}
        # A scalar's instance is <oid>.0; accept it when the object itself is correct.
        if oid and oid not in by_oid and oid.endswith(".0") and oid[:-2] in by_oid:
            oid = oid[:-2]
        if oid in by_oid:
            oid_ok, want = True, by_oid[oid]
        elif _obj(p.get("object_name")) in by_name:
            oid_ok, want = True, by_name[_obj(p.get("object_name"))]
        else:
            oid_ok, want = False, None
        scale = None
        if oid_ok:
            got = p.get("scale_to_canonical")
            if want is None:
                scale = "correct"          # not scored for this object
            elif got is None:
                scale = "flagged"          # model said: cannot tell from the MIB
            else:
                scale = "correct" if abs(float(got) - want) < 1e-9 else "wrong"
        rows[fam] = {
            "oid": oid, "name": p.get("object_name"), "oid_ok": oid_ok,
            "scale_ok": scale == "correct", "scale": scale,
            "trap": truth.get("traps", {}).get(oid),
        }
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mib-dir", default=str(Path.home() / "data" / "mibs_json"))
    ap.add_argument("--vendors", default="cisco,fortinet,linux")
    ap.add_argument("--modes", default="baseline,with-mib,no-mib")
    ap.add_argument("--model", default=None)
    ap.add_argument("--effort", default="minimal", help="reasoning effort: minimal..xhigh")
    ap.add_argument("--truth", default=str(ROOT / "eval" / "ground_truth_day1.json"),
                    help="answer key; data/benchmark_librenms.json for the multi-vendor benchmark")
    ap.add_argument("--split", default=None, help="benchmark only: dev or test")
    ap.add_argument("--limit", type=int, default=None, help="first N devices (sorted) after the split filter")
    args = ap.parse_args()

    truth_all = json.loads(Path(args.truth).read_text())
    if args.vendors == "all" or args.split:
        names = sorted(v for v, t in truth_all.items() if not args.split or t.get("split") == args.split)
        args.vendors = ",".join(names[: args.limit] if args.limit else names)
    results, total_cost = [], 0.0
    for vendor in args.vendors.split(","):
        truth = truth_all[vendor]
        cands = mib.candidates(Path(args.mib_dir), truth["modules"])
        print(f"\n=== {vendor}: {len(cands)} pollable objects in {', '.join(truth['modules'])}")
        for mode in args.modes.split(","):
            meta = {}
            if mode == "baseline":
                pred = baseline(cands)
            elif mode == "scrambled":
                scr, back = scramble(cands)
                pred, meta = ask_model(truth["device"], scr, True, args.model, args.effort, anonymised=True)
                pred = unscramble(pred, back)
            else:
                pred, meta = ask_model(truth["device"], cands, mode == "with-mib", args.model, args.effort)
                total_cost += meta.get("cost_usd", 0) if not meta.get("cached") else 0
            rows = score(pred, truth)
            results.append({"vendor": vendor, "mode": mode, "scores": rows, "prediction": pred,
                            "usage": meta.get("usage"), "cost_usd": meta.get("cost_usd"),
                            "seconds": meta.get("seconds"), "cached": meta.get("cached")})
            line = "  ".join(
                f"{fam}:{'OK ' if r['oid_ok'] else 'X  '}{'s' if r['scale_ok'] else ('?' if r['scale'] == 'flagged' else '-')}"
                for fam, r in rows.items())
            info = f" (${meta.get('cost_usd', 0):.4f}, {meta.get('seconds')}s{', cached' if meta.get('cached') else ''})" if meta else ""
            print(f"  {mode:9} {line}{info}")
            for fam, r in rows.items():
                if not r["oid_ok"]:
                    print(f"            {fam}: picked {r['oid']} ({r['name']})" + (f"  <- TRAP: {r['trap']}" if r["trap"] else ""))

    print("\n=== Summary (OID correct / OID+scale correct, families with an answer key)")
    for mode in args.modes.split(","):
        rs = [r for r in results if r["mode"] == mode]
        n = sum(len(r["scores"]) for r in rs)
        if not n:
            continue
        ok = sum(s["oid_ok"] for r in rs for s in r["scores"].values())
        both = sum(s["oid_ok"] and s["scale_ok"] for r in rs for s in r["scores"].values())
        flagged = sum(s["scale"] == "flagged" for r in rs for s in r["scores"].values())
        wrong = sum(s["scale"] == "wrong" for r in rs for s in r["scores"].values())
        print(f"  {mode:9} OID {ok}/{n} ({100 * ok / n:.0f}%)   OID+scale {both}/{n} ({100 * both / n:.0f}%)"
              f"   scale flagged {flagged}, wrong {wrong}")
        fams = {}
        for r in rs:
            for fam, sc in r["scores"].items():
                a, b = fams.get(fam, (0, 0))
                fams[fam] = (a + sc["oid_ok"], b + 1)
        print("            by family: " + "  ".join(f"{f} {a}/{b}" for f, (a, b) in fams.items()))
    print(f"  API cost this run: ${total_cost:.4f}")

    out = ROOT / "results" / f"day1_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print(f"  saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
