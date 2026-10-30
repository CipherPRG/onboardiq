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


def implicit_root(text, error):
    m = re.search(r"Unknown parent symbol: ([\w-]+) at MIB", error or "")
    if not m or m.group(1) not in SMI_ROOTS:
        return None
    sym = m.group(1)
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
        return (fixed, f"imported {sym} from {smi}") if n else None
    smi = "SNMPv2-SMI" if "MODULE-IDENTITY" in text else "RFC1155-SMI"
    clause = f"{sym} FROM {smi}"
    if block:
        pos = block.end(1)
        return text[:pos] + f"\n        {clause}" + text[pos:], f"imported {sym} from {smi}"
    b = re.search(r"\bBEGIN\b", text)
    if not b:
        return None
    return text[:b.end()] + f"\nIMPORTS {clause};" + text[b.end():], f"imported {sym} from {smi}"


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


RULES = (trailing_comma, digit_enum_label, hyphen_identifier, implicit_root, duplicate_name)


def repair(text, error):
    """(new text, note) from the first rule that recognises the error and is sure of its fix, else None."""
    for rule in RULES:
        got = rule(text, error)
        if got and got[0] != text:
            return got
    return None
