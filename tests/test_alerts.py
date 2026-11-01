"""Alert emails: when they fire, what they contain, that they are escaped and never need an account. No network."""
from onboardiq import alerts

RES = {"score": 40, "level": "critical", "at": "2026-10-29T17:40:35", "summary": "cpu_load core #1 0/100",
       "anomalies": ["cpu_load: 12 of 12 rows far from their usual"],
       "families": {"cpu_load": {"score": 0.0, "worst": "core #1",
                                 "rows": [{"name": "core #1", "score": 0.0, "level": "critical", "why": "100% (full marks up to 70%)"}]},
                    "memory": {"score": 100.0, "worst": "overall",
                               "rows": [{"name": "overall", "score": 100.0, "level": "ok", "why": "12%"}]}},
       "explanation": {"headline": "CPU load critical on core #1", "what_happened": "Health fell to 40 out of 100.",
                       "why": "Core #1 is at 100%.", "check": "Find the process using the CPU.",
                       "source": "meta-models/muse-spark-open-1026"}}


def level(res, lv, score):
    return {**res, "level": lv, "score": score}


def test_email_has_subject_card_table_and_check():
    subject, text, page = alerts.render("Linux laptop", RES, previous="ok")
    assert subject == "[OnboardIQ] Linux laptop: health 40/100 critical (CPU load critical on core #1)"
    assert "ok to critical" in text and "What to check: Find the process" in text and "CPU load" in text
    assert "<table" in page and "Muse Spark" not in subject and "muse-spark-open" in page


def test_every_value_is_escaped_in_the_html_email():
    evil = {**RES, "explanation": {**RES["explanation"], "headline": "<script>alert(1)</script>"}}
    _, _, page = alerts.render("<img src=x onerror=1>", evil)
    assert "<script>" not in page and "<img" not in page and "&lt;script&gt;" in page


def test_outbox_file_is_written_without_smtp(tmp_path, monkeypatch):
    monkeypatch.delenv("OIQ_SMTP_HOST", raising=False)
    a = alerts.Alerter("box", settings=None, outbox=tmp_path)
    assert a.consider(level(RES, "ok", 100)) is None                     # first reading: nothing to compare
    sent = a.consider(RES)
    assert sent["via"] == "outbox" and (tmp_path / sent["file"]).read_text().startswith("<!doctype html>")


def test_cooldown_stops_flapping_but_worse_levels_always_send(tmp_path, monkeypatch):
    monkeypatch.delenv("OIQ_SMTP_HOST", raising=False)
    t = [1000.0]
    a = alerts.Alerter("box", cooldown=300, settings=None, outbox=tmp_path, clock=lambda: t[0])
    a.consider(level(RES, "ok", 100))
    assert a.consider(level(RES, "warning", 70))                         # worse: sent
    t[0] += 30
    assert a.consider(level(RES, "ok", 100)) is None                     # recovery inside the cooldown: held back
    t[0] += 30
    assert a.consider(level(RES, "critical", 40))                        # worse again: always sent
    t[0] += 400
    r = a.consider(level(RES, "ok", 100))
    assert r and "recovered" in r["subject"]                             # recovery after the cooldown


def test_smtp_settings_need_valid_addresses_and_come_from_the_environment():
    ok = {"OIQ_SMTP_HOST": "smtp.example.com", "OIQ_ALERT_TO": "a@example.com, b@example.com", "OIQ_SMTP_USER": "me@example.com"}
    cfg = alerts.smtp_settings(ok)
    assert cfg["port"] == 587 and cfg["to"] == ["a@example.com", "b@example.com"] and cfg["from"] == "me@example.com"
    assert alerts.smtp_settings({**ok, "OIQ_ALERT_TO": "not-an-address"}) is None
    assert alerts.smtp_settings({**ok, "OIQ_ALERT_TO": "a@example.com\nBcc: x@example.com"}) is None
    assert alerts.smtp_settings({}) is None


def test_smtp_failure_falls_back_to_the_outbox(tmp_path, monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            raise OSError("no route")
    monkeypatch.setattr(alerts.smtplib, "SMTP", Boom)
    cfg = {"host": "h", "port": 587, "user": "", "password": "", "from": "a@example.com", "to": ["b@example.com"]}
    out = alerts.deliver("s", "t", "<p>x</p>", settings=cfg, outbox=tmp_path)
    assert out["via"] == "outbox" and out["smtp_error"] == "OSError"


def test_recipients_from_the_dialog_are_checked_and_limited():
    import pytest
    from onboardiq import alerts
    assert alerts.parse_recipients("a@x.org, b@y.com") == ["a@x.org", "b@y.com"]
    assert alerts.parse_recipients("") == []
    for bad in ("not-an-address", "a@x.org\r\nBcc: v@z.com", "a@b.co,c@d.co,e@f.co,g@h.co"):
        with pytest.raises(ValueError):
            alerts.parse_recipients(bad)


def test_the_dialog_recipient_replaces_alert_to_but_never_the_account():
    from onboardiq import alerts
    env = {"OIQ_SMTP_HOST": "smtp.example.com", "OIQ_SMTP_USER": "me@example.com", "OIQ_SMTP_PASS": "pw"}
    assert alerts.mail_configured(env)                                     # an account, no default recipient
    assert alerts.smtp_settings(env) is None                               # nobody to send to yet
    cfg = alerts.smtp_settings(env, to=["you@example.org"])
    assert cfg["to"] == ["you@example.org"] and cfg["user"] == "me@example.com" and cfg["password"] == "pw"
    assert not alerts.mail_configured({"OIQ_ALERT_TO": "you@example.org"})   # a recipient alone is not an account


def test_settings_forgive_stray_spaces_and_quotes_and_465_uses_tls(monkeypatch):
    from onboardiq import alerts
    env = {"OIQ_SMTP_HOST": " smtp.gmail.com ", "OIQ_SMTP_PORT": "465 ", "OIQ_SMTP_USER": '"me@gmail.com"',
           "OIQ_SMTP_PASS": "abcd efgh ijkl mnop ", "OIQ_ALERT_TO": "me@gmail.com"}
    cfg = alerts.smtp_settings(env)
    assert cfg["host"] == "smtp.gmail.com" and cfg["port"] == 465 and cfg["user"] == "me@gmail.com"
    used = {}

    class Fake:
        def __init__(self, host, port, timeout):
            used["cls"] = type(self).__name__
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): used["starttls"] = True
        def login(self, u, p): used["login"] = u
        def send_message(self, m): used["sent"] = True

    class SMTP_SSL(Fake):
        pass
    monkeypatch.setattr(alerts.smtplib, "SMTP_SSL", SMTP_SSL)
    out = alerts.deliver("s", "t", "<p>p</p>", settings=cfg)
    assert out["via"] == "smtp" and used == {"cls": "SMTP_SSL", "login": "me@gmail.com", "sent": True}


def test_a_failed_send_says_what_to_fix_without_the_password(tmp_path, monkeypatch):
    import smtplib
    from onboardiq import alerts

    def hangup(*a, **k):
        raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
    monkeypatch.setattr(alerts.smtplib, "SMTP", hangup)
    cfg = {"host": "smtp.gmail.com", "port": 587, "user": "me@gmail.com", "password": "secret-pw",
           "from": "me@gmail.com", "to": ["me@gmail.com"]}
    out = alerts.deliver("s", "t", "<p>p</p>", settings=cfg, outbox=tmp_path)
    assert out["via"] == "outbox" and "OIQ_SMTP_PORT" in out["smtp_error"] and "secret-pw" not in out["smtp_error"]
