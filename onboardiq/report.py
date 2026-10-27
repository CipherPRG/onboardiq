"""Render an onboarding session as one self-contained HTML page (a test certificate).

Every string that came from a model, a MIB or a device is HTML-escaped: a MIB description
or a model answer can never inject markup or script into the page.
"""
import html
import re
from datetime import datetime

VERDICT = {
    "proven-experiment": ("Proven", "by experiment on the device", "ok"),
    "proven-static": ("Proven", "by static checks", "ok"),
    "unproven": ("Not proven", "passed checks that could run, not tested under load", "warn"),
    "not-supported": ("Not on this device", "the device does not serve this object", "muted"),
    "rejected": ("Rejected", "failed on the device", "bad"),
}
FAMILY = {"cpu": "CPU", "memory": "Memory", "temperature": "Temperature", "fan": "Fan"}


def e(x):
    return html.escape("" if x is None else str(x), quote=True)


def _fmt(v, unit):
    if v is None:
        return "-"
    if unit == "bytes":
        for u, d in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
            if abs(v) >= d:
                return f"{v / d:.2f} {u}"
        return f"{v:.0f} B"
    if unit == "percent":
        return f"{v:.0f}%"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:.3g}"


def chart(series, unit, verdict):
    """Inline SVG: baseline then under-load samples, load window shaded."""
    if not series or not (series.get("base") or series.get("load")):
        return ""
    pts = series["base"] + series["load"]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    W, H, L, R, T, B = 480, 180, 62, 10, 14, 28
    x0, x1 = min(xs), max(xs) or 1
    lo, hi = min(ys), max(ys)
    if hi == lo:
        pad = abs(hi) * 0.1 or 1
        lo, hi = lo - pad, hi + pad
    else:
        pad = (hi - lo) * 0.15
        lo, hi = lo - pad, hi + pad
    if min(ys) >= 0:
        lo = max(lo, 0)
    if unit == "percent":
        lo, hi = max(lo, 0), min(max(hi, 10), 105)

    def X(x):
        return L + (x - x0) / ((x1 - x0) or 1) * (W - L - R)

    def Y(y):
        return T + (hi - y) / ((hi - lo) or 1) * (H - T - B)
    load_start = series["load"][0][0] if series["load"] else x1
    base_end = series["base"][-1][0] if series["base"] else x0
    band_x = X((base_end + load_start) / 2) if series["base"] else X(load_start)
    path = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(pts))
    dots = "".join(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="2.6"/>' for x, y in pts)
    ticks = "".join(
        f'<line x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="grid"/>'
        f'<text x="{L - 8}" y="{Y(v) + 4:.1f}" text-anchor="end">{e(_fmt(v, unit))}</text>'
        for v in (lo + (hi - lo) * k / 3 for k in range(4)))
    multi = series.get("rows", 1) > 1
    what = "rate per second" if series.get("counter") else ("responding row" if multi else "value")
    return (f'<figure class="chart {e(verdict)}"><svg viewBox="0 0 {W} {H}" role="img" '
            f'aria-label="Measured {e(what)} before and during load">'
            f'<rect x="{band_x:.1f}" y="{T}" width="{W - R - band_x:.1f}" height="{H - T - B}" class="band"/>'
            f'<text x="{band_x + 6:.1f}" y="{T + 14}" class="bandlabel">under load</text>'
            f'<text x="{L + 4}" y="{T + 14}" class="bandlabel">baseline</text>'
            f'{ticks}<path d="{path}" class="line"/><g class="dots">{dots}</g>'
            f'<text x="{L}" y="{H - 8}">{e(_fmt(x0, None))} s</text>'
            f'<text x="{W - R}" y="{H - 8}" text-anchor="end">{e(_fmt(x1, None))} s</text></svg>'
            f'<figcaption>Measured {e(what)}'
            + (f' (table row {e(str(series["row"]).rsplit(".", 1)[-1])})'
               if series.get("row") and not str(series["row"]).endswith(".0") and series.get("rows", 2) > 1 else "")
            + '</figcaption></figure>')


def attempt_block(t, reasoning_after=None):
    p = t.get("pick") or {}
    if t.get("family") not in ("cpu", "memory"):
        p = {**p, "under_load": None}
    unit = p.get("canonical_unit")
    title, sub, tone = VERDICT.get(t["verdict"], (t["verdict"], "", "muted"))
    if t["verdict"] == "rejected" and any("not served" in (c.get("detail") or "") for c in t.get("evidence", [])):
        title, sub, tone = "Not served", "this device does not have this object", "muted"
    checks = "".join(
        f'<li class="{"pass" if c["ok"] else ("fail" if c["ok"] is False else "info")}">'
        f'<span class="mark" aria-hidden="true">{"✓" if c["ok"] else ("✗" if c["ok"] is False else "·")}</span>'
        f'<span class="what">{e(c["check"])}</span> {e(c["detail"])}</li>' for c in t.get("evidence", []))
    pred = {"rise": "should rise under load", "fall": "should fall under load",
            "none": "should not move under load"}.get(p.get("under_load"), "")
    out = (f'<div class="attempt"><div class="claim">'
           f'<p class="n">Attempt {e(t["attempt"])}</p>'
           f'<h4>{e(p.get("object_name"))}</h4><p class="oid">{e(p.get("oid"))}'
           + '</p>'
           + (f'<p class="pred">Scaled ×{e(format(float(p.get("scale_to_canonical")), "g"))} to {e(unit)}.</p>'
              if p.get("scale_to_canonical") not in (None, 1, 1.0) and unit and unit != "none" else "")
           + (f'<p class="pred">The model predicted it {e(pred)}.</p>' if pred else "")
           + (f'<blockquote>{e(p.get("evidence"))}</blockquote>' if p.get("evidence") else "")
           + f'</div><div class="test">{chart(t.get("series"), unit, tone)}'
           f'<p class="stamp {tone}"><strong>{e(title)}</strong> {e(sub)}</p>'
           f'<ul class="checks">{checks}</ul></div></div>')
    if reasoning_after:
        parts = re.split(r"(?<=[.;])\s+", reasoning_after.strip())
        lead, rest = " ".join(parts[:2]), " ".join(parts[2:])
        out += (f'<div class="reread"><p><strong>Re-read the manual with this evidence.</strong> {e(lead)}</p>'
                + (f'<details><summary>Full reasoning</summary><p>{e(rest)}</p></details>' if rest else "")
                + '</div>')
    return out


def render(out: dict, title: str = None) -> str:
    trace, spec = out.get("trace", []), out.get("spec", {})
    fams = []
    for fam, f in spec.items():
        verifies = [t for t in trace if t["kind"] == "verify" and t["family"] == fam]
        repicks = {t["attempt"]: t.get("reasoning") for t in trace if t["kind"] == "repick" and t["family"] == fam}
        blocks = "".join(attempt_block(t, repicks.get(t["attempt"] + 1)) for t in verifies)
        if not verifies:
            blocks = '<p class="none">The model found no object for this metric in the manual.</p>'
        vt, vs, tone = VERDICT.get(f["verdict"], (f["verdict"], "", "muted"))
        fams.append(f'<section class="family" aria-labelledby="h-{e(fam)}">'
                    f'<header><h3 id="h-{e(fam)}">{e(FAMILY.get(fam, fam))}</h3>'
                    f'<p class="final {tone}">{e(vt)} <span>{e(vs)}</span>'
                    + (f' <span>after {f["attempts"]} attempts</span>' if f["attempts"] > 1 else "")
                    + f'</p></header>{blocks}</section>')
    proven = sum(f["verdict"].startswith("proven") for f in spec.values())
    absent = sum(f["verdict"] == "not-supported" for f in spec.values())
    # corrected = a family where a pick was rejected on the device and a later pick was proven
    caught = sum(1 for fam, f in spec.items() if f["verdict"].startswith("proven") and any(
        t["kind"] == "verify" and t["family"] == fam and t["verdict"] == "rejected" for t in trace))
    replay = out.get("replay")
    mode = "Replay of a recorded live session" if replay else "Live on the device"
    summary = (f'{proven} of {len(spec)} metrics proven on the device'
               + (f', {absent} not available on this device' if absent else "")
               + (f'; the agent caught and corrected {caught} wrong pick{"s" if caught != 1 else ""}'
                  if caught else "") + ".")
    css = CSS
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src data:">
<title>{e(title or "Onboarding record")}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>{css}</style></head><body><main>
<header class="top"><p class="brand">OnboardIQ onboarding record</p>
<h1>{e(out.get("device"))}</h1><p class="lede">{e(summary)}</p>
<dl class="meta"><div><dt>Mode</dt><dd>{e(mode)}</dd></div>
<div><dt>Verification time</dt><dd>{e(round((replay or {}).get("virtual_seconds") or out.get("seconds") or 0))} s</dd></div>
<div><dt>SNMP queries</dt><dd>{e(out.get("snmp_calls"))} (read-only)</dd></div>
<div><dt>Model cost</dt><dd>{"from the recording" if replay else "$" + format(out.get("cost_usd", 0), ".3f")}</dd></div>
<div><dt>Generated</dt><dd>{e(datetime.now().strftime("%Y-%m-%d %H:%M"))}</dd></div></dl></header>
{''.join(fams)}
<footer>{"<p>In a replay, device readings and model answers come from the recording of the live run; the verifier recomputes every check from them.</p>" if replay else ""}<p>Each metric shows what the model chose from the vendor manual, the line it quoted, how it predicted the value
would move under load, and what the device actually did. Verdicts come from measurements, not from the model.</p></footer>
</main></body></html>"""


CSS = """
:root{--paper:#EDF1F4;--sheet:#F8FAFB;--ink:#1B2A3A;--soft:#5A6B7B;--rule:#C9D3DC;--band:#DCE5ED;
--ok:#0E7A6B;--bad:#B8202E;--warn:#A86A10;--muted:#6C7A88;--line:#1B2A3A}
@media (prefers-color-scheme:dark){:root{--paper:#121A22;--sheet:#18222C;--ink:#E3EAF0;--soft:#9BABBA;
--rule:#2C3A47;--band:#22303D;--ok:#3FB8A5;--bad:#F06A74;--warn:#E0A54A;--muted:#8A99A8;--line:#E3EAF0}}
*{box-sizing:border-box}html{background:var(--paper)}
body{margin:0;color:var(--ink);font:16px/1.55 "IBM Plex Sans",system-ui,sans-serif;background:var(--paper)}
main{max-width:1040px;margin:0 auto;padding:40px 24px 64px}
.top{padding-bottom:28px;border-bottom:2px solid var(--ink);margin-bottom:8px}
.brand{margin:0 0 6px;color:var(--soft);font-weight:500}
h1{margin:0;font-size:clamp(28px,4.2vw,44px);line-height:1.1;font-weight:600;letter-spacing:-.01em}
.lede{margin:10px 0 22px;font-size:20px;max-width:46ch}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px 24px;margin:0}
.meta dt{color:var(--soft);font-size:13px}.meta dd{margin:2px 0 0;font-weight:500}
.family{padding:28px 0 8px;border-bottom:1px solid var(--rule)}
.family>header{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 18px;margin-bottom:14px}
h3{margin:0;font-size:24px;font-weight:600}
.final{margin:0;font-weight:600}.final span{font-weight:400;color:var(--soft)}
.final.ok{color:var(--ok)}.final.bad{color:var(--bad)}.final.warn{color:var(--warn)}.final.muted{color:var(--muted)}
.attempt{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:24px;background:var(--sheet);
border:1px solid var(--rule);border-radius:3px;padding:20px;margin:0 0 12px}
.n{margin:0;color:var(--soft);font-size:14px}
h4{margin:2px 0 4px;font-size:19px;font-weight:600;word-break:break-word}
.oid{margin:0 0 10px;font:14px/1.4 "IBM Plex Mono",ui-monospace,monospace;color:var(--soft);word-break:break-all}
.pred{margin:0 0 10px}
blockquote{margin:0;padding:2px 0 2px 12px;border-left:3px solid var(--rule);color:var(--soft);font-size:15px}
.chart{margin:0}.chart svg{width:100%;height:auto;display:block}
.chart text{font:11px "IBM Plex Sans",sans-serif;fill:var(--soft)}
.chart .grid{stroke:var(--rule);stroke-width:1}.chart .band{fill:var(--band)}
.chart .bandlabel{fill:var(--soft);font-weight:500}
.chart .line{fill:none;stroke:var(--line);stroke-width:2}
.chart .dots circle{fill:var(--line)}
.chart.bad .line{stroke:var(--bad)}.chart.bad .dots circle{fill:var(--bad)}
.chart.ok .line{stroke:var(--ok)}.chart.ok .dots circle{fill:var(--ok)}
figcaption{font-size:13px;color:var(--soft);margin-top:2px}
.stamp{margin:10px 0 6px;padding:6px 10px;border:2px solid currentColor;border-radius:3px;display:inline-block}
.stamp.ok{color:var(--ok)}.stamp.bad{color:var(--bad)}.stamp.warn{color:var(--warn)}.stamp.muted{color:var(--muted)}
.stamp strong{margin-right:6px}
.checks{list-style:none;margin:6px 0 0;padding:0;font-size:14px}
.checks li{padding:3px 0;display:grid;grid-template-columns:18px auto 1fr;gap:6px}
.checks .what{font-weight:500}.checks .mark{font-weight:600}
.checks .pass .mark{color:var(--ok)}.checks .fail{color:var(--bad)}.checks .info .mark{color:var(--muted)}
.reread{margin:-4px 0 14px 24px;padding:10px 14px;border-left:3px solid var(--ink);max-width:76ch}
.reread p{margin:0}.reread details{margin-top:6px;color:var(--soft);font-size:15px}
.reread summary{cursor:pointer;color:var(--ink);font-weight:500}
.reread summary:focus-visible,a:focus-visible{outline:2px solid var(--ok);outline-offset:2px}
.none{color:var(--soft)}
footer{margin-top:28px;color:var(--soft);font-size:14px;max-width:72ch}
@media (max-width:760px){main{padding:24px 16px 48px}.attempt{grid-template-columns:1fr}.reread{margin-left:8px}}
"""
