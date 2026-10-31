"""Download the few open MIB source files the container needs (net-snmp, pinned release) into a folder.

  python scripts/fetch_open_mibs.py /tmp/open_mibs

These are the IETF / net-snmp base modules (host resources, UCD, sensors, SMI). They are fetched at build time and
never stored in this repository. Vendor MIBs are never fetched.
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

TAG = "v5.9.4"
BASE = f"https://raw.githubusercontent.com/net-snmp/net-snmp/{TAG}/mibs/"
FILES = ["HOST-RESOURCES-MIB", "HOST-RESOURCES-TYPES", "UCD-SNMP-MIB", "LM-SENSORS-MIB", "NET-SNMP-MIB", "NET-SNMP-TC",
         "SNMPv2-SMI", "SNMPv2-TC", "SNMPv2-CONF", "SNMPv2-MIB", "IF-MIB", "IANAifType-MIB", "HCNUM-TC",
         "INET-ADDRESS-MIB"]


# SHA-256 of each file at that release: a moved tag or a tampered download stops the build.
SHA256 = {
    "HCNUM-TC": "c8d21522e37294169277cab9a9e6e0dae85ce2a106af3e64429ece8f35b6835c",
    "HOST-RESOURCES-MIB": "0406b893dcfacc370a1ae04ccfe6ffdecb15f93e3377a1a5ded2bbde5d944165",
    "HOST-RESOURCES-TYPES": "be189dd823a76cd93bd26d7fe2e85672ac9f359121420339b7236f40896fbd0f",
    "IANAifType-MIB": "ab12e9414b91b401cc427702ef368a9c07dc5ff2f3c964a2897311fd625bbfdf",
    "IF-MIB": "a41d2d0414bd6e1b3249257d0d035ea11554624c525b830f6910195b72b0c297",
    "INET-ADDRESS-MIB": "2cec7722aa39eb8facf105b26e93d0bce4ca9f097638c094b0a4f247c86843dc",
    "LM-SENSORS-MIB": "95026d904789871177203c83b0c7d164f930fba75df77acd60a21a15a6c61db2",
    "NET-SNMP-MIB": "b0460053145ec563a026ccc7e792ab9ebb370c6aab3d57a9f95f3964cdd9d399",
    "NET-SNMP-TC": "bf111deffcc7c36262d2e47ff8fd7d49eee8a3f1bdad6236367660da6854a233",
    "SNMPv2-CONF": "b3e90ba682e10c76f6de90e65a0013143f0ec531b0ed546b2b8aa5ad5c0001eb",
    "SNMPv2-MIB": "b4f8ef130f580b86d2b6bc890564e46100785f589431df76954e878a7b166e11",
    "SNMPv2-SMI": "ece2355fc8b6140af702f86d77bd3f7398d80375fc6278c3e30ff3a31b53e0b7",
    "SNMPv2-TC": "c1379575e6a0ad25b2d7da68294153c1fd79750827376f2aa6323d072d73f0b8",
    "UCD-SNMP-MIB": "7ee1e98fc2554475bf2c8fb11885e6eb8a54c2d55dc492bd9226723de26ff9fd",
}


def main(dest):
    out = Path(dest)
    out.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        with urllib.request.urlopen(BASE + name + ".txt", timeout=60) as r:
            data = r.read()
        if hashlib.sha256(data).hexdigest() != SHA256[name]:
            raise SystemExit(f"{name}: checksum mismatch, refusing the download")
        (out / f"{name}.txt").write_bytes(data)
    print(f"fetched {len(FILES)} open MIB files ({TAG}) into {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "open_mibs")
