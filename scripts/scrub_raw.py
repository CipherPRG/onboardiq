"""Remove the device's own name, contact and location from a raw health recording, in place.

  python scripts/scrub_raw.py demo/laptop_stress.raw.json

New recordings are scrubbed when saved; this is for one recorded before the scrubber existed.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from onboardiq.replay import scrub_raw  # noqa: E402

if __name__ == "__main__":
    f = Path(sys.argv[1])
    d = scrub_raw(json.loads(f.read_text()))
    f.write_text(json.dumps(d, separators=(",", ":")))
    print(f"scrubbed {f}")
