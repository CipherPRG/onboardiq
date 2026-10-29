#!/usr/bin/env bash
# One command: record a raw health run of THIS machine's own SNMP agent under a CPU load you do not have to start.
#   bash scripts/record_stress_demo.sh            # about 9 minutes, then replays and exports it
# Needs: snmpd running on this machine, a live (not replayed) onboarding in results/, stress-ng, and
# BASETEN_API_KEY in .env (only for the explanation; without it the card is written from a template).
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python
export OIQ_BASETEN_DAILY_USD=${OIQ_BASETEN_DAILY_USD:-25}   # the explanation is written by Muse Spark Open on Baseten
OUT=demo/laptop_stress.raw.json
ONB=$($PY - <<'PYEOF'
import json, glob, os
for f in sorted(glob.glob("results/onboard_*.json"), key=os.path.getmtime, reverse=True):
    d = json.load(open(f))
    if "replay" not in d and (d.get("spec") or {}).get("cpu") and d.get("args", {}).get("modules"):
        print(f); break
PYEOF
)
[ -n "$ONB" ] || { echo "no live onboarding found in results/. Run scripts/onboard.py against this machine first."; exit 1; }
snmpget -v2c -c public localhost 1.3.6.1.2.1.1.3.0 >/dev/null || { echo "snmpd does not answer on localhost (sudo service snmpd start)"; exit 1; }
command -v stress-ng >/dev/null || { echo "stress-ng missing: sudo apt install stress-ng"; exit 1; }
echo "onboarding record: $ONB"; rm -f "$OUT"
( sleep 130; echo ">>> CPU load on for 120 s"; stress-ng --cpu "$(nproc)" --timeout 120s --quiet ) &
timeout 540 $PY scripts/monitor.py "$ONB" --interval 10 --explain --record-raw "$OUT" || true
wait || true
echo; echo "=== replay (recomputed from the raw answers, no device, no key) ==="
$PY scripts/monitor.py --replay-raw "$OUT" | grep -E "health|EVENT|explanation" | head -60
$PY scripts/export_playback.py >/dev/null && echo "exported to web/sessions"
ls -l "$OUT"
