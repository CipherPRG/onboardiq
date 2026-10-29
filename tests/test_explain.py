"""Explainer: grounded evidence, number check, template fallback. No network."""
import json

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


GOOD = json.dumps({"headline": "CPU load critical on core #1",
                   "what_happened": "Health fell to 5 out of 100 from 100.",
                   "why": "Core #1 is at 99%, far above its usual 1, against full marks up to 70%."})


def test_grounded_card_is_accepted(monkeypatch):
    fake(monkeypatch, GOOD)
    out = explain.explain(hot())
    assert out["source"] == "open-model" and out["checked"] and out["headline"].startswith("CPU load")
    assert "99%" in out["why"] and out["text"].startswith("Health fell")
    assert out["check"] == explain.CHECKS["cpu"]          # what to check comes from code, not the model


def test_fenced_json_and_extra_text_are_tolerated(monkeypatch):
    fake(monkeypatch, "Here you go:\n```json\n" + GOOD + "\n```")
    assert explain.explain(hot())["source"] == "open-model"


def test_card_with_a_cut_off_field_or_long_headline_is_rejected(monkeypatch):
    bad = json.loads(GOOD)
    bad["why"] = "Core #1 is at 99%, far above its usual 1, against"
    fake(monkeypatch, json.dumps(bad))
    out = explain.explain(hot())
    assert out["source"] == "template" and "cut off" in out["note"]
    bad = json.loads(GOOD)
    bad["headline"] = "CPU load is critical on core number one right now and the score fell a lot " * 2
    fake(monkeypatch, json.dumps(bad))
    assert explain.explain(hot())["source"] == "template"


def test_plain_text_reply_falls_back_to_the_template_card(monkeypatch):
    fake(monkeypatch, "CPU load jumped to 99 which pulled the score down to 5 out of 100.")
    out = explain.explain(hot())
    assert out["source"] == "template" and "JSON" in out["note"]
    assert out["headline"] and out["what_happened"] and out["why"] and out["check"]


def test_healthy_card_says_no_action_needed():
    p = {"metrics": {"cpu_load": {"verdict": "standard-mib", "object": "X", "kind": "per-core", "unit": "percent",
                                  "rows": [{"name": "core", "value": 3.0}]}}}
    ev = explain.evidence(health.score(p))
    c = explain.template_card(ev)
    assert c["headline"].startswith("Healthy") and explain.next_step(ev) == explain.HEALTHY_CHECK


def test_invented_number_is_rejected_and_replaced_by_the_template(monkeypatch):
    bad = json.loads(GOOD)
    bad["why"] = "Core #1 is at 99% because of a firmware bug that began 37 minutes ago."
    fake(monkeypatch, json.dumps(bad))
    out = explain.explain(hot())
    assert out["source"] == "template" and "rejected" in out["note"] and "37" not in out["text"]
    assert "5 out of 100" in out["text"]


def test_markdown_or_long_answers_are_rejected(monkeypatch):
    bad = json.loads(GOOD)
    bad["why"] = "- Core #1 is at 99%\n- usual 1."
    fake(monkeypatch, json.dumps(bad))
    out = explain.explain(hot())
    assert out["source"] == "template" and "plain prose" in out["note"]


def test_no_model_means_template_not_a_crash(monkeypatch):
    fake(monkeypatch, "", boom=True)
    out = explain.explain(hot())
    assert out["source"] == "template" and "unavailable" in out["note"]


def test_template_says_nothing_proven_when_there_is_no_score():
    ev = explain.evidence(health.score({"metrics": {}}))
    assert "No health score" in explain.template(ev)


def test_cut_off_answer_is_rejected():
    ok, why = explain.check("Your device health is 40 out of 100 and the cpu load family scored 0 with", {"score": 40})
    assert not ok and "cut off" in why


def test_evidence_carries_the_step_and_how_many_rows_moved_together():
    r = health.score({"metrics": {"cpu_load": {"verdict": "standard-mib", "object": "x", "unit": "percent", "kind": "per_core",
                      "rows": [{"name": "c", "index": str(i), "value": 100.0} for i in range(3)]}}})
    ev = explain.evidence(r, history=[("t1", 100), ("t2", 70), ("t3", r["score"])])
    assert ev["previous_score"] == 70
    f = ev["families"]["cpu_load"]
    assert f["rows"] == 3 and f["rows_below_full_marks"] == 3


def test_next_step_on_a_network_device_does_not_say_top():
    r = health.score({"metrics": {"cpu_load": {"verdict": "standard-mib", "object": "x", "unit": "percent", "kind": "per_core",
                      "rows": [{"name": "c", "index": "1", "value": 100.0}]}}})
    sw = explain.evidence(r, provenance={"sysDescr": "Cisco IOS Software, Catalyst L3 Switch"})
    host = explain.evidence(r, provenance={"sysDescr": "Linux box 6.1 #1 SMP"})
    assert "top" not in explain.next_step(sw) and "top" in explain.next_step(host)
