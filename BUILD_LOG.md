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
- First live API call: muse-spark-1.3 replied "ready" for $0.0005.
- Answer-key correction (made after seeing a model answer, recorded for transparency): Muse Spark chose CISCO-MEMORY-POOL-MIB::ciscoMemoryPoolUtilization5Min (1.3.6.1.4.1.9.9.48.1.2.1.2) for Cisco memory. The MIB defines it as a current, device-wide memory utilisation percentage, so the 1/5/10-minute utilisation objects were added to the accepted set.
- Day-1 accuracy run (muse-spark-1.3, $0.24): keyword parser 1/12, Muse Spark without the MIB 3/12, Muse Spark reading the MIB 9/12.
- Scoring corrections after reviewing every miss against the MIB text (logged for transparency): Linux memTotalRealX/memAvailRealX (current 64-bit versions, kB) added to the accepted set; scalar instance suffix ".0" now accepted when the object is correct. Kept as a real model error: Fortinet fan -> fgHwSensorEntAlarmStatus, a true/false threshold flag rather than a fan reading.
