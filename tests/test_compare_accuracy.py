import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import compare_accuracy as ca  # noqa: E402


def run(rows):
    return {("v", f): {"oid": o, "oid_ok": ok} for f, (o, ok) in rows.items()}


def test_agree_and_disagree_counts():
    a = run({"cpu": ("1.1", True), "memory": ("2.1", False), "fan": ("3.1", True), "temperature": ("4.1", False)})
    b = run({"cpu": ("1.1", True), "memory": ("2.1", False), "fan": ("3.9", False), "temperature": ("4.9", False)})
    t = ca.compare(a, b)
    assert t == {"n": 4, "agree": 2, "agree_right": 1, "differ": 2, "only_a": 1, "only_b": 0, "neither": 1}


def test_loads_the_accuracy_test_file(tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps({"results": [{"vendor": "v", "mode": "with-mib", "scores": {"cpu": {"oid": "1", "oid_ok": True}}},
                                         {"vendor": "v", "mode": "baseline", "scores": {"cpu": {"oid": "2", "oid_ok": False}}}]}))
    assert ca.load(f, "with-mib") == {("v", "cpu"): {"oid": "1", "oid_ok": True}}
