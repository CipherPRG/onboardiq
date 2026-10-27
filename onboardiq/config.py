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
}
OPEN_MODEL = os.environ.get("OIQ_OPEN_MODEL", "meta-models/muse-spark-open-1026")


def provider_for(model: str) -> str:
    return "baseten" if model.startswith("meta-models/") else "meta"
