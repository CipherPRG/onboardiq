"""Read-only SNMP access through the net-snmp command-line tools.

Only GET and (bulk)WALK are exposed; nothing here can SET a value on a device.
Output is requested with -On (numeric OIDs) and -Oe/-Ot (numeric enums and
timeticks) so parsing never depends on MIB files being installed.
"""
import atexit
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

OID_RE = re.compile(r"^\.?\d+(?:\.\d+){1,127}$")
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


def check_oid(oid: str) -> str:
    """Only numeric OIDs reach the command line: an OID from a model or a file can never become an option."""
    if not isinstance(oid, str) or not OID_RE.match(oid.strip()):
        raise ValueError(f"refusing non-numeric OID {oid!r}")
    return oid.strip()


V3_AUTH = {"MD5", "SHA", "SHA-224", "SHA-256", "SHA-384", "SHA-512"}
V3_PRIV = {"DES", "AES", "AES-192", "AES-256"}


def credentials(body: dict) -> dict:
    """Keyword arguments for Snmp() from a form or JSON body: version, community and (for v3) the user, protocols
    and passphrases. Raises ValueError with a readable message. Nothing here is ever echoed back to the page."""
    version = str(body.get("version") or "2c")
    if version not in ("1", "2c", "3"):
        raise ValueError("SNMP version must be 1, 2c or 3")
    if version != "3":
        community = str(body.get("community") or "public")
        if len(community) > 64 or any(ord(c) < 32 for c in community):
            raise ValueError("bad community")
        return {"community": community, "version": version}
    v3 = {"user": str(body.get("v3_user") or "").strip(), "auth_proto": body.get("v3_auth_proto") or "SHA",
          "auth_pass": str(body.get("v3_auth_pass") or ""), "priv_proto": body.get("v3_priv_proto") or "AES",
          "priv_pass": str(body.get("v3_priv_pass") or "")}
    if not v3["user"] or len(v3["user"]) > 64:
        raise ValueError("SNMPv3 user name is required (up to 64 characters)")
    if len(v3["auth_pass"]) > 128 or len(v3["priv_pass"]) > 128:
        raise ValueError("passphrase too long")
    Snmp._write_conf("x", {k: v for k, v in v3.items() if v}, "3")   # validates everything; the temp dir is removed at exit
    return {"v3": {k: v for k, v in v3.items() if v}, "version": "3"}


class Snmp:
    """Read-only SNMP client (v2c or v3). `runner` is injectable for tests.

    Credentials never appear on a command line (where any local user could read them with ps):
    they go into a snmp.conf in a private temporary directory (mode 0700/0600) that net-snmp
    reads through SNMPCONFPATH, and the directory is deleted when the process exits.

    v3 = {"user": ..., "auth_proto": "SHA", "auth_pass": ..., "priv_proto": "AES", "priv_pass": ...}
    version = "1", "2c" (default) or "3" (v3 is implied when a v3 dict is given). Protocol names are checked
    against a short list (they go into a config file) and passphrases must be at least 8 characters, the net-snmp rule.
    """

    def __init__(self, host: str = "localhost", community: str = "public", port: int | None = None,
                 timeout: float = 2.0, retries: int = 1, runner=None, v3: dict | None = None, version: str = "2c"):
        if not re.match(r"^[A-Za-z0-9.:\[\]_-]{1,253}$", host or "") or host.startswith("-"):
            raise ValueError(f"refusing host {host!r}")
        self.target = f"{host}:{port}" if port else host
        self.base = ["-t", str(timeout), "-r", str(retries), "-On", "-Oe", "-Ot"]
        self.runner = runner or self._run
        self.calls = 0
        self._conf_dir = self._write_conf(community, v3, version)

    @staticmethod
    def _write_conf(community, v3, version="2c"):
        def clean(x):
            x = str(x)
            if any(c in x for c in "\r\n\0") or not x:
                raise ValueError("credential contains a line break, NUL or is empty")
            return x

        def proto(x, allowed, what):
            x = str(x).upper()
            if x not in allowed:
                raise ValueError(f"unknown {what} protocol {x!r}; choose one of {', '.join(sorted(allowed))}")
            return x

        def phrase(x, what):
            x = clean(x)
            if len(x) < 8:
                raise ValueError(f"{what} passphrase must be at least 8 characters (net-snmp rule)")
            return x
        if v3 or str(version) == "3":
            if not v3 or not v3.get("user") or not v3.get("auth_pass"):
                raise ValueError("SNMPv3 needs a user name and an authentication passphrase")
            lines = ["defVersion 3", f"defSecurityName {clean(v3['user'])}"]
            if v3.get("priv_pass"):
                lines += ["defSecurityLevel authPriv",
                          f"defPrivType {proto(v3.get('priv_proto', 'AES'), V3_PRIV, 'privacy')}",
                          f"defPrivPassphrase {phrase(v3['priv_pass'], 'privacy')}"]
            else:
                lines += ["defSecurityLevel authNoPriv"]
            lines += [f"defAuthType {proto(v3.get('auth_proto', 'SHA'), V3_AUTH, 'authentication')}",
                      f"defAuthPassphrase {phrase(v3['auth_pass'], 'authentication')}"]
        else:
            if str(version) not in ("1", "2c"):
                raise ValueError(f"unknown SNMP version {version!r}")
            lines = [f"defVersion {version}", f"defCommunity {clean(community)}"]
        d = tempfile.mkdtemp(prefix="oiq-snmp-")
        os.chmod(d, 0o700)
        f = os.path.join(d, "snmp.conf")
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        atexit.register(shutil.rmtree, d, True)
        return d

    def _run(self, cmd: list[str]) -> str:
        if not shutil.which(cmd[0]):
            raise RuntimeError(f"{cmd[0]} not found: install net-snmp (apt install snmp)")
        env = {**os.environ, "SNMPCONFPATH": self._conf_dir, "MIBS": ""}
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
        if r.returncode != 0 and not r.stdout:
            raise RuntimeError(r.stderr.strip() or f"{cmd[0]} failed")
        return r.stdout

    def get(self, *oids: str) -> list[Var]:
        oids = [check_oid(o) for o in oids]
        self.calls += 1
        return parse(self.runner(["snmpget", *self.base, "--", self.target, *oids]))

    def walk(self, oid: str) -> list[Var]:
        """All instances under `oid`. A scalar's single instance (<oid>.0) is returned too."""
        oid = check_oid(oid)
        self.calls += 1
        rows = [v for v in parse(self.runner(["snmpbulkwalk", *self.base, "--", self.target, oid]))
                if v.type != "missing" and (v.oid == oid.lstrip(".") or v.oid.startswith(oid.lstrip(".") + "."))]
        if rows:
            return rows
        # Scalars: walking <oid> may return nothing on some agents; try <oid>.0 directly.
        got = self.get(oid.lstrip(".").removesuffix(".0") + ".0")
        return [v for v in got if v.type != "missing"]
