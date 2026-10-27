"""Verifier logic against a fake device (no SNMP agent, no real load)."""
from onboardiq.snmp import Var, parse
from onboardiq.verify import Item, static_checks, verify_item

FAST = dict(base_s=0.02, load_s=0.05, interval=0.01, adapt_windows=False, witness_interval=0.001, observe_interval=0.001)


def test_parse_types_and_missing():
    out = parse('.1.3.6.1.4.1.2021.11.50.0 = Counter32: 845613\n'
                '.1.3.6.1.4.1.2021.10.1.3.1 = STRING: "0.71"\n'
                '.1.3.6.1.4.1.2021.11.9.0 = No Such Instance currently exists at this OID\n')
    assert [(v.oid, v.type, v.value) for v in out] == [
        ("1.3.6.1.4.1.2021.11.50.0", "Counter32", "845613"),
        ("1.3.6.1.4.1.2021.10.1.3.1", "STRING", "0.71"),
        ("1.3.6.1.4.1.2021.11.9.0", "missing", "No Such Instance currently exists at this OID")]


class FakeDevice:
    """oid -> (type, f(loaded, tick)); `loaded` flips while the fake load runs."""
    def __init__(self, table):
        self.table, self.loaded, self.tick = table, False, 0

    def walk(self, oid):
        self.tick += 1
        if oid not in self.table:
            return []
        typ, f = self.table[oid]
        return [Var(oid + ".0", typ, str(f(self.loaded, self.tick)))]

    def load(self, kind, seconds, size):
        dev = self
        dev.loaded = True

        class P:
            def terminate(self):
                dev.loaded = False

            def wait(self, timeout=None):
                return 0
        return P()


ALLOC = 1024 ** 3  # experiment allocates min(1 GiB, 25% RAM); test machines have >= 4 GiB
DEV = FakeDevice({
    "1.9.9.1": ("Counter32", lambda l, t: t * (500 if l else 2)),
    "1.9.9.2": ("Counter32", lambda l, t: t * (2 if l else 900)),
    "1.9.9.3": ("Gauge32", lambda l, t: 90 if l else 3),
    "1.9.9.4": ("Gauge32", lambda l, t: 8 if l else 96),
    "1.9.9.5": ("INTEGER", lambda l, t: 6_000_000 - (ALLOC // 1024 if l else 0)),
    "1.9.9.6": ("INTEGER", lambda l, t: 8_000_000),
    "1.9.9.7": ("INTEGER", lambda l, t: 452),
})


def run(item, lab=True):
    return verify_item(DEV, item, lab=lab, load=DEV.load, **FAST)


def test_not_served_is_rejected():
    r = run(Item("cpu", "1.9.9.9", canonical_unit="percent"))
    assert r.verdict == "rejected" and "not served" in r.evidence[-1]["detail"]


def test_counter_read_as_gauge_is_rejected():
    r = run(Item("cpu", "1.9.9.1", value_type="gauge", canonical_unit="percent"))
    assert r.verdict == "rejected" and r.evidence[-1]["check"] == "type"


def test_counter_proven_under_cpu_load():
    assert run(Item("cpu", "1.9.9.1", value_type="counter")).verdict == "proven-experiment"
    # idle-time counters slow down under load (found on the laptop: ssCpuRawIdle 1196/s -> 1.6/s)
    assert run(Item("cpu", "1.9.9.2", name="ssCpuRawIdle", value_type="counter")).verdict == "proven-experiment"
    assert run(Item("cpu", "1.9.9.2", name="cpuBusyTicks", value_type="counter")).verdict == "rejected"


def test_cpu_percent_and_idle_direction():
    assert run(Item("cpu", "1.9.9.3", name="busyPct", canonical_unit="percent")).verdict == "proven-experiment"
    assert run(Item("cpu", "1.9.9.4", name="ssCpuIdle", canonical_unit="percent")).verdict == "proven-experiment"
    # the same idle object claimed as a busy percentage moves the wrong way
    assert run(Item("cpu", "1.9.9.4", name="cpuBusy", canonical_unit="percent")).verdict == "rejected"


def test_total_ram_constant_is_rejected():
    r = run(Item("memory", "1.9.9.6", name="hrMemorySize", scale=1024, canonical_unit="bytes"))
    assert r.verdict == "rejected" and "change +0" in r.evidence[-1]["detail"]


def test_memory_available_proven_and_wrong_scale_suggested():
    ok = run(Item("memory", "1.9.9.5", name="memAvailReal", scale=1024, canonical_unit="bytes"))
    assert ok.verdict == "proven-experiment"
    bad = run(Item("memory", "1.9.9.5", name="memAvailReal", scale=1, canonical_unit="bytes"))
    assert bad.verdict == "rejected" and bad.suggested_scale == 1024


def test_range_rejects_wrong_temperature_scale_and_suggests():
    r = static_checks(DEV, Item("temperature", "1.9.9.7", scale=1, canonical_unit="celsius"))
    assert r.verdict == "rejected" and r.suggested_scale == 0.1
    ok = static_checks(DEV, Item("temperature", "1.9.9.7", scale=0.1, canonical_unit="celsius"))
    assert ok.verdict == "proven-static"


def test_without_lab_cpu_memory_stay_unproven():
    r = verify_item(DEV, Item("cpu", "1.9.9.3", canonical_unit="percent"), lab=False, observe_s=0.02, observe_interval=0.001)
    assert r.verdict == "unproven"


class TableDevice(FakeDevice):
    def walk(self, oid):
        self.tick += 1
        if oid != "1.9.9.8":
            return []
        ram = 2_000_000 + (ALLOC // 1024 if self.loaded else 0)   # kB units, RAM row responds
        return [Var(oid + ".1", "INTEGER", str(ram)), Var(oid + ".3", "INTEGER", "50000"),
                Var(oid + ".31", "INTEGER", "9000000")]


def test_table_row_that_responds_is_found():
    dev = TableDevice({})
    r = verify_item(dev, Item("memory", "1.9.9.8", name="hrStorageUsed", scale=1024,
                              canonical_unit="bytes"), lab=True, load=dev.load, **FAST)
    assert r.verdict == "proven-experiment"
    assert any(e["check"] == "row" and "row 1 " in e["detail"] for e in r.evidence)



def test_model_prediction_beats_name_rule():
    loads = []

    def counting_load(*a):
        loads.append(a)
        return DEV.load(*a)
    # name rule would expect "memLeft" to rise; the model says it falls, and the device agrees
    r = verify_item(DEV, Item("memory", "1.9.9.5", name="memLeft", scale=1024, canonical_unit="bytes",
                              under_load="fall"), lab=True, load=counting_load, **{k: v for k, v in FAST.items()})
    assert r.verdict == "proven-experiment" and "per model" in r.evidence[-1]["detail"]
    assert any(e["check"] == "direction" for e in r.evidence)
    # the model predicts idle rises under load: the device disagrees
    assert run(Item("cpu", "1.9.9.4", name="ssCpuIdle", canonical_unit="percent", under_load="rise")).verdict == "rejected"
    # the model predicts no movement: nothing to prove, and no load is started
    n = len(loads)
    r = verify_item(DEV, Item("cpu", "1.9.9.3", canonical_unit="percent", under_load="none"), lab=True,
                    load=counting_load, **FAST)
    assert r.verdict == "unproven" and len(loads) == n


def test_benchmark_schema_unchanged():
    from onboardiq import reader
    assert "under_load" not in reader.PICK["properties"]
    assert "under_load" in reader.PICK_AGENT["required"]



def test_load_by_hand_prompts_and_skips_memory():
    from onboardiq.verify import prompt_load
    asked, said = [], []
    loader = prompt_load({"cpu": "run a busy loop"}, ask=lambda m: asked.append(m) or "", say=said.append)

    def hand(kind, seconds, size):
        p = loader(kind, seconds, size)
        DEV.loaded = True
        stop = p.terminate

        class P:
            def terminate(self):
                DEV.loaded = False
                stop()

            def wait(self, timeout=None):
                return p.wait()
        return P()
    r = verify_item(DEV, Item("cpu", "1.9.9.3", name="busyPct", canonical_unit="percent"), lab=True, load=hand, **FAST)
    assert r.verdict == "proven-experiment" and len(asked) == 2 and any("START CPU" in s for s in said)
    r = verify_item(DEV, Item("memory", "1.9.9.5", name="memAvail", scale=1024, canonical_unit="bytes"),
                    lab=True, load=hand, **FAST)
    assert r.verdict == "unproven" and "by hand" in r.evidence[-1]["detail"]



def test_busy_baseline_is_inconclusive_not_rejected():
    dev = FakeDevice({"1.9.9.20": ("Gauge32", lambda l, t: 95)})   # already loaded before the test
    r = verify_item(dev, Item("cpu", "1.9.9.20", name="busyPct", canonical_unit="percent"), lab=True,
                    load=dev.load, **FAST)
    assert r.verdict == "unproven" and "already busy" in r.evidence[-1]["detail"]


WDEV = FakeDevice({
    "1.9.8.1": ("INTEGER", lambda l, t: 452),        # vendor temperature, tenths of a degree
    "1.9.8.2": ("INTEGER", lambda l, t: 45),         # ENTITY-SENSOR temperature, whole degrees: agrees
    "1.9.8.3": ("INTEGER", lambda l, t: 71),         # some other sensor: disagrees
})


def temp(witness, oid="1.9.8.1", scale=0.1):
    return Item("temperature", oid, name="vendorTemp", scale=scale, canonical_unit="celsius", witness=witness)


def test_witness_agreement_proves_temperature():
    r = verify_item(WDEV, temp({"oid": "1.9.8.2", "object_name": "entPhySensorValue", "scale_to_canonical": 1}),
                    observe_s=0.01, witness_interval=0.001, observe_interval=0.001)
    assert r.verdict == "proven-witness" and r.evidence[-1]["ok"] is True


def test_witness_disagreement_blocks_proof():
    r = verify_item(WDEV, temp({"oid": "1.9.8.3", "object_name": "otherSensor", "scale_to_canonical": 1}),
                    observe_s=0.01, witness_interval=0.001, observe_interval=0.001)
    assert r.verdict == "unproven" and r.evidence[-1]["ok"] is False and "disagree" in r.evidence[-1]["detail"]


def test_witness_missing_or_self_changes_nothing():
    r = verify_item(WDEV, temp({"oid": "1.9.8.99", "object_name": "absent", "scale_to_canonical": 1}),
                    observe_s=0.01, witness_interval=0.001, observe_interval=0.001)
    assert r.verdict == "proven-static" and "not served" in r.evidence[-1]["detail"]
    r = verify_item(WDEV, temp({"oid": "1.9.8.1", "object_name": "vendorTemp", "scale_to_canonical": 0.1}),
                    observe_s=0.01, witness_interval=0.001, observe_interval=0.001)
    assert r.verdict == "proven-static" and "not a usable" in r.evidence[-1]["detail"]


def test_wrong_scale_shows_up_as_witness_disagreement():
    # pick scale 1 for tenths: 452 C fails the range check before the witness even runs
    r = verify_item(WDEV, temp({"oid": "1.9.8.2", "object_name": "ent", "scale_to_canonical": 1}, scale=1),
                    observe_s=0.01, witness_interval=0.001, observe_interval=0.001)
    assert r.verdict == "rejected" and r.suggested_scale == 0.1
