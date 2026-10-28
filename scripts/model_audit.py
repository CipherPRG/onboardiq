"""Ask Muse Spark Open (Baseten) to review this repository as a hackathon judge would. Output: results/model_audit.md

    python scripts/model_audit.py                 # one review call, about $0.50-1.50
    python scripts/model_audit.py --dry-run       # print what would be sent and the token estimate, call nothing

The model sees only public repository files (README, BUILD_LOG, the core source files, the tests' names and the
recorded eval summary), the official rubric and the stated facts. It is not given anyone's opinion of the work,
so it is not steered toward a verdict. Its output is a second opinion, not a fact: it can be wrong or miss
things, so check each point against the code before acting on it.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FILES = ["README.md", "BUILD_LOG.md", "onboardiq/agent.py", "onboardiq/reader.py", "onboardiq/verify.py",
         "onboardiq/profile.py", "onboardiq/health.py", "onboardiq/health_policy.json", "onboardiq/explain.py",
         "onboardiq/alerts.py", "onboardiq/monitor.py", "onboardiq/replay.py", "onboardiq/server.py",
         "onboardiq/snmp.py", "onboardiq/intake.py", "onboardiq/spend_guard.py", "web/index.html"]
MAX_FILE_CHARS = 60_000

RUBRIC = """Stage One (pass/fail): the project fits a theme (Build for Your World, or Push the Frontier) and reasonably uses Meta Model API and/or Meta open-weight models.
Stage Two, each scored 0-10: Impact 30%, Use of Meta AI Tooling 25%, Technical Depth and Execution 20%, Demo 25%.
Stage Three: the top 5 per track are ranked by judges watching the 3-minute demo videos live.
Rules: public repo created on or after Oct 26 2026; a working demo that runs from the repo's setup instructions plus the video; judges need no accounts or keys of their own; every model called at runtime must be a Meta model; open-source libraries and public datasets are fine if labelled; keep API keys out of the repo.
Bonus prizes: Best Agentic (autonomy, effectiveness, practical usefulness); Best Build on Meta Open Weight Models (explain how and where the open model is used); Most Viral on X."""

FACTS = """Facts stated by the author (not verified by this review):
- One person built it during the hackathon. Two live devices were tested: a Linux machine (net-snmp, WSL2) and a MikroTik RouterOS 7 virtual router. Neither has temperature or fan sensors.
- The accuracy numbers in the README and BUILD_LOG come from recorded runs on LibreNMS device recordings. The test split was never used for tuning.
- Muse Spark picks the objects; Muse Spark Open writes the health explanations (served on Baseten)."""

TASK = """Review this repository as a skeptical hackathon judge would, using only the files, rubric and facts above.
Reply in Markdown with these sections:
1. Stage One: pass or fail, and why.
2. A 0-10 score for each of the four Stage Two criteria, each with the evidence from the files that supports it and what would raise it.
3. Claims in the README or BUILD_LOG that the code or the recorded results do not support, or that are overstated. Quote the claim and say what is missing.
4. Bugs, risks or security problems you can see in the code, each with the file and the reason.
5. Things a judge would find confusing when trying to run it.
6. The five changes that would most improve the score, in order, each with the criterion it affects.
Say 'cannot tell from these files' instead of guessing. Do not praise; list evidence."""


def collect():
    parts, used = [], []
    for rel in FILES:
        p = ROOT / rel
        if not p.is_file():
            continue
        text = p.read_text(errors="replace")
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + "\n[file cut here]"
        parts.append(f"===== {rel} =====\n{text}")
        used.append(rel)
    tests = sorted(t.name for t in (ROOT / "tests").glob("test_*.py"))
    names = []
    for t in tests:
        names += re.findall(r"^def (test_\w+)", (ROOT / "tests" / t).read_text(), re.M)
    parts.append("===== test names =====\n" + "\n".join(names))
    ev = sorted((ROOT / "results").glob("recorded_eval_*.json")) if (ROOT / "results").is_dir() else []
    if ev:
        d = json.loads(ev[-1].read_text())
        keep = {k: v for k, v in d.items() if k not in ("rows",)}
        parts.append(f"===== latest recorded eval summary ({ev[-1].name}; per-question rows left out) =====\n"
                     + json.dumps(keep, indent=1)[:12_000])
    return "\n\n".join(parts), used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default=None, help="default: the explainer model (Muse Spark Open on Baseten)")
    args = ap.parse_args()
    from onboardiq import config, llm
    model = args.model or config.EXPLAIN_MODEL
    body, used = collect()
    user = f"{FACTS}\n\nRubric and rules:\n{RUBRIC}\n\n{TASK}\n\nRepository files:\n\n{body}"
    est = len(user) // 4
    print(f"{len(used)} files, about {est:,} tokens, model {model}")
    if args.dry_run:
        print("files:", ", ".join(used))
        return
    r = llm.chat([{"role": "system", "content": "You are a careful, skeptical reviewer. Use only the material given."},
                  {"role": "user", "content": user}], model=model, max_tokens=12000, reasoning_effort="high")
    out = ROOT / "results" / "model_audit.md"
    out.parent.mkdir(exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    out.write_text(f"<!-- reviewer: {r.get('model')}, {stamp}, cost ${r.get('cost_usd', 0):.3f} -->\n\n"
                   + (r.get("content") or "(empty reply)"))
    print(f"saved {out.relative_to(ROOT)}  (reviewer {r.get('model')}, cost ${r.get('cost_usd', 0):.3f})")


if __name__ == "__main__":
    main()
