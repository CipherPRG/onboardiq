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


def test_reason_names_the_real_cause_and_never_the_download_line():
    out = ("Borrow missing/failed MIBs from: https://mibs.pysnmp.com/fulltexts/@mib@\n"
           "Failed MIBs: BAD-MIB (Bad grammar near offset 71 at MIB BAD-MIB, line 4)\n")
    r = intake.reason_from(out)
    assert r.startswith("Failed MIBs: BAD-MIB") and intake.failure_kind(r) == "syntax"
    assert intake.failure_kind("Missing source MIBs: FOO-MIB") == "missing import"
    assert intake.reason_from("").startswith("converter wrote nothing")


def test_conversion_is_told_not_to_borrow_from_the_internet(tmp_path):
    arg = intake.no_download(tmp_path)
    assert arg[0] == "--mib-borrower" and arg[1].startswith("file://") and "http" not in arg[1]


# --- repair rules (onboardiq/mibfix.py): text in, fixed text and a note out; unsure means no change -------------------
from onboardiq import mibfix  # noqa: E402


def test_trailing_comma_is_removed_only_when_the_converter_stopped_at_the_brace():
    t = "n NOTIFICATION-TYPE\r\n  OBJECTS { a,\r\n    b,\r\n  }\r\n"
    off = t.index("}")
    fixed, note = mibfix.repair(t, f"Failed MIBs: M (Bad grammar near offset {off} at MIB M, line 4)")
    assert "b,\r\n  }" not in fixed and "b\r\n  }" in fixed and "comma" in note
    assert mibfix.repair(t, "Failed MIBs: M (Bad grammar near offset 3 at MIB M, line 1)") is None


def test_digit_enum_labels_get_a_letter_in_front():
    t = "SYNTAX INTEGER\r\n { 100(0),\r\n 10(1), up(2) }\r\nMAX-ACCESS read-only\r\n"
    off = t.index("100")
    fixed, note = mibfix.repair(t, f"Failed MIBs: M (Bad grammar near offset {off} at MIB M, line 2)")
    assert "n100(0)" in fixed and "n10(1)" in fixed and "up(2)" in fixed and "2 enumeration labels" in note


def test_hyphen_ending_identifier_and_clash_guard():
    err = "Failed MIBs: M (Identifier should not end with '-': abc- at MIB M, line 2)"
    fixed, _ = mibfix.repair("abc- OBJECT-TYPE\nuses abc-\n", err)
    assert "abc-" not in fixed and fixed.count("abc") == 2
    assert mibfix.repair("abc OBJECT-TYPE\nabc- OBJECT-TYPE\n", err) is None


def test_implicit_enterprises_gets_imported_from_the_right_smi_module():
    err = "Failed MIBs: M (Unknown parent symbol: enterprises at MIB M)"
    v2 = "M DEFINITIONS ::= BEGIN\nIMPORTS MODULE-IDENTITY, OBJECT-TYPE\n    FROM SNMPv2-SMI;\nm MODULE-IDENTITY ::= { enterprises 1 }\nEND\n"
    fixed, note = mibfix.repair(v2, err)
    assert "OBJECT-TYPE, enterprises" in fixed and "SNMPv2-SMI" in note
    v1 = "M DEFINITIONS ::= BEGIN\nx OBJECT IDENTIFIER ::= { enterprises 5 }\nEND\n"
    fixed, note = mibfix.repair(v1, err)
    assert "IMPORTS enterprises FROM RFC1155-SMI;" in fixed
    assert mibfix.repair("M DEFINITIONS ::= BEGIN\nIMPORTS enterprises FROM RFC1155-SMI;\nEND\n", err) is None
    assert mibfix.repair(v1, "Failed MIBs: M (Unknown parent symbol: someVendorRoot at MIB M)") is None


def test_duplicate_definitions_identical_ones_dropped_different_ones_renamed():
    same = "a OBJECT IDENTIFIER ::= { r 1 }\nb OBJECT IDENTIFIER ::= { r 2 }\na OBJECT IDENTIFIER ::= { r 1 }\n"
    fixed, note = mibfix.repair(same, "Failed MIBs: M (Duplicate symbol found: a at MIB M)")
    assert fixed.count("a OBJECT IDENTIFIER") == 1 and "identical" in note
    diff = "a OBJECT IDENTIFIER ::= { r 1 }\na OBJECT IDENTIFIER ::= { r 9 }\n"
    fixed, note = mibfix.repair(diff, "Failed MIBs: M (Duplicate symbol found: a at MIB M)")
    assert "aDup2 OBJECT IDENTIFIER ::= { r 9 }" in fixed and "a OBJECT IDENTIFIER ::= { r 1 }" in fixed
    assert mibfix.repair("a OBJECT IDENTIFIER ::= { r 1 }\n", "Failed MIBs: M (Duplicate symbol found: a at MIB M)") is None


def test_hyphen_underscore_collision_is_found():
    t = "S5330-28TX OBJECT IDENTIFIER ::= { p 1 }\nS5330_28TX OBJECT IDENTIFIER ::= { p 2 }\n"
    fixed, note = mibfix.repair(t, "Failed MIBs: M (Duplicate symbol found: S5330_28TX at MIB M)")
    assert "Dup2" in fixed


_HEAD = ('{M} DEFINITIONS ::= BEGIN\nIMPORTS MODULE-IDENTITY, OBJECT-TYPE, Integer32, NOTIFICATION-TYPE, enterprises FROM SNMPv2-SMI;\n'
         '{m} MODULE-IDENTITY LAST-UPDATED "202001010000Z" ORGANIZATION "t" CONTACT-INFO "t" DESCRIPTION "t" '
         'REVISION "202001010000Z" DESCRIPTION "t" ::= {{ enterprises {n} }}\n')
_OBJ = '{o} OBJECT-TYPE SYNTAX {s} MAX-ACCESS read-only STATUS current DESCRIPTION "d" ::= {{ {p} {i} }}\n'


@needs_base
def test_real_converter_accepts_files_after_repair_and_never_touches_the_originals(tmp_path):
    comma = (_HEAD.format(M="COMMA-MIB", m="commaMib", n=2) + _OBJ.format(o="cA", s="Integer32", p="commaMib", i=1)
             + 'cTrap NOTIFICATION-TYPE OBJECTS { cA,\n } STATUS current DESCRIPTION "d" ::= { commaMib 3 }\nEND\n')
    enum = (_HEAD.format(M="ENUM-MIB", m="enumMib", n=3)
            + _OBJ.format(o="eSpeed", s="INTEGER\n { 100(0),\n 10(1)\n }", p="enumMib", i=1) + "END\n")
    dup = (_HEAD.format(M="DUP-MIB", m="dupMib", n=4) + "dupNode OBJECT IDENTIFIER ::= { dupMib 7 }\n"
           + _OBJ.format(o="dupVal", s="Integer32", p="dupNode", i=1) + "dupNode OBJECT IDENTIFIER ::= { dupMib 7 }\nEND\n")
    src = tmp_path / "mibs"
    src.mkdir()
    for n, t in {"comma": comma, "enum": enum, "dup": dup}.items():
        (src / f"{n}.mib").write_bytes(t.replace("\n", "\r\n").encode())
    before = {p.name: p.read_bytes() for p in src.iterdir()}
    off = intake.convert(src, tmp_path / "off", repair=False)
    on = intake.convert(src, tmp_path / "on", repair=True)
    assert sorted(off["failed"]) == ["COMMA-MIB", "DUP-MIB", "ENUM-MIB"]
    assert on["failed"] == {} and sorted(on["repairs"]) == ["COMMA-MIB", "DUP-MIB", "ENUM-MIB"]
    assert (tmp_path / "on" / "_repairs.json").exists()
    assert {p.name: p.read_bytes() for p in src.iterdir()} == before          # the originals are untouched
