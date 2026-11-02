"""Exports to Prometheus (snmp_exporter) and Zabbix from a saved onboarding.

The two fixtures are real device profiles: built by profile.build() against a net-snmp agent, then trimmed to
the fields the exporters read. One has a counter CPU and UCD memory scalars, the other hrStorage memory rows.
"""
import copy
import json
import re
from pathlib import Path

import yaml

from onboardiq import export, health

FIX = Path(__file__).parent / "fixtures"
UCD = json.loads((FIX / "onboard_ucd_counter_cpu.json").read_text())
HR = json.loads((FIX / "onboard_hr_storage_memory.json").read_text())


def with_cores(saved, n=4):
    """The same onboarding with hrProcessorLoad per core, as profile.cpu_load() records it."""
    d = copy.deepcopy(saved)
    d["profile"]["metrics"]["cpu_load"] = {
        "verdict": "standard-mib", "object": "HOST-RESOURCES-MIB::hrProcessorLoad", "unit": "percent",
        "kind": "per-core", "rows": [{"index": str(196608 + i), "name": "GenuineIntel: Core", "value": 3.0}
                                     for i in range(n)]}
    return d


def test_alert_limits_are_where_the_health_score_changes_level():
    for fam in ("cpu", "cpu_load", "memory", "disk", "temperature"):
        warn, crit = export.level_limits(fam)
        r = health.DEFAULT["rules"][fam]
        lv = health.DEFAULT["levels"]
        assert abs(health._line(warn, r["ok"], r["mid"], r["zero"]) - lv["ok_from"]) < 0.01
        assert abs(health._line(crit, r["ok"], r["mid"], r["zero"]) - lv["warning_from"]) < 0.01
        assert r["ok"] < warn < crit < r["zero"]
    assert export.level_limits("cpu") == (80.0, 92.0)
    assert export.level_limits("fan") is None                  # fans are scored as stopped/spinning, not a limit


def test_only_proven_and_standard_objects_are_exported():
    fams = {s["family"] for s in export.series(UCD)}
    assert fams == {"cpu", "memory", "disk"}                   # temperature and fan were not proven
    assert all(s["verdict"] in export.PROVEN or s["verdict"] == "standard-mib" for s in export.series(HR))


def test_snmp_exporter_scalars_counters_and_scale():
    snmp_yml, rules = export.snmp_exporter(UCD)
    (name, mod), = snmp_yml["modules"].items()
    assert name == "onboardiq_linux_laptop_net_snmp_agent"
    m = {x["name"]: x for x in mod["metrics"]}
    assert m["ssCpuRawUser"]["type"] == "counter" and "indexes" not in m["ssCpuRawUser"]
    assert "1.3.6.1.4.1.2021.11.50.0" in mod["get"]           # scalars are fetched with get, at .0
    assert m["memAvailRealX"]["scale"] == 1024 and m["memTotalRealX"]["scale"] == 1024
    exprs = {r["alert"]: r["expr"] for r in rules["groups"][0]["rules"]}
    assert exprs["OnboardIQMemoryInTrouble"] == "100 * (memTotalRealX - memAvailRealX) / memTotalRealX > 95"
    disk = exprs["OnboardIQDiskUnderStrain"]
    assert 'hrStorageIndex=~"34|36|43|54"' in disk and disk.endswith("> 86.25")   # only the real disks
    assert not any("Cpu" in a for a in exprs)                  # a tick counter has no percent limit


def test_snmp_exporter_tables_rows_and_names():
    snmp_yml, rules = export.snmp_exporter(with_cores(HR))
    mod = next(iter(snmp_yml["modules"].values()))
    m = {x["name"]: x for x in mod["metrics"]}
    load = m["hrProcessorLoad"]
    assert load["indexes"] == [{"labelname": "hrDeviceIndex", "type": "gauge"}]
    assert load["lookups"][0]["oid"] == "1.3.6.1.2.1.25.3.2.1.3" and load["lookups"][0]["labelname"] == "name"
    assert set(mod["walk"]) >= {"1.3.6.1.2.1.25.3.3.1.2", "1.3.6.1.2.1.25.3.2.1.3", "1.3.6.1.2.1.25.2.3.1.4"}
    assert "hrStorageAllocationUnits" in m and "scale" not in m["hrStorageUsed"]    # bytes need the unit size
    assert [x["name"] for x in mod["metrics"]].count("hrStorageUsed") == 1           # memory and disk share it
    exprs = {r["alert"]: r["expr"] for r in rules["groups"][0]["rules"]}
    assert exprs["OnboardIQMemoryUnderStrain"].startswith('100 * hrStorageUsed{hrStorageIndex=~"1"}')
    assert exprs["OnboardIQCpuLoadInTrouble"].endswith("> 92")
    assert 'hrDeviceIndex=~"196608|196609|196610|196611"' in exprs["OnboardIQCpuLoadUnderStrain"]
    yaml.safe_load(yaml.safe_dump(snmp_yml))                    # plain YAML, no Python objects


def test_zabbix_template_shape():
    doc = export.zabbix(with_cores(HR))
    z = doc["zabbix_export"]
    assert z["version"] == "6.0"
    t = z["templates"][0]
    name = t["template"]
    uuids = re.findall(r"'uuid': '([0-9a-f]+)'", repr(doc))
    assert len(uuids) == len(set(uuids)) and all(re.fullmatch(r"[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}", u)
                                                  for u in uuids)          # UUIDv4, unique
    rules = {r["key"]: r for r in t["discovery_rules"]}
    cpu = rules["oiq.cpu_load.discovery"]
    assert cpu["snmp_oid"] == "discovery[{#OIQVAL},1.3.6.1.2.1.25.3.3.1.2,{#OIQNAME},1.3.6.1.2.1.25.3.2.1.3]"
    proto = cpu["item_prototypes"][0]
    assert proto["snmp_oid"] == "1.3.6.1.2.1.25.3.3.1.2.{#SNMPINDEX}" and proto["units"] == "%"
    trig = {x["priority"]: x["expression"] for x in proto["trigger_prototypes"]}
    assert trig["HIGH"] == f"min(/{name}/oiq.cpu_load[{{#SNMPINDEX}}],5m)>{{$OIQ.CPU_LOAD.CRIT}}"
    mem = rules["oiq.memory.discovery"]
    assert mem["filter"]["conditions"][1]["value"] == "^(1)$"                  # RAM row only
    calc = [p for p in mem["item_prototypes"] if p["type"] == "CALCULATED"][0]
    assert calc["params"] == "100*last(//oiq.memory[{#SNMPINDEX}])/last(//oiq.memory.total[{#SNMPINDEX}])"
    macros = {m["macro"]: m["value"] for m in t["macros"]}
    assert macros["{$OIQ.CPU_LOAD.WARN}"] == "80" and macros["{$OIQ.MEMORY.CRIT}"] == "95"
    assert all(v for v in t.values())                           # Zabbix rejects empty lists


def test_zabbix_scalars_counters_and_preprocessing():
    t = export.zabbix(UCD)["zabbix_export"]["templates"][0]
    items = {i["key"]: i for i in t["items"]}
    assert items["oiq.cpu"]["preprocessing"] == [{"type": "CHANGE_PER_SECOND", "parameters": [""]}]
    assert items["oiq.cpu"]["snmp_oid"] == "1.3.6.1.4.1.2021.11.50.0" and "triggers" not in items["oiq.cpu"]
    assert items["oiq.memory"]["preprocessing"] == [{"type": "MULTIPLIER", "parameters": ["1024"]}]
    assert items["oiq.memory.pused"]["params"] == \
        "100*(last(//oiq.memory.total) - last(//oiq.memory))/last(//oiq.memory.total)"


def test_ids_are_stable_so_a_reexport_updates_the_same_template():
    assert export.zabbix(UCD) == export.zabbix(copy.deepcopy(UCD))


def test_cli_writes_three_files(tmp_path):
    import subprocess
    import sys
    src = tmp_path / "onboard_20261101_000000.json"
    src.write_text(json.dumps(HR))
    out = subprocess.run([sys.executable, "scripts/export.py", str(src), "--out", str(tmp_path / "x")],
                         capture_output=True, text=True, cwd=Path(__file__).parent.parent)
    assert out.returncode == 0, out.stderr
    names = sorted(p.name for p in (tmp_path / "x").iterdir())
    assert names == ["onboardiq.rules.yml", "snmp.yml", "zabbix_template.yaml"]
    assert yaml.safe_load((tmp_path / "x" / "zabbix_template.yaml").read_text())["zabbix_export"]["version"] == "6.0"


def test_zabbix_template_name_uses_only_allowed_characters():
    saved = copy.deepcopy(UCD)
    saved["profile"]["device"] = "Linux LAPTOP 6.18 #1 SMP (sysObjectID 1.3.6.1.4.1.8072.3.2.10) Tue 19:13:07"
    saved["device"] = saved["profile"]["device"]
    z = export.zabbix(saved)["zabbix_export"]
    t = z["templates"][0]
    assert re.fullmatch(r"[A-Za-z0-9 ._-]+", t["template"]) and len(t["template"]) <= 64
    assert t["template"] in yaml.safe_dump(z)
