"""Does verification make the agent more accurate? Measured on recordings of real devices.

For each benchmark device with a LibreNMS test recording (tests/snmpsim/<os>.snmprec, a walk
captured from a real unit), run the full agent: read the MIB, verify each pick against the
recorded device, re-read on rejection. Score the FIRST pick and the FINAL pick against the
answer key, and check how often a pick the verifier passed is actually right.

    python scripts/recorded_eval.py --limit 5          # try a few first
    python scripts/recorded_eval.py                    # all test devices

Limits, stated in the output too:
- a recording is one snapshot: no load experiments and no change over time, so only the
  static checks (exists, type, range, rows) and the second-object comparison can run;
- LibreNMS recordings mostly hold the objects LibreNMS itself polls, so "not served by this
  device" favours the answer key. Corrections are therefore split by what triggered them:
  absence, or a type/range failure (the fair part).
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from onboardiq import agent, mib  # noqa: E402
from onboardiq.recorded import RecordedSnmp  # noqa: E402

FAST = {"observe_interval": 0, "witness_interval": 0, "observe_s": 0}


def score(pick, key, names):
    """'right' | 'wrong-object' | 'wrong-scale' | 'none' for one pick against key {MOD::obj: scale|None}."""
    if not pick or not pick.get("oid"):
        return "none"
    name = names.get(pick["oid"].lstrip(".").removesuffix(".0"))
    if name not in key:
        return "wrong-object"
    want, got = key[name], pick.get("scale_to_canonical")
    if want is None or (got is not None and abs(got - want) <= 1e-9 * max(1, abs(want))):
        return "right"
    return "wrong-scale"


def _brief(pick, names):
    if not pick or not pick.get("oid"):
        return None
    return {"object": names.get(pick["oid"].lstrip(".").removesuffix(".0"), pick.get("object_name")),
            "scale": pick.get("scale_to_canonical"),
            "witness": (pick.get("witness") or {}).get("object_name")}


def recording(sim: Path, os_name: str):
    exact = sim / f"{os_name}.snmprec"
    if exact.exists():
        return exact
    alts = sorted(sim.glob(f"{os_name}_*.snmprec"))
    return alts[0] if alts else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", default=str(ROOT / "data" / "benchmark_librenms.json"))
    ap.add_argument("--librenms", default=str(Path.home() / "data" / "librenms"))
    ap.add_argument("--mib-dir", default=str(Path.home() / "data" / "mibs_json"))
    ap.add_argument("--split", default="test")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--effort", default="high")
    args = ap.parse_args()
    sim = Path(args.librenms).expanduser() / "tests" / "snmpsim"
    if not sim.is_dir():
        raise SystemExit(f"no recordings at {sim}\n  cd {args.librenms} && git sparse-checkout add tests/snmpsim")
    bench = json.loads(Path(args.bench).read_text())
    todo = [(o, d) for o, d in sorted(bench.items()) if d["split"] == args.split and recording(sim, o)]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} {args.split} devices with a recording", flush=True)

    rows, cost = [], 0.0
    for i, (os_name, d) in enumerate(todo, 1):
        mods = d["modules"] if isinstance(d["modules"], list) else d["modules"].split(",")
        cands = mib.candidates(Path(args.mib_dir).expanduser(), mods)
        names = {c["oid"].lstrip(".").removesuffix(".0"): f"{c['module']}::{c['name']}" for c in cands}
        fams = [f for f in ("cpu", "memory", "temperature", "fan") if d.get(f)]
        rec = recording(sim, os_name)
        try:
            out = agent.onboard(d["device"], cands, RecordedSnmp(rec), lab=False, families=fams,
                                effort=args.effort, verify_kw=dict(FAST), log=lambda m: None)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(todo)}] {os_name}: ERROR {type(e).__name__}: {str(e)[:120]}")
            continue
        cost += out["cost_usd"]
        first = {}
        trig = {}
        for ev in out["trace"]:
            if ev["kind"] in ("verify-start", "no-pick") and ev["family"] not in first:
                first[ev["family"]] = ev.get("pick") or {}
            if ev["kind"] == "verify" and ev["verdict"] == "rejected" and ev["family"] not in trig:
                bad = [e for e in ev["evidence"] if e["ok"] is False]
                trig[ev["family"]] = "absent" if bad and agent.NOT_SERVED in bad[-1]["detail"] else \
                    (bad[-1]["check"] if bad else "?")
        for f in fams:
            fin = out["spec"][f]
            rows.append({"os": os_name, "recording": rec.name, "family": f,
                         "first": score(first.get(f), d[f], names),
                         "final": score(fin["pick"], d[f], names),
                         "verdict": fin["verdict"], "attempts": fin["attempts"],
                         "trigger": trig.get(f),
                         "key": d[f],
                         "first_pick": _brief(first.get(f), names),
                         "final_pick": _brief(fin["pick"], names),
                         "evidence": [e["detail"] for e in fin["evidence"]][-4:]})
        s = " ".join(f"{r['family']}:{r['first']}->{r['final']}({r['verdict']})" for r in rows[-len(fams):])
        print(f"[{i}/{len(todo)}] {os_name}: {s}  ${cost:.2f}", flush=True)

    n = len(rows)
    if not n:
        return
    first_ok = sum(r["first"] == "right" for r in rows)
    final_ok = sum(r["final"] == "right" for r in rows)
    passed = [r for r in rows if r["verdict"] in ("proven-static", "proven-witness", "proven-experiment")]
    unproven = [r for r in rows if r["verdict"] == "unproven"]
    print(f"\n=== {n} questions on {len(todo)} recorded real devices ({args.split} split)")
    print(f"  first pick right   {first_ok}/{n} = {first_ok / n:.0%}")
    print(f"  final pick right   {final_ok}/{n} = {final_ok / n:.0%}")
    fixed = [r for r in rows if r["first"] != "right" and r["final"] == "right"]
    broke = [r for r in rows if r["first"] == "right" and r["final"] != "right"]
    print(f"  fixed by re-reading {len(fixed)}  (by trigger: {dict(Counter(r['trigger'] for r in fixed))})")
    print(f"  broken by re-reading {len(broke)}")
    if passed:
        ok = sum(r["final"] == "right" for r in passed)
        print(f"  passed the checks  {len(passed)}; of those right {ok}/{len(passed)} = {ok / len(passed):.0%}")
    if unproven:
        ok = sum(r["final"] == "right" for r in unproven)
        print(f"  left unproven      {len(unproven)}; of those right {ok}/{len(unproven)}")
    print(f"  verdicts {dict(Counter(r['verdict'] for r in rows))}")
    print(f"  model cost ${cost:.2f}")
    print("  limits: snapshots only (no load tests); LibreNMS recordings favour the key's objects for "
          "'not served' — see 'by trigger'")
    res = ROOT / "results" / f"recorded_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    res.parent.mkdir(exist_ok=True)
    res.write_text(json.dumps({"args": vars(args), "rows": rows, "cost_usd": round(cost, 4)}, indent=1))
    print(f"  saved {res.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
