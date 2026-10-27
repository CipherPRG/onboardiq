from onboardiq.recorded import RecordedSnmp
from onboardiq.verify import Item, static_checks, verify_item


def rec(tmp_path, lines):
    f = tmp_path / "dev.snmprec"
    f.write_text("\n".join(lines) + "\n")
    return RecordedSnmp(f)


def temp(oid, scale=1):
    return Item(family="temperature", oid=oid, name="t", value_type="gauge", scale=scale, canonical_unit="celsius")


def test_walk_get_and_hex_strings(tmp_path):
    d = rec(tmp_path, ["1.3.6.1.4.1.9.1.1|66|41", "1.3.6.1.4.1.9.1.2|66|38", "1.3.6.1.4.1.9.10|4x|35352E3600",
                       "1.3.6.1.4.1.99.1|2|7"])
    assert [v.value for v in d.walk("1.3.6.1.4.1.9.1")] == ["41", "38"]
    assert d.walk("1.3.6.1.4.1.9.10")[0].type == "STRING" and d.walk("1.3.6.1.4.1.9.10")[0].value == "55.6"
    assert d.get("1.3.6.1.4.1.9.2")[0].type == "missing"


def test_mixed_sensor_column_is_unresolved_not_rejected(tmp_path):
    # one column holding a voltage in deci-volts (243) and a temperature (38): real MikroTik layout
    d = rec(tmp_path, ["1.3.6.1.4.1.14988.1.1.3.100.1.3.1|66|243", "1.3.6.1.4.1.14988.1.1.3.100.1.3.2|66|38"])
    r = verify_item(d, temp("1.3.6.1.4.1.14988.1.1.3.100.1.3"))
    assert r.verdict == "unproven" and r.partial and "1 of 2 rows" in r.evidence[-1]["detail"]


def test_wrong_scale_on_every_row_is_still_rejected(tmp_path):
    d = rec(tmp_path, ["1.3.6.1.4.1.5.1.1|2|412", "1.3.6.1.4.1.5.1.2|2|398"])
    r = static_checks(d, temp("1.3.6.1.4.1.5.1"))
    assert r.verdict == "rejected" and r.suggested_scale == 0.1
