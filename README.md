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

## Judge mode: replay a real session (no device, no API key)

```bash
pip install -r requirements.txt
python scripts/onboard.py --replay demo/laptop_hostresources.json
```
This replays a recorded live onboarding of a Linux host (net-snmp) given only HOST-RESOURCES-MIB:
Muse Spark picks `hrMemorySize` (total installed RAM) for memory, the verifier allocates 1 GiB and sees
+0 bytes, rejects it, Muse Spark re-reads with that evidence and picks `hrStorageUsed`, and the verifier
finds the RAM row and measures +1.1 GB: proven. Every SNMP response and model answer comes from the
recording, served to the same verifier step in the same order, on a virtual clock.

Record your own: `python scripts/onboard.py --device "..." --modules ... --lab --record demo/mine.json`

## Security and privacy

- **Read-only:** only SNMP GET/WALK; the code has no SET path.
- **No secrets in the repo:** keys live in `.env` (gitignored, mode 600); recordings keep no host, community or local paths.
- **No secrets on command lines:** the SNMP community or v3 credentials go into a private `snmp.conf` (dir 0700, file 0600) read by net-snmp through `SNMPCONFPATH`, deleted at exit; `ps` never shows them.
- **Untrusted model output:** only numeric OIDs reach net-snmp (strict regex, `--` ends option parsing); hosts are validated; anything else is rejected before touching the device.
- **Lab experiments** (CPU/memory load) only run against the machine's own agent.
- **What leaves the network:** MIB text, the device description and verifier readings go to the model provider (Meta API, or Baseten for open weights). Credentials never do.
- **Spend limits** in code (daily cap, per-run cap, off switch); dependencies pinned.
- SNMPv2c sends the community in clear text on the wire; use SNMPv3 (`authPriv`) on real networks.
