"""Settings from the environment, with a tiny .env loader (no extra dependency)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

# Ubuntu/WSL: the httpx2 transport used by openai>=3 failed TLS verification
# (CERTIFICATE_VERIFY_FAILED) on a machine where openssl and httpx both verify
# api.meta.ai fine. Point it at the OS trust store. Verification stays on.
_SYSTEM_CA = "/etc/ssl/certs/ca-certificates.crt"
if "SSL_CERT_FILE" not in os.environ and os.path.exists(_SYSTEM_CA):
    os.environ["SSL_CERT_FILE"] = _SYSTEM_CA

API_BASE = os.environ.get("OIQ_API_BASE", "https://api.meta.ai/v1")
MODEL = os.environ.get("OIQ_MODEL", "muse-spark-1.3")
CACHE_DIR = Path(os.environ.get("OIQ_CACHE_DIR", ROOT / "cache"))

# Providers, chosen by model name: "meta-models/..." runs on Baseten
# (open-weights Muse Spark), everything else on the Meta Model API.
PROVIDERS = {
    "meta": {"base": API_BASE, "key_env": "MODEL_API_KEY"},
    "baseten": {"base": os.environ.get("OIQ_BASETEN_BASE", "https://inference.baseten.co/v1"),
                "key_env": "BASETEN_API_KEY"},
    "fireworks": {"base": os.environ.get("OIQ_FIREWORKS_BASE", "https://api.fireworks.ai/inference/v1"),
                  "key_env": "FIREWORKS_API_KEY"},
}
OPEN_MODEL = os.environ.get("OIQ_OPEN_MODEL", "meta-models/muse-spark-open-1026")
# The same open weights on a second host. The id is the one a third-party listing shows; confirm it in
# the Fireworks dashboard and set OIQ_FIREWORKS_MODEL if it differs.
FIREWORKS_OPEN_MODEL = os.environ.get("OIQ_FIREWORKS_MODEL", "accounts/fireworks/models/muse-spark-open")


def provider_for(model: str) -> str:
    if model.startswith("accounts/fireworks/"):
        return "fireworks"
    return "baseten" if model.startswith("meta-models/") else "meta"

# Plain-English explanations of health changes: the open-weights model first (Baseten; Fireworks can be put
# first with OIQ_EXPLAIN_MODEL), then Muse Spark 1.3 if the open model is out of budget or unavailable.
EXPLAIN_MODEL = os.environ.get("OIQ_EXPLAIN_MODEL", f"{OPEN_MODEL},{MODEL}")
