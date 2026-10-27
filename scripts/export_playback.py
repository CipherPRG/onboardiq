"""Turn recorded sessions into playback files for the web player (web/sessions/*.json).

Each playback file is the replay's event trace (read, verify, re-read, final) with the measured
chart series, plus a short header. It contains no MIB dump, no credentials and no host details.

  python scripts/export_playback.py demo/*.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from onboardiq import replay  # noqa: E402

TITLES = {
    "laptop_hostresources": "Linux host: catches its own memory mistake",
    "laptop_full": "Linux host: all four metrics",
    "laptop_witness": "Linux host: memory proven two ways",
    "laptop_ucd_hr": "Linux host: predicted directions",
    "mikrotik_chr_cpu": "MikroTik RouterOS: CPU under load on the router",
}
KEEP = ("read", "verify-start", "verify", "repick", "no-pick", "final", "done")


def export(path: Path) -> dict:
    session = json.loads(path.read_text())
    events = []
    out = replay.replay(path, log=lambda *a: None, on_event=lambda e: events.append(e))
    events = [e for e in events if e["kind"] in KEEP]
    a = session["args"]
    return {
        "id": path.stem,
        "title": TITLES.get(path.stem, out["device"]),
        "device": out["device"],
        "modules": a.get("modules"),
        "lab": a.get("lab"),
        "objects": len(session.get("cands", [])),
        "mode": "Replay of a recorded live session",
        "summary": {f: v["verdict"] for f, v in out["spec"].items()},
        "seconds": out.get("replay", {}).get("virtual_seconds"),
        "snmp_calls": out.get("snmp_calls"),
        "events": events,
    }


def main():
    files = [Path(f) for f in sys.argv[1:]] or sorted((ROOT / "demo").glob("*.json"))
    dest = ROOT / "web" / "sessions"
    dest.mkdir(parents=True, exist_ok=True)
    index = []
    order = list(TITLES)
    files.sort(key=lambda f: order.index(f.stem) if f.stem in order else len(order))
    for f in files:
        pb = export(f)
        (dest / f"{pb['id']}.json").write_text(json.dumps(pb, separators=(",", ":")))
        index.append({"id": pb["id"], "title": pb["title"], "device": pb["device"], "summary": pb["summary"]})
        print(f"{f.name}: {len(pb['events'])} events -> web/sessions/{pb['id']}.json")
    (dest / "index.json").write_text(json.dumps(index, indent=1))


if __name__ == "__main__":
    main()
