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

API_BASE = os.environ.get("OIQ_API_BASE", "https://api.meta.ai/v1")
MODEL = os.environ.get("OIQ_MODEL", "muse-spark-1.3")
CACHE_DIR = Path(os.environ.get("OIQ_CACHE_DIR", ROOT / "cache"))
