"""Narrow repairs for common mistakes in vendor MIB files, applied to a temporary copy only.

The converter (pysmi) is stricter than the tools vendors test against, so many real MIBs are rejected for one
small slip. Each rule below recognises one such slip from the converter's own error message and the text at the
place it names, fixes exactly that, and says what it did. A rule that is not sure returns None (no guess).

  - the user's MIB files are never changed: intake works on copies in a temporary folder;
  - every repair is logged (intake writes `_repairs.json` next to the converted JSON) so nothing is silent;
  - a repaired module is converted from the repaired text, so its JSON may differ from the vendor's file by
    exactly the logged edit.

Rules (error message -> fix):
  trailing comma     "Bad grammar", stopped at '}' right after a ','        -> drop the comma
  digit enum label   "Bad grammar", stopped at  100(0)  in INTEGER { ... } -> label becomes n100
  hyphen identifier  "Identifier should not end with '-'"                  -> drop the trailing '-'
  implicit root      "Unknown parent symbol: enterprises" (and kin)        -> import it from the SMI module
  duplicate name     "Duplicate symbol found: X"                           -> drop an identical repeat of the
                                                                              definition, else rename the repeat
  unimported type    "Unknown parents for symbols: ...", their SYNTAX type not imported -> import it (Counter -> Counter32)
  missing comma      "Bad grammar", stopped at  web(4)  right after  snmp(3) -> add the comma
  repeated TC        "Duplicate symbol found: X", X a TEXTUAL-CONVENTION     -> rename the later copy
"""
import re

# Names every SMI module defines; MIBs sometimes use them without importing them.
SMI_ROOTS = {"iso", "org", "dod", "internet", "directory", "mgmt", "mib-2", "transmission", "experimental", "private",
             "enterprises", "security", "snmpV2", "snmpDomains", "snmpProxys", "snmpModules"}
DEF_KINDS = (r"OBJECT[ \t]+IDENTIFIER|OBJECT-TYPE|OBJECT-IDENTITY|MODULE-IDENTITY|NOTIFICATION-TYPE|TRAP-TYPE|"
             r"OBJECT-GROUP|NOTIFICATION-GROUP|MODULE-COMPLIANCE")
ASSIGN = re.compile(r"::=\s*\{[^}]*\}")


def _line(text, pos):
    return text.count("\n", 0, pos) + 1


def _offset(error):
    m = re.search(r"Bad grammar near offset (\d+)", error or "")
    return int(m.group(1)) if m else None


def trailing_comma(text, error):
    off = _offset(error)
    if off is None or off >= len(text) or text[off] != "}":
        return None
    j = off - 1
    while j >= 0 and text[j] in " \t\r\n":
        j -= 1
    if j < 0 or text[j] != ",":
        return None
    return text[:j] + text[j + 1:], f"removed a comma before '}}' (line {_line(text, j)})"


def digit_enum_label(text, error):
    off = _offset(error)
    if off is None:
        return None
    if not re.match(r"\d[\w-]*[ \t]*\(\s*-?\d+\s*\)", text[off:off + 80]):
        return None
    start = text.rfind("{", 0, off)
    end = text.find("}", off)
    if start < 0 or end < 0:
        return None
    group = text[start:end]
    fixed, n = re.subn(r"(?<=[{,\s])(\d[\w-]*)(?=\s*\(\s*-?\d+\s*\))", r"n\1", group)
    if not n:
        return None
    return text[:start] + fixed + text[end:], \
        f"renamed {n} enumeration label{'s' if n != 1 else ''} that started with a digit (line {_line(text, off)})"


def hyphen_identifier(text, error):
    m = re.search(r"Identifier should not end with '-': (\S+) at MIB", error or "")
    if not m:
        return None
    bad = m.group(1)
    good = bad.rstrip("-")
    word = r"(?<![\w-])%s(?![\w-])"
    if not good or re.search(word % re.escape(good), text):
        return None                                    # the fixed name would clash with a real one: do not guess
    fixed, n = re.subn(word % re.escape(bad), good, text)
    return (fixed, f"renamed {bad} to {good} ({n} place{'s' if n != 1 else ''})") if n else None


def _add_import(text, sym):
    """(new text, smi module) with `sym` imported from the SMI module the file already uses, or None."""
    block = re.search(r"\bIMPORTS\b(.*?);", text, re.S)
    if block and re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(sym), block.group(1)):
        return None                                    # already imported: something else is wrong
    smi = None
    if block:
        for cand in ("SNMPv2-SMI", "RFC1155-SMI"):
            if re.search(r"\bFROM\s+%s\b" % cand, block.group(1)):
                smi = cand
                break
    if smi:                                            # add to the clause the module already has
        fixed, n = re.subn(r"(\s+)FROM(\s+%s\b)" % smi, r", %s\1FROM\2" % sym, text, count=1)
        return (fixed, smi) if n else None
    smi = "SNMPv2-SMI" if "MODULE-IDENTITY" in text else "RFC1155-SMI"
    clause = f"{sym} FROM {smi}"
    if block:
        pos = block.end(1)
        return text[:pos] + f"\n        {clause}" + text[pos:], smi
    b = re.search(r"\bBEGIN\b", text)
    if not b:
        return None
    return text[:b.end()] + f"\nIMPORTS {clause};" + text[b.end():], smi


def implicit_root(text, error):
    m = re.search(r"Unknown parent symbol: ([\w-]+) at MIB", error or "")
    if not m or m.group(1) not in SMI_ROOTS:
        return None
    got = _add_import(text, m.group(1))
    return (got[0], f"imported {m.group(1)} from {got[1]}") if got else None


# SMI types a MIB may use without importing them; SMIv1 spellings map to the SMIv2 type
SMI_TYPES = {"Counter32": "Counter32", "Counter64": "Counter64", "Gauge32": "Gauge32", "Unsigned32": "Unsigned32",
             "Integer32": "Integer32", "TimeTicks": "TimeTicks", "IpAddress": "IpAddress", "Opaque": "Opaque",
             "Counter": "Counter32", "Gauge": "Gauge32"}


def implicit_type(text, error):
    """"Unknown parents for symbols: a, b": the objects were dropped because their SYNTAX type is not imported."""
    m = re.search(r"Unknown parents for symbols: ([^)]*?) at MIB", error or "")
    if not m:
        return None
    block = re.search(r"\bIMPORTS\b(.*?);", text, re.S)
    imported = block.group(1) if block else ""
    done = []
    for sym in [x.strip() for x in m.group(1).split(",") if x.strip()]:
        d = re.search(r"^[ \t]*%s[ \t]+OBJECT-TYPE\b(.*?)::=" % re.escape(sym), text, re.M | re.S)
        t = re.search(r"\bSYNTAX\s+([A-Za-z0-9]+)", d.group(1)) if d else None
        if not t or t.group(1) not in SMI_TYPES or SMI_TYPES[t.group(1)] in done:
            continue
        want = SMI_TYPES[t.group(1)]
        if not re.search(r"(?<![\w-])%s(?![\w-])" % want, imported):
            done.append(want)
    if not done:
        return None
    out, notes = text, []
    for want in done:
        if want == "Counter32" and not re.search(r"\bCounter\b", imported):
            out = re.sub(r"(\bSYNTAX\s+)Counter(?![\w-])", r"\1Counter32", out)
        if want == "Gauge32":
            out = re.sub(r"(\bSYNTAX\s+)Gauge(?![\w-])", r"\1Gauge32", out)
        got = _add_import(out, want)
        if got:
            out = got[0]
            notes.append(want)
    return (out, "imported " + ", ".join(notes) + " (used but not imported)") if notes else None


def duplicate_name(text, error):
    m = re.search(r"Duplicate symbol found: (\S+) at MIB", error or "")
    if not m:
        return None
    sym = m.group(1)
    pat = re.escape(sym).replace("_", "[-_]").replace(r"\-", "[-_]")
    defs = list(re.finditer(r"^[ \t]*(%s)[ \t]*(?:\r?\n[ \t]*)?(?:%s)\b" % (pat, DEF_KINDS), text, re.M | re.I))
    if len(defs) < 2:
        return None
    first = ASSIGN.search(text, defs[0].end())
    first_clause = re.sub(r"\s+", " ", first.group(0)) if first else None
    notes, out = [], text
    for k, d in reversed(list(enumerate(defs))[1:]):    # from the end, so earlier positions stay valid
        end = ASSIGN.search(out, d.end())
        if end and first_clause and re.sub(r"\s+", " ", end.group(0)) == first_clause:
            out = out[:d.start()] + out[end.end():]
            notes.append(f"dropped a repeat of {sym} (line {_line(text, d.start())}), identical to the first")
        else:
            out = out[:d.start(1)] + f"{d.group(1)}Dup{k + 1}" + out[d.end(1):]
            notes.append(f"renamed the repeated definition of {sym} at line {_line(text, d.start())} to {d.group(1)}Dup{k + 1}")
    return out, "; ".join(reversed(notes))


def missing_comma(text, error):
    """`snmp(3) web(4)`: two enumeration items with no comma between them."""
    off = _offset(error)
    if off is None or off >= len(text):
        return None
    if not re.match(r"[A-Za-z][\w-]*[ \t]*\(\s*-?\d+\s*\)", text[off:off + 80]):
        return None
    j = off - 1
    while j >= 0 and text[j] in " \t\r\n":
        j -= 1
    if j < 0 or text[j] != ")":
        return None
    start = text.rfind("{", 0, off)
    if start < 0 or "}" in text[start:off]:
        return None                                    # not inside a { ... } list
    return text[:j + 1] + "," + text[j + 1:], f"added a missing comma between enumeration items (line {_line(text, off)})"


def duplicate_convention(text, error):
    """A TEXTUAL-CONVENTION defined twice: the later copy is renamed (uses of the name keep the first)."""
    m = re.search(r"Duplicate symbol found: (\S+) at MIB", error or "")
    if not m:
        return None
    pat = re.escape(m.group(1)).replace("_", "[-_]").replace(r"\-", "[-_]")
    defs = list(re.finditer(r"^[ \t]*(%s)[ \t]*::=[ \t]*TEXTUAL-CONVENTION\b" % pat, text, re.M | re.I))
    if len(defs) < 2:
        return None
    out, notes = text, []
    for k, d in reversed(list(enumerate(defs))[1:]):
        out = out[:d.start(1)] + f"{d.group(1)}Dup{k + 1}" + out[d.end(1):]
        notes.append(f"renamed the repeated TEXTUAL-CONVENTION {d.group(1)} at line {_line(text, d.start())}")
    return out, "; ".join(reversed(notes))


RULES = (trailing_comma, digit_enum_label, missing_comma, hyphen_identifier, implicit_root, implicit_type,
         duplicate_name, duplicate_convention)


def repair(text, error):
    """(new text, note) from the first rule that recognises the error and is sure of its fix, else None."""
    for rule in RULES:
        got = rule(text, error)
        if got and got[0] != text:
            return got
    return None
