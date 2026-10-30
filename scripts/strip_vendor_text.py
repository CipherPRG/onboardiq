"""Remove vendor MIB description text from a recorded session before it is committed.

  python scripts/strip_vendor_text.py demo/my_device.json

Descriptions are the vendor's copyright. The replay needs names, OIDs, types and ranges (facts), not the prose,
so this keeps descriptions only for modules with an open licence (IETF RFC text and the net-snmp project's MIBs).
Run it on any recording that is going into the repository; tests/test_intake.py checks every file in demo/.
"""
import json
import sys
from pathlib import Path

OPEN = {"HOST-RESOURCES-MIB", "LM-SENSORS-MIB"}


def is_open(module):
    return module in OPEN or module.startswith(("UCD-", "NET-SNMP-"))


def strip(session):
    n = 0
    for c in session.get("cands", []):
        if c.get("description") and not is_open(c.get("module", "")):
            c["description"] = ""
            n += 1
    return n


if __name__ == "__main__":
    for path in sys.argv[1:] or sys.exit(__doc__):
        d = json.loads(Path(path).read_text())
        n = strip(d)
        Path(path).write_text(json.dumps(d, indent=1))
        print(f"{path}: removed description text from {n} object(s)")
