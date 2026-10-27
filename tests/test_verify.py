"""Verifier logic against a fake device (no SNMP agent, no real load)."""
from onboardiq.snmp import Var, parse
from onboardiq.verify import Item, static_checks, verify_item

FAST = dict(base_s=0.02, load_s=0.05, interval=0.01, adapt_windows=False)


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
    "cpu.counter": ("Counter32", lambda l, t: t * (500 if l else 2)),
    "cpu.idle.counter": ("Counter32", lambda l, t: t * (2 if l else 900)),
    "cpu.pct": ("Gauge32", lambda l, t: 90 if l else 3),
    "cpu.idle": ("Gauge32", lambda l, t: 8 if l else 96),
    "mem.availKB": ("INTEGER", lambda l, t: 6_000_000 - (ALLOC // 1024 if l else 0)),
    "mem.totalKB": ("INTEGER", lambda l, t: 8_000_000),
    "temp.deci": ("INTEGER", lambda l, t: 452),
})


def run(item, lab=True):
    return verify_item(DEV, item, lab=lab, load=DEV.load, **FAST)


def test_not_served_is_rejected():
    r = run(Item("cpu", "hr.processor.load", canonical_unit="percent"))
    assert r.verdict == "rejected" and "not served" in r.evidence[-1]["detail"]


def test_counter_read_as_gauge_is_rejected():
    r = run(Item("cpu", "cpu.counter", value_type="gauge", canonical_unit="percent"))
    assert r.verdict == "rejected" and r.evidence[-1]["check"] == "type"


def test_counter_proven_under_cpu_load():
    assert run(Item("cpu", "cpu.counter", value_type="counter")).verdict == "proven-experiment"
    # idle-time counters slow down under load (found on the laptop: ssCpuRawIdle 1196/s -> 1.6/s)
    assert run(Item("cpu", "cpu.idle.counter", name="ssCpuRawIdle", value_type="counter")).verdict == "proven-experiment"
    assert run(Item("cpu", "cpu.idle.counter", name="cpuBusyTicks", value_type="counter")).verdict == "rejected"


def test_cpu_percent_and_idle_direction():
    assert run(Item("cpu", "cpu.pct", name="busyPct", canonical_unit="percent")).verdict == "proven-experiment"
    assert run(Item("cpu", "cpu.idle", name="ssCpuIdle", canonical_unit="percent")).verdict == "proven-experiment"
    # the same idle object claimed as a busy percentage moves the wrong way
    assert run(Item("cpu", "cpu.idle", name="cpuBusy", canonical_unit="percent")).verdict == "rejected"


def test_total_ram_constant_is_rejected():
    r = run(Item("memory", "mem.totalKB", name="hrMemorySize", scale=1024, canonical_unit="bytes"))
    assert r.verdict == "rejected" and "change +0" in r.evidence[-1]["detail"]


def test_memory_available_proven_and_wrong_scale_suggested():
    ok = run(Item("memory", "mem.availKB", name="memAvailReal", scale=1024, canonical_unit="bytes"))
    assert ok.verdict == "proven-experiment"
    bad = run(Item("memory", "mem.availKB", name="memAvailReal", scale=1, canonical_unit="bytes"))
    assert bad.verdict == "rejected" and bad.suggested_scale == 1024


def test_range_rejects_wrong_temperature_scale_and_suggests():
    r = static_checks(DEV, Item("temperature", "temp.deci", scale=1, canonical_unit="celsius"))
    assert r.verdict == "rejected" and r.suggested_scale == 0.1
    ok = static_checks(DEV, Item("temperature", "temp.deci", scale=0.1, canonical_unit="celsius"))
    assert ok.verdict == "proven-static"


def test_without_lab_cpu_memory_stay_unproven():
    r = verify_item(DEV, Item("cpu", "cpu.pct", canonical_unit="percent"), lab=False, observe_s=0.02)
    assert r.verdict == "unproven"


class TableDevice(FakeDevice):
    def walk(self, oid):
        self.tick += 1
        if oid != "hr.storage.used":
            return []
        ram = 2_000_000 + (ALLOC // 1024 if self.loaded else 0)   # kB units, RAM row responds
        return [Var(oid + ".1", "INTEGER", str(ram)), Var(oid + ".3", "INTEGER", "50000"),
                Var(oid + ".31", "INTEGER", "9000000")]


def test_table_row_that_responds_is_found():
    dev = TableDevice({})
    r = verify_item(dev, Item("memory", "hr.storage.used", name="hrStorageUsed", scale=1024,
                              canonical_unit="bytes"), lab=True, load=dev.load, **FAST)
    assert r.verdict == "proven-experiment"
    assert any(e["check"] == "row" and "row 1 " in e["detail"] for e in r.evidence)
