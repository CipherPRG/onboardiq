# OnboardIQ

An agent that onboards any network or CCTV device into monitoring: it reads the device's MIB and manual, proves every metric on the live device, then watches it and explains anomalies.

Built for the Meta Global AI Developer Hackathon (Oct 26 - Nov 4, 2026), Indie track, theme "Push the Frontier".

## Built during the hackathon
Everything in this repository was written on or after Oct 27, 2026 (IST). See BUILD_LOG.md for a dated record and the git history for proof.

## Setup (WSL2 Ubuntu)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # paste MODEL_API_KEY into .env
.venv/bin/python -m pytest -q tests      # offline tests, no API calls
.venv/bin/python scripts/smoke.py        # first API call, prints cost
```

MIB files are not stored in this repo. Convert them to JSON with pysmi's `mibdump`
(see BUILD_LOG.md) into `~/data/mibs_json`.

## Day-1 accuracy test

```bash
.venv/bin/python scripts/accuracy_test.py --modes baseline   # free, keyword parser only
.venv/bin/python scripts/accuracy_test.py                    # baseline vs Muse Spark with MIB vs without MIB
```

Ground truth (`eval/ground_truth_day1.json`) covers Cisco, Fortinet and Linux (net-snmp), taken from
the public MIB files, with deliberate traps: deprecated objects, thresholds, and per-VDOM / per-cluster
CPU objects that look right but are not device-wide.

## Spend safety

Every paid call goes through `onboardiq/spend_guard.py` (daily dollar cap, per-run call cap, `OIQ_PAID=off`
switch) and identical requests are served from a disk cache. The Meta Model API billing "spend limit"
only sends an email, so the limit is enforced in code.

## Multi-vendor benchmark

```bash
python scripts/build_benchmark.py            # needs ~/data/librenms and ~/data/mibs_json
OIQ_MAX_CALLS_PER_RUN=60 python scripts/accuracy_test.py --truth data/benchmark_librenms.json \
    --split dev --modes baseline,with-mib,no-mib --effort high
```
The answer key comes from LibreNMS device definitions (GPL) and is generated locally, not committed.
Tune on `--split dev`; report on `--split test`.
