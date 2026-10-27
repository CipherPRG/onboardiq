"""First call to open-weights Muse Spark on Baseten: key, structured output, reasoning field."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from onboardiq import llm  # noqa: E402
from onboardiq.config import OPEN_MODEL  # noqa: E402

schema = {"type": "object", "properties": {"status": {"type": "string"}, "n": {"type": "integer"}},
          "required": ["status", "n"], "additionalProperties": False}
r = llm.chat([{"role": "user", "content": 'Return JSON with status "ready" and n = 7.'}],
             model=OPEN_MODEL, max_tokens=2000, json_schema=schema, use_cache=False)
print("raw reply:", r["content"][:300])
try:
    print("structured output OK:", json.loads(r["content"]))
except Exception as e:  # noqa: BLE001
    print("structured output FAILED:", e)
print(f"tokens={r['usage']} charged(conservative)=${r['cost_usd']:.6f} time={r['seconds']}s")
