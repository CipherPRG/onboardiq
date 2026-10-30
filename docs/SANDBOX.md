# Hosted sandbox (optional)

A public, locked copy of the live page: a visitor clicks one button and the real agent onboards a demo device that
lives inside the container (a read-only net-snmp agent), with the real model and a real CPU and memory load test.
Everything else (replays, tests) works without it. The sandbox is off unless `OIQ_SANDBOX=1`.

## What is locked, and why
| Lock | Why |
|---|---|
| The browser's request body is ignored. Target (the container's own agent), MIB modules, device name and load mode are fixed in `onboardiq/server.py` (`sandbox_options`). | Nothing a visitor types can reach SNMP, a shell or the model prompt. |
| One run at a time; pause between runs (`OIQ_SANDBOX_COOLDOWN`, 60 s); runs per process (`OIQ_SANDBOX_MAX_RUNS`, 40). | Bounds how fast and how often money can be spent. |
| Runs per visitor address per day (`OIQ_SANDBOX_PER_IP`, 3). The address is the one our own proxy appended to `X-Forwarded-For`, never the part the visitor writes; IPv6 is grouped by /64. | One person cannot use the whole budget. |
| Dollar budget: `OIQ_DAILY_USD` stops a call that could break the cap, and a run does not start with less than `OIQ_SANDBOX_MIN_BUDGET` ($0.30) left. Per-run call cap `OIQ_MAX_CALLS_PER_RUN`. | A run is never started that cannot finish. |
| Kill switch `OIQ_PAID=off`: no run starts and no model call is made; the page says the demo is paused and the recorded sessions still play. | Stops spending in seconds if anything looks wrong. |
| No monitor mode, no saved results, no recordings, no outbox, no uploads. | Nothing a visitor does is stored, and nothing is left to read back. |
| Model called fresh every run (`OIQ_NO_CACHE=1`). | A run is a live run, never a cached answer. |
| Run ids are random; at most 8 readers per run; requests time out after 30 s; bodies are capped at 64 KB; a bad `Content-Length` is refused. | Closes guessable ids and slow-client tricks. |
| Headers on every response: a strict Content-Security-Policy (own scripts only, no framing, no outside connections), `X-Frame-Options: DENY`, `nosniff`, no referrer, HSTS, cross-origin isolation. | A bug in the page cannot load outside code, and the page cannot be embedded in another site. |
| `Host` must be this service's name; state-changing requests need `POST`, JSON, a custom header and a same-origin `Origin`. | Stops DNS rebinding and cross-site requests from a visitor's browser. |
| Errors shown to visitors carry no paths or internals; the real error goes to the server log. | No information to probe with. |
| The container runs as an unprivileged user (`oiq`); the agent listens on port 1161 inside it, on 127.0.0.1 only, with a read-only community. | A bug in the server cannot become root in the container. |
| The API key lives only in the host's secret store, never in the image or the repository. | Nothing to leak from the image. |

Options (all have safe defaults): `OIQ_SANDBOX_EFFORT` (model reasoning effort; default same as `OIQ_EFFORT`, "high",
the setting the published accuracy numbers were measured at), `OIQ_SANDBOX_ATTEMPTS` (model tries per metric, default 3),
`OIQ_TRUSTED_HOPS` (proxies that append to `X-Forwarded-For`, default 1 for Cloud Run).

## Deploy on Google Cloud Run (you create the account, project and key)
Use a model key created only for this demo, so it can be revoked without touching anything else.
```bash
gcloud auth login && gcloud config set project YOUR_PROJECT
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com
read -rs -p "Meta API key: " K; echo; printf %s "$K" | gcloud secrets create oiq-model-key --data-file=-; unset K
gcloud run deploy onboardiq-demo --source . --region asia-south1 \
  --execution-environment gen2 --cpu 2 --memory 1Gi --min-instances 1 --max-instances 1 --concurrency 20 --timeout 900 \
  --allow-unauthenticated \
  --set-env-vars OIQ_SANDBOX=1,OIQ_NO_CACHE=1,OIQ_DAILY_USD=3,OIQ_MAX_CALLS_PER_RUN=300,OIQ_SANDBOX_MAX_RUNS=40,OIQ_SANDBOX_PER_IP=3 \
  --set-secrets MODEL_API_KEY=oiq-model-key:latest
```
- `gen2` so `/proc` shows real CPU and memory counters to the agent.
- `--max-instances 1` keeps one run at a time and one set of counters. `--min-instances 1` keeps that instance alive,
  so the run, per-visitor and dollar counters are not reset by a scale-to-zero restart (they live in the instance).
  It costs a few dollars a week; delete the service when judging ends.
- `--concurrency 20` caps simultaneous requests, so a flood of slow connections cannot exhaust the server.
- Set a billing budget with alerts in the Google Cloud console (Billing, Budgets & alerts), as a second guard.

## Pause, resume, stop
```bash
# pause all model spending at once (a new revision starts within seconds; the page shows "paused", replays still work)
gcloud run services update onboardiq-demo --region asia-south1 --update-env-vars OIQ_PAID=off
# resume
gcloud run services update onboardiq-demo --region asia-south1 --update-env-vars OIQ_PAID=on
# take it down for good
gcloud run services delete onboardiq-demo --region asia-south1
```
Pausing starts a new instance, which also resets the counters; if the demo looks abused, delete the demo key too.

## Known limits
- A determined attacker with many addresses can still use up the day's budget (the demo then shows recorded sessions
  only). The caps make that cost at most `OIQ_DAILY_USD`; they do not prevent it. The kill switch and the dedicated key
  are the response.
- The counters live in the running instance, not in a database.
