# OnboardIQ

[![tests](https://github.com/CipherPRG/onboardiq/actions/workflows/tests.yml/badge.svg)](https://github.com/CipherPRG/onboardiq/actions/workflows/tests.yml) License: Apache-2.0

**An agent that onboards a network device into monitoring by reading its manual, and lets the device check every answer.**

Muse Spark reads a device's MIB files and picks the object to monitor for each health metric (CPU, memory, disk,
temperature, fan). OnboardIQ then tests every pick on the device itself: does it exist, is the type and range right,
does it move the way the model predicted under real load, does an independent object agree. A pick that fails goes
back to the model with the measured evidence, and the model reads again. Only proven picks are monitored, scored
0-100 and explained in plain English. Code checks that every number in the explanation comes from the readings.

Built for the Meta Global AI Developer Hackathon (Oct 26 - Nov 4, 2026), Indie track.

## Try it

| | What you get | What you need |
|---|---|---|
| **Replay in the browser** ([cipherprg.github.io/onboardiq](https://cipherprg.github.io/onboardiq/)) | The story page and the console, playing recordings of real runs | Nothing |
| **Run it yourself** (`docker compose up --build`) | The full console: onboard any device you can reach, upload its MIB files, monitor it live, export every event | Docker; a Meta API key for live runs (recordings play without one) |
| **Hosted live demo** ([onboardiq-demo…run.app/console](https://onboardiq-demo-430342543451.asia-south1.run.app/console/)) | The real agent, live, on a fixed demo device: onboard it, then monitor it under a load you pick; limited runs per visitor | Nothing (see [docs/SANDBOX.md](docs/SANDBOX.md)) |

```bash
git clone https://github.com/CipherPRG/onboardiq && cd onboardiq
echo "MODEL_API_KEY=..." > .env          # optional: without it, recordings still replay
docker compose up --build                # open http://127.0.0.1:8765/console/ (this machine only)
docker compose run --rm oiq verify       # offline tests and a keyless replay, then exit
```
The container holds the web page and console, the agent, the tests and a real net-snmp agent on 127.0.0.1 inside it.
That agent is the device you can onboard and monitor straight away: in **Onboard device…** keep host `localhost`,
modules `HOST-RESOURCES-MIB,UCD-SNMP-MIB` and choose "Load this machine". Only the open IETF/net-snmp MIBs are bundled;
for any other device, add its MIB files in the dialog (converted locally, never uploaded anywhere else).
Onboarded devices are kept in a Docker volume, so they survive a rebuild; `docker compose down -v` clears them.

## How a device is onboarded

1. **Ask the device.** One read-only SNMP query (`sysDescr`, `sysObjectID`) says what it is.
2. **Read the manual.** Its MIB files are converted locally (pysmi, with automatic repairs for common vendor mistakes)
   and reduced to the candidate objects for each metric.
3. **Pick.** Muse Spark 1.3 picks one object per metric, quotes the MIB line it relied on and predicts how the value
   should move under load.
4. **Test on the device.** Existence, type, range and units; then a baseline, real CPU or memory load, and a check
   that the value moved the predicted way; or an independent second object that must agree.
5. **Correct.** A failed pick goes back to the model with the readings that disproved it; it picks again (up to three
   attempts). What cannot be proven is reported as unproven or not on the device, never guessed.
6. **Score and explain.** Proven objects are polled into a 0-100 health score per metric and overall. When the level
   changes, Muse Spark Open writes a short explanation; code rejects it if any number in it is not in the readings,
   and falls back to a labelled template. "What to check next" comes from a fixed list per metric, not the model.

A recorded example (`laptop_full`): the model's first CPU pick, `ssCpuRawIdle`, was meant to rise under load. On the
device its rate fell from 1197/s to 1.5/s, so it was rejected; the model re-read with that evidence and picked
`ssCpuRawUser`, which rose under load and was proven.

## The console

`console/` is a React, TypeScript and Vite app (Tailwind, TanStack Table and Virtual, Recharts; exact versions in
`console/package-lock.json`). Everything it shows is real: live runs stream from the server, recordings replay at
their recorded timing (1×, 4×, 16× or instantly). Nothing is simulated.

- **Run pane:** a status line saying what the agent is doing now (reading the MIB, testing a pick, load test,
  asking again, finished), then the inputs (device, MIB text, model) and, per metric, first pick → asked again →
  verdict, coloured by what the device measured.
- **Object table:** every pick with its value at rest, under load, the change and the verdict. Hover for the OID.
- **Inspector:** follows the pick being tested: the live measurements, the MIB line the model quoted, every check,
  the re-read reasoning. Click any row to look at an earlier pick.
- **Health view:** a plain verdict (Healthy, Under strain, In trouble), each metric's reading against its limits,
  the score over time, the latest explanation and the alerts sent.
- **Filter** (⌘K): `family:cpu verdict:rejected object:ssCpu oid:2021 kind:verify attempt:2` and free text.
- **Raw events** (folded): every event exactly as it arrived, with word wrap, follow and **Export JSON**.
- A refused action (cooldown, limit, run in progress) appears as a popup and leaves the screen as it was. All times
  are UTC.

The same console runs everywhere and shows what the server behind it allows: recordings only (static hosting), one
fixed live run plus a fixed four-minute monitor with its own CPU and memory load (hosted demo; one run at a time, others watch it live), or the full onboard, upload and monitor tools (your own server).

## What has been shown, and what has not

- **Live** on two devices: a Linux machine (net-snmp) and a MikroTik RouterOS 7 virtual router. Neither serves
  temperature or fan sensors, so those metrics are reported as not on the device.
- **Accuracy figures** come from recorded walks of real devices (LibreNMS test recordings) replayed through the same
  verifier, not from live hardware of every vendor.
- **Recordings are recomputed, not stored.** A health recording keeps every raw SNMP answer; replaying it runs the
  same scoring, anomaly and explanation-check code, so editing one raw number changes the replayed score
  (`tests/test_monitor_raw.py` does exactly that).
- **Cost:** a full onboarding run with Muse Spark 1.3 costs about $0.25-0.30 in model calls (measured: $0.27 for one
  live run, 6 calls). Every call passes a daily dollar cap, a per-run cap and an off switch in code.

## Models

Every model called at runtime is a Meta model: **Muse Spark 1.3** (Meta Model API) reads MIBs and picks objects;
**Muse Spark Open** (Baseten, or Fireworks) writes the health explanations. `onboardiq/config.py` warns if any other
model is configured.

## Security

Read-only SNMP (no SET path); model output can only choose among numeric OIDs from your MIB files and never reaches a
shell; SNMP credentials never reach a command line, a log or a recording; the server listens on 127.0.0.1, checks
Host and Origin, and sends a strict Content-Security-Policy; the hosted demo ignores the request body and runs a fixed
target with per-visitor, cooldown and dollar limits. Details, what leaves your machine, and how to report a problem:
[SECURITY.md](SECURITY.md).

## Without Docker (WSL2 or Linux)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                          # put MODEL_API_KEY in it
.venv/bin/python -m pytest -q tests           # offline tests, no API calls
(cd console && npm ci && npm run build)       # builds the console into web/console
.venv/bin/python scripts/intake.py ~/vendor_mibs --out ~/data/mibs_json   # convert a folder of MIB files
.venv/bin/python -m onboardiq.server          # open http://127.0.0.1:8765/console/
```
Command line: `scripts/onboard.py` (onboard, `--record`, `--replay`), `scripts/monitor.py` (score, `--alert`,
`--record-raw`, `--replay-raw`), `scripts/accuracy_test.py` and `scripts/build_benchmark.py` (benchmarks).

## Alerts

When a device's level changes, the monitor writes one alert (and one when it recovers) with the score, the per-metric
table, the explanation and what to check. The Monitor dialog has a **Send the alert email to** box.

- **Without a mail account** (the default): alerts appear in the console's right panel, labelled "not emailed", with
  a link to the email exactly as it would be sent. Nothing leaves your machine.
- **With a mail account:** add SMTP settings to `.env` (never in the web page) and the same alert is emailed:
  ```
  OIQ_SMTP_HOST=smtp.gmail.com
  OIQ_SMTP_PORT=587
  OIQ_SMTP_USER=you@gmail.com
  OIQ_SMTP_PASS=...        # for Gmail, an app password you create yourself
  OIQ_ALERT_TO=you@gmail.com
  ```
  Then `docker compose up` again. At most 3 recipients per monitor, and the hosted demo never sends email.

## Export to Prometheus and Zabbix

An onboarding can be handed to the monitoring a team already runs. In the console, each onboarded device has
**Export: Prometheus · alert rules · Zabbix**; on the command line, `python scripts/export.py results/onboard_*.json`.

| File | What it is |
|---|---|
| `snmp.yml` | A ready [snmp_exporter](https://github.com/prometheus/snmp_exporter) config: one module with the proven objects (scale, row names, per-core and per-disk rows). No MIBs or generator needed. |
| `onboardiq.rules.yml` | Prometheus alert rules on the rows the onboarding kept (RAM only, real disks) |
| `zabbix_template.yaml` | A Zabbix template (6.0 format, imports into 6.0 and later): items, discovery for tables, calculated "used %", triggers and macros |

Only proven objects and the standard ones the health score uses are exported. The alert limits are not new
numbers: they are the readings at which OnboardIQ's own health score turns "Under strain" or "In trouble"
(CPU 80% and 92%, memory and disk 86.25% and 95%, temperature about 69 °C and 81 °C under the default policy), so
Prometheus, Zabbix and the console agree.

When an onboarding finishes with proven metrics, the console shows the three files right under the run, on the
hosted demo too (the hosted files describe the fixed demo device and come from memory; nothing is stored).

**Checked against real tools (Nov 2, 2026):** the alert rules pass `promtool check rules` (Prometheus 2.53); a real
`snmp_exporter` v0.28.0 loaded the generated `snmp.yml` and scraped a live net-snmp agent (memory in bytes, CPU
counter); the Zabbix template imported into Zabbix 6.0 ("Imported successfully") after one fix found by that test
(the template name had to avoid characters Zabbix forbids in host names). Not yet checked: other Zabbix or
snmp_exporter versions, and receiving the alerts in Alertmanager or Zabbix actions.

## Repository

| Path | What |
|---|---|
| `onboardiq/` | The agent and server (map below) |
| `console/` | The React console |
| `web/` | The story page; `web/sessions/` holds the recordings both pages play (generated by `scripts/export_playback.py`) |
| `demo/` | The recorded sessions, as captured (MIB prose stripped) |
| `tests/` | Offline tests: recorded sessions replayed through the real verifier, security and sandbox checks |
| `scripts/` | Command-line tools: onboarding and monitoring, MIB fetch and intake, benchmarks and evaluations |
| `eval/` | The day-1 answer key used by the accuracy test |
| `docs/SANDBOX.md` | The hosted demo: its locks and how to deploy, pause and delete it |

`onboardiq/`, by layer:

| Layer | Modules |
|---|---|
| Device access | `snmp.py` (read-only net-snmp), `recorded.py` (a recorded walk as a device), `discover.py` (what is this device, which modules it serves), `identify.py` (from a photo) |
| MIB text | `intake.py` (convert a folder of MIBs), `mibfix.py` (narrow repairs), `mib.py` (load and pick candidates) |
| Model | `llm.py` (Meta Model API client, cache), `spend_guard.py` (hard dollar caps), `reader.py` (Muse Spark picks), `explain.py` (Muse Spark Open explanations, number-checked) |
| Proof | `verify.py` (static, experiment, witness checks), `agent.py` (pick → verify → re-read loop), `profile.py` (what is proven and how to read it) |
| Health | `health.py` + `health_policy.json` (0-100 score, plain code), `monitor.py` (poll, anomalies, explanations), `alerts.py` (email) |
| Recording | `replay.py` (record and replay without a device or key), `playback.py` (files for the web pages), `report.py` (HTML certificate) |
| Serving | `server.py` (local server and hosted demo, one code path, locks in `SANDBOX` mode), `config.py` (settings) |

Everything here was written during the hackathon (on or after Oct 27, 2026, IST); see `BUILD_LOG.md` and the git
history.

## License

Apache License 2.0 (see `LICENSE`). Third-party data keeps its own licence: LibreNMS test recordings and definitions
(GPL) are downloaded by `scripts/build_benchmark.py` for evaluation only and never committed; vendor MIB files are
never committed or bundled.
