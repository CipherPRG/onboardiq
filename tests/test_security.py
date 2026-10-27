"""Nothing from a model, a MIB or a recording can become a command-line option or leak a credential."""
import os
import stat

import pytest

from onboardiq.snmp import Snmp, check_oid


@pytest.mark.parametrize("bad", ["-c secret", "-v1", "1.3.6; rm -rf /", "1.3.6.1 -c x", "../etc", "", "1", None,
                                 "1.3.6.1.2.1.1.1.0\n-c x"])
def test_non_numeric_oids_refused(bad):
    with pytest.raises(ValueError):
        check_oid(bad)


def test_numeric_oids_pass():
    assert check_oid(".1.3.6.1.2.1.25.2.2.0") == ".1.3.6.1.2.1.25.2.2.0"


@pytest.mark.parametrize("host", ["-v1", "a b", "x;y", "", "$(id)"])
def test_bad_hosts_refused(host):
    with pytest.raises(ValueError):
        Snmp(host)


def test_credentials_never_on_command_line_and_private_file():
    seen = []
    s = Snmp("10.0.0.5", community="s3cret-community", runner=lambda cmd: seen.append(cmd) or "")
    s.walk("1.3.6.1.2.1.25.2.2")
    assert seen and all("s3cret-community" not in " ".join(c) for c in seen)
    assert all(c[c.index("--") + 1] == "10.0.0.5" for c in seen)     # options end before host/OIDs
    conf = os.path.join(s._conf_dir, "snmp.conf")
    assert stat.S_IMODE(os.stat(s._conf_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(conf).st_mode) == 0o600
    assert "defCommunity s3cret-community" in open(conf).read()


def test_v3_credentials_in_private_conf_only():
    seen = []
    s = Snmp("10.0.0.5", v3={"user": "mon", "auth_pass": "authpass123", "priv_pass": "privpass123"},
             runner=lambda cmd: seen.append(cmd) or "")
    s.get("1.3.6.1.2.1.1.3.0")
    text = open(os.path.join(s._conf_dir, "snmp.conf")).read()
    assert "defSecurityLevel authPriv" in text and "privpass123" in text
    assert all("authpass123" not in " ".join(c) and "privpass123" not in " ".join(c) for c in seen)


def test_newline_in_credential_refused():
    with pytest.raises(ValueError):
        Snmp("localhost", community="public\ndefVersion 1")


def test_verifier_rejects_injected_oid_without_calling_the_device():
    from onboardiq.verify import Item, static_checks
    called = []
    s = Snmp("localhost", runner=lambda cmd: called.append(cmd) or "")
    r = static_checks(s, Item("cpu", "-c secret 1.3.6", canonical_unit="percent"))
    assert r.verdict == "rejected" and not called
