import json

from onboardiq import discover
from onboardiq.recorded import RecordedSnmp


def test_discovery_finds_only_served_modules(tmp_path):
    mibs = tmp_path / "mibs"
    mibs.mkdir()
    obj = lambda oid: {"class": "objecttype", "nodetype": "column", "maxaccess": "read-only", "oid": oid}  # noqa: E731
    (mibs / "VENDOR-HEALTH-MIB.json").write_text(json.dumps({"cpu": obj("1.3.6.1.4.1.99.1.1"), "mem": obj("1.3.6.1.4.1.99.1.2")}))
    (mibs / "OTHER-VENDOR-MIB.json").write_text(json.dumps({"x": obj("1.3.6.1.4.1.77.1.1")}))
    (mibs / "HOST-RESOURCES-MIB.json").write_text(json.dumps({"hrProcessorLoad": obj("1.3.6.1.2.1.25.3.3.1.2")}))
    rec = tmp_path / "d.snmprec"
    rec.write_text("\n".join(["1.3.6.1.2.1.1.1.0|4|Acme Router OS 1.2", "1.3.6.1.2.1.1.2.0|6|1.3.6.1.4.1.99.5",
                              "1.3.6.1.4.1.99.1.1.1|66|12", "1.3.6.1.4.1.99.1.2.1|66|400",
                              "1.3.6.1.2.1.25.3.3.1.2.196608|2|7"]) + "\n")
    out = discover.discover(RecordedSnmp(rec), mibs)
    assert out["identity"]["enterprise"] == 99
    assert [m["module"] for m in out["modules"]] == ["VENDOR-HEALTH-MIB", "HOST-RESOURCES-MIB"]
    assert discover.describe(out["identity"]).startswith("Acme Router OS 1.2 (sysObjectID 1.3.6.1.4.1.99.5)")
    assert discover.select_modules(out["modules"], max_objects=2) == ["VENDOR-HEALTH-MIB"]
