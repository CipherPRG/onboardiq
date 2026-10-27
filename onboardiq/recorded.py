"""Serve a recorded SNMP walk (.snmprec, the snmpsim format) as a read-only device.

Used to test the verifier on real values captured from real devices (e.g. the LibreNMS
test recordings) without network access. Line format: <oid>|<type code>|<value>.
"""
from pathlib import Path

from .snmp import Var, check_oid

TYPES = {"2": "INTEGER", "4": "STRING", "4x": "Hex-STRING", "5": "NULL", "6": "OID", "64": "IpAddress",
         "65": "Counter32", "66": "Gauge32", "67": "Timeticks", "68": "Opaque", "70": "Counter64"}


def _key(oid):
    return tuple(int(x) for x in oid.split("."))


class RecordedSnmp:
    def __init__(self, path):
        self.rows = {}
        for line in Path(path).read_text(errors="replace").splitlines():
            parts = line.split("|", 2)
            if len(parts) != 3 or not parts[0][:1].isdigit():
                continue
            oid, code, val = parts
            try:
                k = _key(oid)
            except ValueError:
                continue
            typ = TYPES.get(code, "unknown")
            if code == "4x":   # hex-encoded string: show printable text the way snmpget would
                try:
                    txt = bytes.fromhex(val).rstrip(b"\0").decode("ascii")
                    if txt.isprintable():
                        typ, val = "STRING", txt
                except ValueError:
                    pass
            self.rows[k] = Var(oid, typ, val)
        self.sorted = sorted(self.rows)
        self.calls = 0

    def get(self, *oids):
        self.calls += 1
        out = []
        for o in oids:
            o = check_oid(o).lstrip(".")
            out.append(self.rows.get(_key(o)) or Var(o, "missing", "No Such Object"))
        return out

    def walk(self, oid):
        self.calls += 1
        p = _key(check_oid(oid).lstrip("."))
        return [self.rows[k] for k in self.sorted if k[:len(p)] == p]
