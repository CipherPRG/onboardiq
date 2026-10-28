# OnboardIQ

An agent that onboards any network or CCTV device into monitoring: it reads the device's MIB and manual, proves every metric on the live device, then scores its health from 0 to 100 using only what was proven and flags readings that leave a row's own normal. When the score moves, Muse Spark explains why in plain English, and every number in that explanation is checked against the readings.

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

## Web player (replays in the browser)

`web/` is a static page that plays recorded sessions: the model's pick, the measurements drawn as they were
taken, the verdict, and the model's re-read when a pick fails. No server, no API key, no device.

```bash
python scripts/export_playback.py          # demo/*.json -> web/sessions/*.json
python -m http.server 8765 --directory web --bind 127.0.0.1   # open http://127.0.0.1:8765
```
The page runs no third-party scripts (Content-Security-Policy `script-src 'self'`) and inserts every string
from a session as text, never as HTML.

## Live mode (local server)

```bash
python -m onboardiq.server        # then open http://127.0.0.1:8765
```
The same page as the replays gains a **Run live** panel: choose the MIB modules, the device and how to prove
CPU/memory picks (read-only plus a second object, load on this machine, or load you start on the device when
asked). Steps and measurements stream to the page as they happen; finished runs are recorded and appear in the
session list. The server listens on 127.0.0.1 only, checks the Host header, refuses cross-site requests, keeps the
API key in the process and the SNMP community in memory for the run only, and serves files from `web/` only.

**Monitor live.** Below it, a **Monitor live** panel polls a device you have onboarded (from this page or from
`scripts/onboard.py`) and draws the health score, the per-family breakdown, anomalies and Muse Spark's checked
explanation as readings arrive. When the device is this machine's own agent, a button puts CPU load on it for
120 seconds so you can watch the score fall and recover. A run stops after 30 minutes and can be saved as a replay.
