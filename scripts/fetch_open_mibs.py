"""Download the few open MIB source files the container needs (net-snmp, pinned release) into a folder.

  python scripts/fetch_open_mibs.py /tmp/open_mibs

These are the IETF / net-snmp base modules (host resources, UCD, sensors, SMI). They are fetched at build time and
never stored in this repository. Vendor MIBs are never fetched.
"""
import sys
import urllib.request
from pathlib import Path

TAG = "v5.9.4"
BASE = f"https://raw.githubusercontent.com/net-snmp/net-snmp/{TAG}/mibs/"
FILES = ["HOST-RESOURCES-MIB", "HOST-RESOURCES-TYPES", "UCD-SNMP-MIB", "LM-SENSORS-MIB", "NET-SNMP-MIB", "NET-SNMP-TC",
         "SNMPv2-SMI", "SNMPv2-TC", "SNMPv2-CONF", "SNMPv2-MIB", "IF-MIB", "IANAifType-MIB", "HCNUM-TC",
         "INET-ADDRESS-MIB"]


def main(dest):
    out = Path(dest)
    out.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        with urllib.request.urlopen(BASE + name + ".txt", timeout=60) as r:
            data = r.read()
        if len(data) < 500 or b"DEFINITIONS" not in data:
            raise SystemExit(f"{name}: unexpected download")
        (out / f"{name}.txt").write_bytes(data)
    print(f"fetched {len(FILES)} open MIB files ({TAG}) into {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "open_mibs")
