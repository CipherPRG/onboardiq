"""First call: confirms the API key, model name, billing and spend guard all work."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from onboardiq import llm  # noqa: E402

r = llm.chat([{"role": "user", "content": "Reply with exactly: ready"}], max_tokens=500, use_cache=False)
print("reply:", r["content"].strip())
print(f"model={r['model']} tokens={r['usage']} cost=${r['cost_usd']:.6f} time={r['seconds']}s")
print("spent today:", llm.guard().spent_today())
