# Build log

## 2026-10-27 (IST)
- Repo created after the hackathon kickoff.
- Live test device: net-snmp agent on the developer laptop (WSL2 Ubuntu).
- Manual experiment: 2-core CPU stress raised ssCpuRawUser from 1,392 to 6,327 and the 1-minute load from 0.05 to 0.71 within 25 s; the 5-minute load barely moved.
- hrProcessorLoad (HOST-RESOURCES-MIB) is documented but not served on this device.
- Overnight prep on the laptop: Python venv, LibreNMS MIBs and device definitions downloaded, 3,891 MIB modules converted to JSON with pysmi (finished 03:32 IST); 9-hour CPU/load baseline log of the laptop device running.
- Mapped LibreNMS device definitions as a future benchmark: 6,721 labelled sensor OIDs in 466 files, 1,699 with a scaling divisor, 721 distinct MIBs.
- Added spend guard (daily cap, per-run cap, off switch), cached Meta Model API client, MIB JSON loader, smoke test, offline tests (5 passing).
- Day-1 accuracy test with ground truth for Cisco, Fortinet and Linux taken from the public MIBs. Found: HUAWEI-ENTITY-EXTENT-MIB fails to convert (HUAWEI-MIB missing from the LibreNMS set), so Linux (net-snmp) replaces Huawei.
- Keyword-parser baseline (no AI): 1/12 correct. It picked Fortinet's data-plane-only CPU, Linux's deprecated ssCpuIdle, a Cisco notification-enable flag as "temperature", and hrSystemProcesses as "temperature" (sys-TEMP-rocesses).
