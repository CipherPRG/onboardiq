# Hosted sandbox (optional)

A public, locked copy of the live page: a visitor clicks one button and the real agent onboards a demo device that
lives inside the container (a read-only net-snmp agent), with the real model and a real CPU and memory load test.
Everything else (replays, tests) works without it. The sandbox is off unless `OIQ_SANDBOX=1`.

## What is locked
- The browser's request body is ignored. Target (the container's own agent), MIB modules, device name and load mode
  are fixed in `onboardiq/server.py` (`sandbox_options`), so nothing a visitor types reaches SNMP, a shell or the prompt.
- One run at a time, a pause between runs (`OIQ_SANDBOX_COOLDOWN`, default 60 s), a cap on runs per process
  (`OIQ_SANDBOX_MAX_RUNS`, default 40), the daily dollar cap (`OIQ_DAILY_USD`) and the per-process call cap.
- No monitor mode. The model is called fresh every run (`OIQ_NO_CACHE=1`); a run is never a cached answer.
- Errors shown to visitors carry no paths or internals; the real error goes to the server log.
- The API key lives only in the host's secret store, never in the image or the repository.

## Deploy on Google Cloud Run (you create the account, project and key)
```bash
gcloud auth login && gcloud config set project YOUR_PROJECT
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com
read -rs -p "Meta API key: " K; echo; printf %s "$K" | gcloud secrets create oiq-model-key --data-file=-; unset K
gcloud run deploy onboardiq-demo --source . --region asia-south1 \
  --execution-environment gen2 --cpu 2 --memory 1Gi --max-instances 1 --concurrency 20 --timeout 900 \
  --allow-unauthenticated \
  --set-env-vars OIQ_SANDBOX=1,OIQ_NO_CACHE=1,OIQ_DAILY_USD=3,OIQ_MAX_CALLS_PER_RUN=300,OIQ_SANDBOX_MAX_RUNS=40 \
  --set-secrets MODEL_API_KEY=oiq-model-key:latest
```
`gen2` is needed so `/proc` shows real CPU and memory counters to the agent. `--max-instances 1` keeps one run at a
time and one set of counters. Set a billing budget alert in the Google Cloud console as a second guard.

Stop it any time: `gcloud run services delete onboardiq-demo --region asia-south1`.
