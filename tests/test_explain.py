"""Explainer: grounded evidence, number check, template fallback. No network."""
from onboardiq import explain, health


def hot():
    p = {"metrics": {"cpu_load": {"verdict": "standard-mib", "object": "X", "kind": "per-core", "unit": "percent",
                                  "rows": [{"name": "core", "value": 99.0}, {"name": "core", "value": 12.0}]}}}
    r = health.score(p)
    return explain.evidence(r, ["cpu_load: 2 of 2 rows far from their usual, e.g. core #1 at 99 (usual 1)"],
                            history=[("15:37:40", 100), ("15:38:32", 40)], at="15:38:32")


def fake(monkeypatch, text, boom=False):
    def chat(*a, **k):
        if boom:
            raise RuntimeError("no key")
        return {"content": text, "model": "open-model", "cost_usd": 0.001}
    monkeypatch.setattr(explain.llm, "chat", chat)


def test_evidence_carries_scores_reasons_anomalies_and_history():
    ev = hot()
    assert ev["score_out_of_100"] == 5 and ev["capped_by"] is None   # 99% on one core: row score 5
    low = ev["families"]["cpu_load"]["lowest_rows"][0]
    assert low["row"] == "core #1" and "99%" in low["why"]
    assert ev["recent_scores"][-1] == {"time": "15:38:32", "score": 40}


def test_grounded_answer_is_accepted(monkeypatch):
    fake(monkeypatch, "CPU load jumped to 99, far above its usual 1, which pulled the health score down to 5 out of 100.")
    out = explain.explain(hot())
    assert out["source"] == "open-model" and out["checked"] and "99" in out["text"]


def test_invented_number_is_rejected_and_replaced_by_the_template(monkeypatch):
    fake(monkeypatch, "CPU hit 99 because of a firmware bug that began 37 minutes ago.")
    out = explain.explain(hot())
    assert out["source"] == "template" and "rejected" in out["note"] and "37" not in out["text"]
    assert "5 out of 100" in out["text"]


def test_markdown_or_long_answers_are_rejected(monkeypatch):
    fake(monkeypatch, "- CPU is at 99\n- score is 40")
    assert explain.explain(hot())["source"] == "template"


def test_no_model_means_template_not_a_crash(monkeypatch):
    fake(monkeypatch, "", boom=True)
    out = explain.explain(hot())
    assert out["source"] == "template" and "unavailable" in out["note"]


def test_template_says_nothing_proven_when_there_is_no_score():
    ev = explain.evidence(health.score({"metrics": {}}))
    assert "no health score" in explain.template(ev)


def test_cut_off_answer_is_rejected():
    ok, why = explain.check("Your device health is 40 out of 100 and the cpu load family scored 0 with", {"score": 40})
    assert not ok and "cut off" in why
