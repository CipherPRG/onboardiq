"""Meta Model API client: every call goes through the spend guard and a disk cache.

Identical requests are answered from cache/llm/<hash>.json, so reruns are free.
"""
import hashlib
import json
import os
import time

from . import config
from .config import CACHE_DIR, MODEL, PROVIDERS, provider_for
from .spend_guard import SpendGuard

_guards: dict = {}
_clients: dict = {}
USED_KEYS: list = []      # cache keys touched by chat() in this process (for session recording)


def guard(provider: str = "meta") -> SpendGuard:
    if provider not in _guards:
        _guards[provider] = SpendGuard(provider=provider)
    return _guards[provider]


def client(provider: str = "meta"):
    if provider not in _clients:
        from openai import OpenAI

        cfg = PROVIDERS[provider]
        key = os.environ.get(cfg["key_env"])
        if not key:
            raise RuntimeError(f"{cfg['key_env']} is not set (copy .env.example to .env)")
        _clients[provider] = OpenAI(base_url=cfg["base"], api_key=key, timeout=600, max_retries=6)
    return _clients[provider]


IMAGE_TOKENS_ESTIMATE = 4000   # per image or document part; deliberately high (no published figure)


def _estimate_tokens(messages) -> int:
    # ~3.5 characters per token; deliberately rough and on the high side.
    chars, parts = 0, 0
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            chars += len(c)
            continue
        for item in c:   # multimodal content: text items count by length, images by a fixed estimate
            if item.get("type") == "text":
                chars += len(item.get("text", ""))
            else:
                parts += 1
    return int(chars / 3.5) + parts * IMAGE_TOKENS_ESTIMATE + 200


def chat(messages, model: str | None = None, max_tokens: int = 8000,
         json_schema: dict | None = None, reasoning_effort: str | None = None,
         use_cache: bool = True) -> dict:
    """Return {"content", "usage", "cost_usd", "cached", "seconds", "model"}.

    `model` may be a comma-separated list ("a,b"): models are tried in order and the next one is used when
    a provider is out of budget, has no key, or fails (rate limit, outage). The result's "model" says which
    one answered, so a benchmark that must not mix hosts simply passes a single model.
    """
    models = [m.strip() for m in (model or MODEL).split(",") if m.strip()]
    if len(models) == 1:
        return _chat_one(messages, models[0], max_tokens, json_schema, reasoning_effort, use_cache)
    from .spend_guard import SpendLimitError
    errors = []
    for m in models:
        try:
            out = _chat_one(messages, m, max_tokens, json_schema, reasoning_effort, use_cache)
            if errors:
                out["fallback_from"] = errors
            return out
        except (SpendLimitError, RuntimeError) as e:
            errors.append(f"{m}: {type(e).__name__}: {str(e)[:120]}")
        except Exception as e:  # noqa: BLE001 - provider errors (rate limit, outage, timeout)
            if type(e).__module__.split(".")[0] != "openai":
                raise
            errors.append(f"{m}: {type(e).__name__}: {str(e)[:120]}")
    raise RuntimeError("every provider failed: " + " | ".join(errors))


def _response_format(schema: dict, style: str) -> dict:
    if style == "json_object":    # Fireworks' own structured-output form
        return {"type": "json_object", "schema": schema}
    return {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}}


def _chat_one(messages, model, max_tokens, json_schema, reasoning_effort, use_cache) -> dict:
    config.require_meta(model)
    request = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if json_schema:
        request["response_format"] = _response_format(json_schema, "json_schema")
    extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else None

    key = hashlib.sha256(json.dumps([request, extra], sort_keys=True).encode()).hexdigest()
    cache_file = CACHE_DIR / "llm" / f"{key}.json"
    USED_KEYS.append(key)
    if use_cache and not os.environ.get("OIQ_NO_CACHE") and cache_file.exists():
        hit = json.loads(cache_file.read_text())
        hit["cached"] = True
        return hit

    g = guard(provider_for(model))
    g.check(model, _estimate_tokens(messages), max_tokens)
    t0 = time.time()
    try:
        resp = client(provider_for(model)).chat.completions.create(**request, extra_body=extra)
    except Exception as e:  # noqa: BLE001
        # Fireworks may want its own response_format shape: retry once, before anything was charged.
        if not (json_schema and provider_for(model) == "fireworks" and "response_format" in str(e).lower()):
            raise
        request["response_format"] = _response_format(json_schema, "json_object")
        resp = client("fireworks").chat.completions.create(**request, extra_body=extra)
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
