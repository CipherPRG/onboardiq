# Security

OnboardIQ reads devices over SNMP and calls a hosted model. This page says what is protected, how, and what is not.

## What it never does
- **Never writes to a device.** The SNMP client is read-only (`snmpget` / `snmpbulkwalk`). No `set`, no traps.
- **Never runs model output.** The model can only choose among object identifiers that are in the MIB modules you
  gave it (`in-mib` check), and only numeric OIDs reach the command line (`check_oid`, with `--` before the target),
  so a model answer, or MIB text written to steer it, cannot become a command option or a shell command.
- **Never renders model or device text as HTML.** The page builds every element with `textContent`. Alert emails are
  escaped and served with a script-free, sandboxed policy.
- **Never puts credentials on a command line.** SNMP communities and v3 passphrases go into a private temporary
  config file (0600, in a 0700 folder) that is deleted when the process exits, are never echoed to the page, never
  logged and never recorded.

## The local live server (`python -m onboardiq.server`, `docker compose up`)
- Listens on 127.0.0.1 only (inside the container on 0.0.0.0, published to the host's 127.0.0.1 by
  `docker-compose.yml`). The `Host` header must name 127.0.0.1 or localhost (stops DNS rebinding).
- State-changing requests need `POST`, JSON, a custom header and a same-origin `Origin`, so another website open in
  the same browser cannot start a run.
- Bodies capped at 64 KB, every field validated, one run at a time, load tests only against the machine's own agent.
- **MIB upload** (`/api/mibs`, local server only): at most 25 files of 4 MB, sent as JSON with the same checks as
  every other request. File names are only echoed back, never used as paths; the text must define a MIB module. It is
  converted locally with pysmi (no downloads, nothing executed) into the MIB folder, and kept in `mibs_src` next to it
  so later uploads can import from it. Both folders stay on this machine and outside the repository.
- Responses carry a strict Content-Security-Policy (own scripts only, no framing), `nosniff`, no referrer.
- **Keep the port on 127.0.0.1.** `docker run -p 8765:8765` (without `127.0.0.1:`) publishes it to your network,
  where the `Host` check is not authentication: anyone on that network could start runs with your API key.

## The hosted sandbox (`OIQ_SANDBOX=1`)
Ignores the request body: target, MIB modules and load are fixed in code. Per-visitor daily limit, cooldown, run cap,
dollar budget with a pre-check, kill switch (`OIQ_PAID=off`), nothing saved to disk, random run ids, request timeouts,
unprivileged container user. Details and the known limits: [docs/SANDBOX.md](docs/SANDBOX.md).

## Spending
Every paid call passes `spend_guard` first: a daily dollar cap per provider (`OIQ_DAILY_USD`), a per-run call cap and
a hard ceiling in code. The provider's own "spend limit" only sends an email, so these caps are the real guard.

## What leaves your machine
To pick objects, the model provider receives the device description you type, the reduced MIB candidate lines
(object names, types and description text from the MIB files you supplied) and, for re-reads, the readings that
disproved a pick. Discovery uses the device's own description (`sysDescr`, which on Linux includes its host name) and
`sysObjectID` as the device text; photo identification (`scripts/identify.py`) sends the photo you give it. In monitor
mode with explanations on, the provider also receives the readings being explained and the `sysDescr`. SNMP
credentials and the target address are never sent. Use a provider and key you are allowed to send that data to.

## Supply chain
Python dependencies are pinned to exact versions. The container's open MIB files come from a pinned net-snmp release
and are checked against SHA-256 sums (`scripts/fetch_open_mibs.py`). Vendor MIBs are never bundled or fetched.
`.dockerignore` and `.gcloudignore` keep `.env*`, private MIB folders and local results out of images and uploads.

## Reporting
Open a private security advisory on the GitHub repository (Security tab, "Report a vulnerability").
