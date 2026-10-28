"""One tiny call per configured provider: key works, structured output works, price/usage visible.

    python scripts/smoke_providers.py            # every provider whose key is set
Costs a fraction of a cent per provider. Nothing is printed from the keys.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from onboardiq import llm  # noqa: E402
from onboardiq.config import FIREWORKS_OPEN_MODEL, MODEL, OPEN_MODEL, PROVIDERS, provider_for  # noqa: E402

schema = {"type": "object", "properties": {"status": {"type": "string"}, "n": {"type": "integer"}},
          "required": ["status", "n"], "additionalProperties": False}
for model in (MODEL, OPEN_MODEL, FIREWORKS_OPEN_MODEL):
    prov = provider_for(model)
    if not os.environ.get(PROVIDERS[prov]["key_env"]):
        print(f"[{prov}] {PROVIDERS[prov]['key_env']} not set: skipped")
        continue
    try:
        r = llm.chat([{"role": "user", "content": 'Return JSON with status "ready" and n = 7.'}], model=model,
                     max_tokens=2000, json_schema=schema, use_cache=False)
    except Exception as e:  # noqa: BLE001
        print(f"[{prov}] {model}: FAILED {type(e).__name__}: {str(e)[:200]}")
        continue
    try:
        ok = json.loads(r["content"]) == {"status": "ready", "n": 7}
    except ValueError:
        ok = False
    print(f"[{prov}] {model}: structured output {'OK' if ok else 'NOT enforced'}; reply {r['content'][:80]!r}; "
          f"tokens {r['usage']['input']}+{r['usage']['output']}; charged(conservative) ${r['cost_usd']:.6f}; "
          f"{r['seconds']}s")
