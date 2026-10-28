# OnboardIQ

[![tests](https://github.com/CipherPRG/onboardiq/actions/workflows/tests.yml/badge.svg)](https://github.com/CipherPRG/onboardiq/actions/workflows/tests.yml) License: Apache-2.0

An agent that onboards an SNMP device into monitoring without a hand-made template: Muse Spark reads the device's MIB text and picks the object for each health family (CPU, memory, disk, temperature, fan); the agent then tests every pick on the device (existence, type and range, a rise under load, an independent second reading) and monitors only what passed. The 0-100 health score uses only proven readings, and readings that leave a row's own normal are flagged. When the score moves, Muse Spark Open explains why in plain English, and code checks that every number in the explanation appears in the readings.

**What has and has not been shown.** Live on two devices: a Linux machine (net-snmp) and a MikroTik RouterOS 7 virtual router; neither serves temperature or fan sensors. Accuracy numbers come from recorded walks of real devices (LibreNMS test recordings), not live hardware. A family with nothing provable is reported as unproven or not supported and left out of the score.

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

MIB files are not stored in this repo (vendor MIBs have their own licences). To onboard a device, put its MIB files in a folder
and convert them to JSON with the bundled intake script (uses pysmi's `mibdump`, installed by `requirements.txt`):

```bash
.venv/bin/python scripts/intake.py ~/vendor_mibs --out ~/data/mibs_json
.venv/bin/python scripts/intake.py ~/vendor_mibs --out ~/data/mibs_json --only HOST-RESOURCES-MIB,UCD-SNMP-MIB
```

The open IETF/net-snmp MIBs (HOST-RESOURCES-MIB, UCD-SNMP-MIB, LM-SENSORS-MIB) usually ship with net-snmp (look in `/usr/share/snmp/mibs` after `apt install snmp`).
To only watch a recorded session, no MIBs, key or device are needed (see "Judge mode" below).

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

## Judge mode: one command, no key, no accounts

```bash
docker compose up --build            # then open http://localhost:8765 (reachable from this machine only)
docker compose run --rm oiq verify   # offline tests + a keyless replay, then exit
```
The container runs the web page, a real net-snmp agent on 127.0.0.1 inside it (the device that gets onboarded and
monitored), the offline tests and the replays. Only the open IETF/net-snmp MIBs are bundled (HOST-RESOURCES, UCD-SNMP,
LM-SENSORS); vendor MIBs never are. To run a live onboarding inside the container, put your own key in a `.env` file
next to `docker-compose.yml` (`MODEL_API_KEY=...`); without one, recorded sessions replay with their recorded model answers.

### Without Docker: replay a real session (no device, no API key)

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
python -m http.server 8765 --directory web --bind 127.0.0.1   # replay only: open http://127.0.0.1:8765
```
The page runs no third-party scripts (Content-Security-Policy `script-src 'self'`) and inserts every string
from a session as text, never as HTML.

## Live mode (local server)

```bash
python -m onboardiq.server --port 8766   # live mode: open http://127.0.0.1:8766 (stop the replay server first, or use another port)
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

## Alerts and the explanation card

When a device's level changes, `scripts/monitor.py --alert` (and the **Monitor live** panel) send one email, and one
more when it recovers: score, per-family table, the explanation card and what to check next. A cooldown stops a
flapping device filling an inbox; a move to a worse level is always sent. SMTP settings come from the environment
only (`OIQ_SMTP_HOST`, `OIQ_SMTP_PORT`, `OIQ_SMTP_USER`, `OIQ_SMTP_PASS`, `OIQ_ALERT_TO`; for Gmail use an app
password you create yourself). Without SMTP, each alert is written to `outbox/` as an HTML file you can open.

The explanation card (headline, what happened, why, what to check) is written by Muse Spark Open from the measured
facts only. Code checks that every number in the card occurs in those facts (it does not judge whether the wording is apt) and rejects a card that is cut off or not plain prose; then a
template card from the same facts is shown and labelled. "What to check" is chosen by code from a fixed list per
family, never written by the model.

## Models
Every model called at runtime is a Meta model: Muse Spark 1.3 (Meta Model API) reads MIBs, and Muse Spark Open (served on Baseten, or Fireworks) writes the explanations. `onboardiq/config.py` prints a warning if any other model name is configured.

## Second opinion from the open model

`python scripts/model_audit.py` asks Muse Spark Open (Baseten) to review this repository against the hackathon rubric
and writes `results/model_audit.md`. It is a second opinion to check against the code, not a verdict.

## License

Apache License 2.0 (see `LICENSE`). Third-party data and libraries keep their own licences: LibreNMS test
recordings and definitions (GPL) are downloaded by `scripts/build_benchmark.py` for evaluation only and are
never committed; vendor MIB files are never committed or bundled.
