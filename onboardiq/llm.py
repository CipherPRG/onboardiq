"""Meta Model API client: every call goes through the spend guard and a disk cache.

Identical requests are answered from cache/llm/<hash>.json, so reruns are free.
"""
import hashlib
import json
import os
import time

from .config import API_BASE, CACHE_DIR, MODEL
from .spend_guard import SpendGuard

_guard: SpendGuard | None = None
_client = None


def guard() -> SpendGuard:
    global _guard
    if _guard is None:
        _guard = SpendGuard()
    return _guard


def client():
    global _client
    if _client is None:
        from openai import OpenAI

        key = os.environ.get("MODEL_API_KEY")
        if not key:
            raise RuntimeError("MODEL_API_KEY is not set (copy .env.example to .env)")
        _client = OpenAI(base_url=API_BASE, api_key=key, timeout=600)
    return _client


def _estimate_tokens(messages) -> int:
    # ~3.5 characters per token; deliberately rough and on the high side.
    return int(sum(len(m["content"]) for m in messages) / 3.5) + 200


def chat(messages, model: str | None = None, max_tokens: int = 8000,
         json_schema: dict | None = None, reasoning_effort: str | None = None,
         use_cache: bool = True) -> dict:
    """Return {"content", "usage", "cost_usd", "cached", "seconds", "model"}."""
    model = model or MODEL
    request = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if json_schema:
        request["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "result", "schema": json_schema, "strict": True},
        }
    extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else None

    key = hashlib.sha256(json.dumps([request, extra], sort_keys=True).encode()).hexdigest()
    cache_file = CACHE_DIR / "llm" / f"{key}.json"
    if use_cache and cache_file.exists():
        hit = json.loads(cache_file.read_text())
        hit["cached"] = True
        return hit

    g = guard()
    g.check(model, _estimate_tokens(messages), max_tokens)
    t0 = time.time()
    resp = client().chat.completions.create(**request, extra_body=extra)
    seconds = round(time.time() - t0, 2)

    u = resp.usage
    cached_tokens = 0
    details = getattr(u, "prompt_tokens_details", None)
    if details is not None:
        cached_tokens = getattr(details, "cached_tokens", 0) or 0
    cost = g.record(model, u.prompt_tokens, u.completion_tokens, cached_tokens)

    out = {
        "content": resp.choices[0].message.content,
        "usage": {"input": u.prompt_tokens, "output": u.completion_tokens, "cached": cached_tokens},
        "cost_usd": round(cost, 6),
        "seconds": seconds,
        "model": model,
        "cached": False,
    }
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(out))
    return out
