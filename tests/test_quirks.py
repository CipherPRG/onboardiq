"""Quirks real devices show, as generic synthetic devices: does the verifier give the right verdict?

Each case is a behaviour seen on real equipment, written without any vendor or product names. A test that
documents a limit (not a fix) says so in its name and asserts the CURRENT behaviour, so the README's limits
table can quote it.
"""
from tests.test_verify import FAST, FakeDevice
from onboardiq.snmp import Var
from onboardiq.verify import Item, static_checks, verify_item


class Rows(FakeDevice):
    """A table: oid -> (type, [value per row])."""
    def walk(self, oid):
        if oid not in self.table:
            return []
        typ, vals = self.table[oid]
        return [Var(f"{oid}.{i + 1}", typ, str(v)) for i, v in enumerate(vals)]


def chk(dev, **kw):
    return static_checks(dev, Item(kw.pop("family", "memory"), "1.7.7.1", **kw))


def test_negative_used_memory_is_rejected():
    # a signed INTEGER 'allocated bytes' object went negative on a stacked device
    r = chk(Rows({"1.7.7.1": ("INTEGER", [-15_000_000])}), name="allocBytes", scale=1, canonical_unit="bytes")
    assert r.verdict == "rejected" and r.evidence[-1]["check"] == "range"


def test_sentinel_rows_in_a_sensor_column_need_a_filter_not_a_verdict():
    # one column: real temperatures plus 'no sensor' markers
    r = chk(Rows({"1.7.7.1": ("INTEGER", [41, 38, 2147483647, -32768])}), family="temperature",
            name="tempValue", scale=1, canonical_unit="celsius")
    assert r.verdict == "unproven" and r.partial


def test_string_readings_with_units_are_read_as_numbers():
    r = chk(Rows({"1.7.7.1": ("STRING", ["29C"])}), family="temperature", name="t", scale=1, canonical_unit="celsius")
    assert r.verdict == "proven-static"
    r = chk(Rows({"1.7.7.1": ("STRING", ["N/A"])}), family="temperature", name="t", scale=1, canonical_unit="celsius")
    assert r.verdict == "rejected"


def test_counter_read_as_gauge_is_rejected():
    r = chk(Rows({"1.7.7.1": ("Counter32", [51_000_000])}), family="cpu", name="cpuTicks", scale=1,
            canonical_unit="percent")
    assert r.verdict == "rejected" and r.evidence[-1]["check"] == "type"


def test_wrong_scale_is_corrected_by_suggestion():
    r = chk(Rows({"1.7.7.1": ("INTEGER", [452])}), family="temperature", name="t", scale=1, canonical_unit="celsius")
    assert r.verdict == "rejected" and r.suggested_scale == 0.1


def test_table_with_an_aggregate_first_row_is_judged_on_the_row_that_responds():
    # per-core CPU table where row 1 is an all-cores average that barely moves, row 2 is a busy core
    class Cpu(Rows):
        def walk(self, oid):
            rows = [3, 3] if not self.loaded else [4, 92]
            return [Var(f"{oid}.{i + 1}", "Gauge32", str(v)) for i, v in enumerate(rows)]
    dev = Cpu({"1.7.7.2": ("Gauge32", [0])})
    r = verify_item(dev, Item("cpu", "1.7.7.2", name="cpuLoad", scale=1, canonical_unit="percent", under_load="rise"),
                    lab=True, load=dev.load, **FAST)
    assert r.verdict == "proven-experiment" and any(e["check"] == "row" for e in r.evidence)


def test_counter_wrap_during_the_window_limit():
    """LIMIT (documented, not fixed): the per-second rate ignores a counter that fell between the first and last
    reading, so a raw tick counter that wraps 2^32 inside the load window shows no rise. Raw tick counters wrap
    about every 497 days at 100 ticks/s, so this is rare; the cost is a wrongly rejected pick, never a wrongly
    proven one, and the agent then chooses again."""
    from onboardiq.verify import _rate
    wrapped = [(0.0, {"r": 2 ** 32 - 1000.0}), (10.0, {"r": 4000.0})]       # really +5000 ticks
    assert _rate(wrapped) == 0.0
    normal = [(0.0, {"r": 1000.0}), (10.0, {"r": 6000.0})]
    assert _rate(normal) == 500.0
