"""Read-only SNMP access through the net-snmp command-line tools.

Only GET and (bulk)WALK are exposed; nothing here can SET a value on a device.
Output is requested with -On (numeric OIDs) and -Oe/-Ot (numeric enums and
timeticks) so parsing never depends on MIB files being installed.
"""
import re
import shutil
import subprocess
from dataclasses import dataclass

LINE = re.compile(r"^(\.?[0-9.]+) = (?:([A-Za-z0-9-]+): )?(.*)$")
MISSING = ("No Such Object", "No Such Instance", "No more variables")


@dataclass
class Var:
    oid: str        # numeric, no leading dot
    type: str       # Counter32, Gauge32, INTEGER, STRING, Timeticks, ... or "missing"
    value: str      # raw text as returned (quotes stripped from strings)


def parse(text: str) -> list[Var]:
    out = []
    for line in text.splitlines():
        m = LINE.match(line.strip())
        if not m:
            continue
        oid, typ, val = m.group(1).lstrip("."), m.group(2), m.group(3).strip()
        if typ is None:
            if any(val.startswith(x) for x in MISSING):
                typ = "missing"
            else:
                typ = "unknown"
        if typ == "STRING" and len(val) >= 2 and val[0] == val[-1] == '"':
            val = val[1:-1]
        out.append(Var(oid, typ, val))
    return out


class Snmp:
    """SNMPv2c read-only client. `runner` is injectable for tests."""

    def __init__(self, host: str = "localhost", community: str = "public", port: int | None = None,
                 timeout: float = 2.0, retries: int = 1, runner=None):
        self.target = f"{host}:{port}" if port else host
        self.base = ["-v2c", "-c", community, "-t", str(timeout), "-r", str(retries), "-On", "-Oe", "-Ot"]
        self.runner = runner or self._run
        self.calls = 0

    @staticmethod
    def _run(cmd: list[str]) -> str:
        if not shutil.which(cmd[0]):
            raise RuntimeError(f"{cmd[0]} not found: install net-snmp (apt install snmp)")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0 and not r.stdout:
            raise RuntimeError(r.stderr.strip() or f"{cmd[0]} failed")
        return r.stdout

    def get(self, *oids: str) -> list[Var]:
        self.calls += 1
        return parse(self.runner(["snmpget", *self.base, self.target, *oids]))

    def walk(self, oid: str) -> list[Var]:
        """All instances under `oid`. A scalar's single instance (<oid>.0) is returned too."""
        self.calls += 1
        rows = [v for v in parse(self.runner(["snmpbulkwalk", *self.base, self.target, oid]))
                if v.type != "missing" and (v.oid == oid.lstrip(".") or v.oid.startswith(oid.lstrip(".") + "."))]
        if rows:
            return rows
        # Scalars: walking <oid> may return nothing on some agents; try <oid>.0 directly.
        got = self.get(oid.lstrip(".").removesuffix(".0") + ".0")
        return [v for v in got if v.type != "missing"]
