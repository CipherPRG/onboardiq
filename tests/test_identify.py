import json

import pytest

from onboardiq import identify, llm

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


def fake_chat(answers):
    calls = []

    def chat(messages, **kw):
        calls.append(messages)
        return {"content": json.dumps(answers[len(calls) - 1]), "cost_usd": 0.01}
    return chat, calls


def test_identity_is_scrubbed_and_unread_model_dropped(monkeypatch):
    chat, calls = fake_chat([
        {"vendor": "HPE Aruba", "model": "AP-9", "model_visible": False, "device_type": "access point",
         "confidence": "medium", "evidence": "HPE logo; label MAC 20:4C:03:1A:2B:3C, S/N CNABC12345",
         "ask_for": "the rear label"},
        {"modules": ["ARUBA-MIB", "NOT-A-REAL-MIB", "ENTITY-SENSOR-MIB"], "why": "vendor + standard"}])
    monkeypatch.setattr(llm, "chat", chat)
    out = identify.identify([JPEG], ["ARUBA-MIB", "ENTITY-SENSOR-MIB", "HOST-RESOURCES-MIB"])
    assert out["identity"]["model"] is None and out["device"] == "HPE Aruba access point (model not visible)"
    assert "20:4C" not in out["identity"]["evidence"] and "CNABC12345" not in out["identity"]["evidence"]
    assert out["modules"] == ["ARUBA-MIB", "ENTITY-SENSOR-MIB"] and out["modules_dropped"] == ["NOT-A-REAL-MIB"]
    img = calls[0][1]["content"][1]["image_url"]["url"]
    assert img.startswith("data:image/jpeg;base64,")


def test_rejects_non_images_and_too_many():
    with pytest.raises(ValueError):
        identify.see([b"not an image"])
    with pytest.raises(ValueError):
        identify.see([JPEG] * 5)


def test_token_estimate_counts_images():
    est = llm._estimate_tokens([{"role": "user", "content": [{"type": "text", "text": "x" * 35},
                                                            {"type": "image_url", "image_url": {"url": "d"}}]}])
    assert est == 10 + llm.IMAGE_TOKENS_ESTIMATE + 200
