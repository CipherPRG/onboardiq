"""MIB intake: find modules in any file, convert, report what is missing. Needs pysmi's mibdump."""
import json

import pytest

from onboardiq import intake, mib

pytest.importorskip("pysmi")

GOOD = """DEMO-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Gauge32, enterprises FROM SNMPv2-SMI;
demo MODULE-IDENTITY
    LAST-UPDATED "202001010000Z"
    ORGANIZATION "Test"
    CONTACT-INFO "none"
    DESCRIPTION "A test module written for this test."
    REVISION "202001010000Z"
    DESCRIPTION "first"
    ::= { enterprises 99999 }
demoCpuLoad OBJECT-TYPE
    SYNTAX Gauge32 (0..100)
    MAX-ACCESS read-only
    STATUS current
    DESCRIPTION "CPU load in percent."
    ::= { demo 1 }
END
"""
NEEDS = GOOD.replace("DEMO-MIB", "NEEDS-MIB").replace("FROM SNMPv2-SMI;", "FROM SNMPv2-SMI\n    TC-THING FROM MISSING-TC-MIB;")


needs_base = pytest.mark.skipif("SNMPv2-SMI" not in intake.modules_in("/usr/share/snmp/mibs")
                                and not any("SNMPv2-SMI" in intake.modules_in(d) for d in intake.system_dirs()),
                                reason="the standard SNMPv2-SMI text is not installed (net-snmp MIBs)")


def test_finds_modules_in_any_file_name_and_lists_missing_imports(tmp_path):
    (tmp_path / "vendor.txt").write_text(GOOD)
    (tmp_path / "other.mib").write_text(NEEDS)
    (tmp_path / "readme.md").write_text("not a mib")
    assert set(intake.modules_in(tmp_path)) == {"DEMO-MIB", "NEEDS-MIB"}
    assert intake.imports_of(tmp_path / "other.mib") == ["MISSING-TC-MIB", "SNMPv2-SMI"]


@needs_base
def test_converts_to_the_json_the_agent_reads(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    (src / "vendor.txt").write_text(GOOD)
    r = intake.convert(src, out)
    assert r["converted"] == ["DEMO-MIB"] and not r["failed"] and not r["missing_imports"]
    cands = mib.candidates(out, ["DEMO-MIB"])
    assert [c["name"] for c in cands] == ["demoCpuLoad"] and cands[0]["mib_range"] == [{"min": 0, "max": 100}]
    json.loads((out / "DEMO-MIB.json").read_text())


@needs_base
def test_missing_import_is_reported_not_hidden(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "n.mib").write_text(NEEDS)
    r = intake.convert(src, tmp_path / "out")
    assert r["missing_imports"] == {"NEEDS-MIB": ["MISSING-TC-MIB"]}


def test_empty_folder_says_so(tmp_path):
    assert "no MIB modules" in intake.convert(tmp_path, tmp_path / "o")["note"]


def test_committed_recordings_carry_no_vendor_mib_description_text():
    """MIB description text is the vendor's copyright. The public demo recordings keep names, OIDs, types and
    ranges (facts the replay needs) and descriptions only for modules with an open licence (IETF RFC text,
    net-snmp)."""
    import glob
    open_modules = {"HOST-RESOURCES-MIB", "LM-SENSORS-MIB"}      # IETF RFC text and the net-snmp project's own MIBs

    def is_open(m):
        return m in open_modules or m.startswith(("UCD-", "NET-SNMP-"))
    for f in glob.glob("demo/*.json"):
        for c in json.load(open(f)).get("cands", []):
            assert is_open(c["module"]) or not c.get("description"), (f, c["module"])


def test_duplicate_module_definitions_are_reported(tmp_path):
    (tmp_path / "a.txt").write_text(GOOD)
    (tmp_path / "copy.mib").write_text(GOOD)
    dup = {}
    mods = intake.modules_in(tmp_path, dup)
    assert list(mods) == ["DEMO-MIB"] and len(dup["DEMO-MIB"]) == 2


def test_failure_causes_are_grouped_and_counted():
    assert intake.failure_kind("converter timed out after 120 s") == "timeout"
    assert intake.failure_kind("converter wrote nothing") == "nothing written"
    assert intake.failure_kind("Unresolved import FOO-MIB") == "missing import"
    assert intake.failure_kind("syntax error at line 4") == "syntax"
    r = {"converted": ["A", "B"], "failed": {"C": "converter wrote nothing", "D": "syntax error at line 4"},
         "missing_imports": {"B": ["X-MIB"], "C": ["X-MIB", "Y-MIB"]}, "incomplete": ["B"], "duplicates": {}}
    s = intake.summary(r)
    assert (s["modules"], s["converted_clean"], s["converted_incomplete"], s["failed"]) == (4, 1, 1, 2)
    assert s["most_missing_imports"][0] == ("X-MIB", 2) and s["failed_by_cause"]["syntax"] == 1
