"""Playback files for the web player, built by replaying recorded sessions.

A playback file is the replay's event trace (read, verify, re-read, final) with the measured
chart series and a short header derived from the session itself. It contains no MIB dump,
no credentials and no host details.
"""
import json
from pathlib import Path

from . import replay
from .config import ROOT

KEEP = ("read", "verify-start", "verify", "repick", "no-pick", "final", "done")
FAMILY = {"cpu": "CPU", "memory": "memory", "temperature": "temperature", "fan": "fan"}


def highlight(events, summary, args) -> str:
    """One line describing what happened, derived from the session's own events."""
    corrected = [f for f, v in summary.items() if v.startswith("proven") and any(
        e["kind"] == "verify" and e["family"] == f and e["verdict"] == "rejected" for e in events)]
    if corrected:
        return "caught and corrected its " + " and ".join(FAMILY.get(f, f) for f in corrected) + " pick"
    witnessed = [e["family"] for e in events if e["kind"] == "verify" and any(
        c["check"] == "witness" and c["ok"] for c in e.get("evidence", []))]
    if witnessed:
        return " and ".join(FAMILY.get(f, f) for f in witnessed) + " proven by experiment and by a second object"
    proven = [f for f, v in summary.items() if v.startswith("proven")]
    return f"{len(proven)} of {len(summary)} metrics proven on the device"


def _model(session):
    """The model that actually answered, as stored with its recorded answers."""
    for aid in session.get("llm_order", []):
        m = session.get("llm_cache", {}).get(aid, {}).get("model")
        if m:
            return m
    return session.get("args", {}).get("model")


def export(path: Path) -> dict:
    session = json.loads(path.read_text())
    events = []
    out = replay.replay(path, log=lambda *a: None, on_event=lambda e: events.append(e))
    events = [e for e in events if e["kind"] in KEEP]
    a = session["args"]
    return {
        "id": path.stem,
        "highlight": highlight(events, {f: v["verdict"] for f, v in out["spec"].items()}, a),
        "device": out["device"],
        "modules": a.get("modules"),
        "lab": a.get("lab"),
        "objects": len(session.get("cands", [])),
        "model": _model(session),
        "mode": "Replay of a recorded live session",
        "recorded": session.get("recorded"),
        "summary": {f: v["verdict"] for f, v in out["spec"].items()},
        "seconds": out.get("replay", {}).get("virtual_seconds"),
        "snmp_calls": out.get("snmp_calls"),
        "events": events,
    }



def export_all(files=None, dest=None) -> list:
    files = [Path(f) for f in files] if files else sorted((ROOT / "demo").glob("*.json"))
    dest = Path(dest or ROOT / "web" / "sessions")
    dest.mkdir(parents=True, exist_ok=True)
    index = []
    for f in files:
        pb = export(f)
        (dest / f"{pb['id']}.json").write_text(json.dumps(pb, separators=(",", ":")))
        index.append({"id": pb["id"], "device": pb["device"], "highlight": pb["highlight"],
                      "summary": pb["summary"], "recorded": pb["recorded"]})
    index.sort(key=lambda x: x.get("recorded") or "", reverse=True)
    (dest / "index.json").write_text(json.dumps(index, indent=1))
    return index
