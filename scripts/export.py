"""Export a saved onboarding to Prometheus (snmp_exporter) and Zabbix. Reads nothing from the device.

    python scripts/export.py results/onboard_20261101_141613.json                # writes to exports/
    python scripts/export.py results/onboard_20261101_141613.json --out /tmp/x --mib-dir mibs_json

Writes three files:
  snmp.yml            a ready snmp_exporter config (one module, the proven objects; no MIBs or generator needed)
  onboardiq.rules.yml Prometheus alert rules at the health score's own level cut-offs
  zabbix_template.yaml a Zabbix 6.0 template (imports into 6.0 and later): items, discovery, triggers, macros
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from onboardiq import export, mib  # noqa: E402
from onboardiq.config import ROOT  # noqa: E402


def write_all(saved, out: Path, cands=None):
    out.mkdir(parents=True, exist_ok=True)
    snmp_yml, rules = export.snmp_exporter(saved, cands)
    files = {"snmp.yml": snmp_yml, "onboardiq.rules.yml": rules, "zabbix_template.yaml": export.zabbix(saved, cands)}
    for name, doc in files.items():
        (out / name).write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120))
    return sorted(files)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("onboarding", type=Path, help="results/onboard_*.json from a live onboarding")
    ap.add_argument("--out", type=Path, default=ROOT / "exports")
    ap.add_argument("--mib-dir", type=Path, default=None, help="converted MIBs, for index names of vendor tables")
    a = ap.parse_args()
    saved = json.loads(a.onboarding.read_text())
    if not (saved.get("profile") or {}).get("metrics"):
        sys.exit("this onboarding has no device profile; onboard the device again")
    cands = None
    mods = (saved.get("args") or {}).get("modules") or ""
    if a.mib_dir and mods:
        cands = mib.candidates(a.mib_dir, [m for m in str(mods).split(",") if m])
    for name in write_all(saved, a.out, cands):
        print(a.out / name)


if __name__ == "__main__":
    main()
