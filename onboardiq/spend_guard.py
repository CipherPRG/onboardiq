"""Hard spend limits in code.

The Meta Model API "spend limit" only sends an email; charges past the free
credits go to the card. Every paid call must pass SpendGuard.check() first and
SpendGuard.record() after. Limits come from the environment:

  OIQ_PAID=off            block every paid call
  OIQ_DAILY_USD=3         dollars per UTC day on this machine (Meta Model API)
  OIQ_BASETEN_DAILY_USD=3 dollars per UTC day on Baseten (separate credit, separate ledger)
  OIQ_MAX_CALLS_PER_RUN=40  paid calls per process and provider
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import CACHE_DIR

try:
    import fcntl
except ImportError:      # Windows: no file lock; one process at a time is assumed
    fcntl = None

# Baseten "meta-models/muse-spark-open-1026" is deliberately NOT listed: its
# price is unconfirmed, so it is charged at UNKNOWN_PRICE until verified.
# USD per 1M tokens: (input, output, cached input). Unknown models get a
# deliberately high price so estimates err on the safe side.
PRICES = {
    "muse-spark-1.3": (1.25, 4.25, 0.15),
    "muse-spark-1.2": (1.25, 4.25, 0.15),
    "muse-spark-1.1": (1.25, 4.25, 0.15),
    "muse-spark-1.3-contributor": (0.10, 0.20, 0.002),
    "muse-spark-1.2-contributor": (0.10, 0.20, 0.002),
    # Fireworks Muse Spark Open: $1.25 in / $4.25 out per 1M as listed in the account (no cached-token
    # discount assumed, so the estimate errs high). Confirm in the dashboard.
    "accounts/fireworks/models/muse-spark-open": (1.25, 4.25, 1.25),
}
UNKNOWN_PRICE = (5.0, 15.0, 5.0)
HARD_CEILING_USD = 25.0
HARD_CEILING_CALLS = 500


class SpendLimitError(RuntimeError):
    pass


def _price(model: str):
    return PRICES.get(model, UNKNOWN_PRICE)


def cost_usd(model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    p_in, p_out, p_cached = _price(model)
    fresh = max(input_tokens - cached_tokens, 0)
    return (fresh * p_in + cached_tokens * p_cached + output_tokens * p_out) / 1_000_000


def _read_limit(name: str, default: float, ceiling: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError:
        raise SpendLimitError(f"{name}={raw!r} is not a number; refusing to run")
    if value < 0 or value > ceiling:
        raise SpendLimitError(f"{name}={value} is outside 0..{ceiling}; refusing to run")
    return value


class SpendGuard:
    def __init__(self, state_file: Path | None = None, provider: str = "meta"):
        # each provider is a different account and credit, so each has its own ledger and limit
        default = CACHE_DIR / ("spend.json" if provider == "meta" else f"spend_{provider}.json")
        self.state_file = Path(state_file or default)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        env = "OIQ_DAILY_USD" if provider == "meta" else f"OIQ_{provider.upper()}_DAILY_USD"
        self.daily_usd = _read_limit(env, 3, HARD_CEILING_USD)
        self.max_calls = int(_read_limit("OIQ_MAX_CALLS_PER_RUN", 40, HARD_CEILING_CALLS))
        self.paid_on = os.environ.get("OIQ_PAID", "on").lower() != "off"
        self.calls_this_run = 0

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load(self, f) -> dict:
        f.seek(0)
        text = f.read()
        state = json.loads(text) if text.strip() else {}
        if state.get("date") != self._today():
            state = {"date": self._today(), "usd": 0.0, "calls": 0}
        return state

    def _locked(self):
        self.state_file.touch(exist_ok=True)
        f = open(self.state_file, "r+")
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
        return f

    def spent_today(self) -> dict:
        with self._locked() as f:
            return self._load(f)

    def check(self, model: str, est_input_tokens: int, max_output_tokens: int) -> float:
        """Raise SpendLimitError if this call could break a limit. Returns the worst-case cost."""
        if not self.paid_on:
            raise SpendLimitError("OIQ_PAID=off: paid calls are disabled")
        if self.calls_this_run >= self.max_calls:
            raise SpendLimitError(f"call cap reached for this run ({self.max_calls})")
        worst = cost_usd(model, est_input_tokens, max_output_tokens)
        with self._locked() as f:
            state = self._load(f)
        if state["usd"] + worst > self.daily_usd:
            raise SpendLimitError(
                f"daily cap: spent ${state['usd']:.4f} today, this call could add ${worst:.4f}, "
                f"cap ${self.daily_usd:.2f}"
            )
        return worst

    def record(self, model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
        cost = cost_usd(model, input_tokens, output_tokens, cached_tokens)
        self.calls_this_run += 1
        with self._locked() as f:
            state = self._load(f)
            state["usd"] = round(state["usd"] + cost, 6)
            state["calls"] += 1
            f.seek(0)
            f.truncate()
            json.dump(state, f)
        return cost
