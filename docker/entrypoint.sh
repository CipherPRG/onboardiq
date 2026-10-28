#!/bin/sh
# docker compose up          -> web page on http://localhost:8765 (replays, live panel, monitor)
# docker compose run --rm oiq verify   -> offline tests + a keyless replay, then exit
set -e
case "${1:-serve}" in
  verify)
    python -m pytest -q tests
    python scripts/onboard.py --replay demo/laptop_full.json
    ;;
  serve)
    snmpd -Lsd -Lf /dev/null -p /tmp/snmpd.pid -C -c /etc/snmp/snmpd.conf
    python scripts/export_playback.py >/dev/null 2>&1 || true
    echo "OnboardIQ judge mode: open http://localhost:8765"
    exec python -m onboardiq.server --port 8765 --bind 0.0.0.0 --mib-dir "$OIQ_MIB_DIR"
    ;;
  *) exec "$@" ;;
esac
