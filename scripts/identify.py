"""Photo of a device -> what it is -> which MIB modules to read (Muse Spark vision).

    python scripts/identify.py label.jpg [front.jpg ...] [--hint "ceiling access point, hostel"]

Prints the identity, the chosen modules and the onboarding command. Photos are sent to the
Meta Model API only; they are not saved anywhere by this tool.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from onboardiq import identify  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("photos", nargs="+")
    ap.add_argument("--hint")
    ap.add_argument("--mib-dir", default=str(Path.home() / "data" / "mibs_json"))
    a = ap.parse_args()
    mib_dir = Path(a.mib_dir).expanduser()
    available = sorted(p.stem for p in mib_dir.glob("*.json"))
    if not available:
        raise SystemExit(f"no converted MIB modules in {mib_dir}")
    out = identify.identify([Path(p).read_bytes() for p in a.photos], available, hint=a.hint)
    print(json.dumps(out["identity"], indent=2))
    print(f"\ndevice:  {out['device']}")
    print(f"modules: {', '.join(out['modules']) or '(none chosen)'}")
    if out["modules_why"]:
        print(f"why:     {out['modules_why']}")
    if out["modules_dropped"]:
        print(f"dropped (not in your MIB folder): {', '.join(out['modules_dropped'])}")
    if out["identity"].get("ask_for"):
        print(f"to be sure: {out['identity']['ask_for']}")
    print(f"cost:    ${out['cost_usd']:.4f}")
    if out["modules"]:
        print(f'\nnext: python scripts/onboard.py --device "{out["device"]}" --modules {",".join(out["modules"])}')


if __name__ == "__main__":
    main()
