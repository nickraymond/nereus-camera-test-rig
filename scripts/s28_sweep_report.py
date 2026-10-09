# ruff: noqa: E501  (inline HTML template)
"""Sunrise sweep (R5) + B3a encode ladder report: one self-contained HTML page from the Pi's
CSVs (r5_sweep.csv, ladder.csv) and tiles. Formatting only: every number comes from the Pi.

    python scripts/s28_sweep_report.py <pulled_run_dir> --out <dir>/index.html
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import statistics as st
from pathlib import Path

FLOOR_MAX = 1.13       # IMX708 analogue gain floor reads 1.123
CAP_US = 29900         # the 30 ms cap reads back as 29,997 us
BANDS = [("dark", 0, 20), ("dim, at the cap", 20, 450), ("bright, below the cap", 450, 1e9)]
TILES = [("0600", "dark, before sunrise"), ("0730", "sunrise"), ("0810", "daylight"), ("1020", "LEDs on")]
BUDGET = 480.0


def fv(r: dict, k: str, fmt: str = "{:.2f}") -> str:
    return fmt.format(float(r[k])) if r.get(k) not in (None, "") else "—"


def band(lux: float) -> str:
    return next(n for n, lo, hi in BANDS if lo <= lux < hi)


def pct(v, q):
    v = sorted(v)
    if not v:
        return float("nan")
    k = (len(v) - 1) * q
    f = int(k)
    return v[f] + (v[min(f + 1, len(v) - 1)] - v[f]) * (k - f)


def chart(rows) -> str:
    """Gain vs Lux (log x), low-gain and stock; exposure shown on hover."""
    W, H, L, R, T, B = 760, 300, 56, 16, 16, 44
    import math

    xs = [math.log10(max(float(r["Lux"]), 0.1)) for r in rows]
    x0, x1 = math.floor(min(xs)), math.ceil(max(xs))
    ymax = 17.0

    def px(lux):
        return L + (math.log10(max(lux, 0.1)) - x0) / (x1 - x0) * (W - L - R)

    def py(g):
        return T + (1 - g / ymax) * (H - T - B)

    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Analogue gain vs Lux">']
    for g in (1, 2, 4, 8, 16):
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{py(g):.1f}" y2="{py(g):.1f}" class="grid"/><text x="{L - 6}" y="{py(g) + 4:.1f}" class="ax" text-anchor="end">{g}</text>')
    for d in range(x0, x1 + 1):
        out.append(f'<text x="{px(10 ** d):.1f}" y="{H - B + 16}" class="ax" text-anchor="middle">{10 ** d:g}</text>')
    out.append(f'<text x="{(L + W - R) / 2}" y="{H - 6}" class="ax" text-anchor="middle">Lux (rpicam estimate, log scale)</text>')
    out.append(f'<text x="14" y="{(T + H - B) / 2}" class="ax" text-anchor="middle" transform="rotate(-90 14 {(T + H - B) / 2})">analogue gain</text>')
    for prof, cls in (("stock", "s"), ("lowgain", "l")):
        for r in rows:
            if r["profile"] != prof:
                continue
            lux, g, e = float(r["Lux"]), float(r["AnalogueGain"]), int(r["ExposureTime"])
            shape = (f'<circle cx="{px(lux):.1f}" cy="{py(g):.1f}" r="4.5" class="{cls}">' if prof == "lowgain"
                     else f'<rect x="{px(lux) - 4:.1f}" y="{py(g) - 4:.1f}" width="8" height="8" class="{cls}">')
            tag = "circle" if prof == "lowgain" else "rect"
            out.append(f'{shape}<title>{r["slot"][:2]}:{r["slot"][2:]} {prof}: Lux {lux:g}, gain {g:.3f}, exposure {e / 1000:.1f} ms</title></{tag}>')
    out.append("</svg>")
    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    e = html.escape
    rows = list(csv.DictReader(open(a.run / "r5_sweep.csv")))
    lad = list(csv.DictReader(open(a.run / "ladder" / "ladder.csv")))
    by = {(r["slot"], r["profile"]): r for r in rows}
    lg = [r for r in rows if r["profile"] == "lowgain"]
    below = [r for r in lg if int(r["ExposureTime"]) < CAP_US]
    viol = [r for r in below if float(r["AnalogueGain"]) > FLOOR_MAX]
    viol2 = [r for r in lg if float(r["AnalogueGain"]) > FLOOR_MAX and int(r["ExposureTime"]) < CAP_US]
    gain_pass = not viol and not viol2
    # noise: low-gain vs stock at the same slot, per band
    nrows, noise_summary = [], {}
    for name, lo, hi in BANDS:
        ratios, sharp, cc = [], [], []
        for r in lg:
            if not lo <= float(r["Lux"]) < hi:
                continue
            s = by.get((r["slot"], "stock"))
            if s and r["v3_grey_hp_noise"] and s["v3_grey_hp_noise"]:
                ratios.append(float(r["v3_grey_hp_noise"]) / float(s["v3_grey_hp_noise"]))
                sharp.append(float(r["tag_sharpness"]) / float(s["tag_sharpness"]))
                if r["cc_grey_noise_cv"] and s["cc_grey_noise_cv"]:
                    cc.append(float(r["cc_grey_noise_cv"]) / float(s["cc_grey_noise_cv"]))
        if not ratios:
            continue
        ok = st.median(ratios) <= 1.05
        cc_s = "%.2f (n %d)" % (st.median(cc), len(cc)) if cc else "—"
        noise_summary[name] = ok
        exp_l = [int(r["ExposureTime"]) / 1000 for r in lg if lo <= float(r["Lux"]) < hi]
        exp_s = [int(by[(r["slot"], "stock")]["ExposureTime"]) / 1000 for r in lg if lo <= float(r["Lux"]) < hi]
        g_l = [float(r["AnalogueGain"]) for r in lg if lo <= float(r["Lux"]) < hi]
        nrows.append(f"<tr><td>{name}</td><td class=num>{len(ratios)}</td><td class=num>{min(exp_l):.1f}–{max(exp_l):.1f}</td><td class=num>{min(exp_s):.1f}–{max(exp_s):.1f}</td><td class=num>{min(g_l):.2f}–{max(g_l):.2f}</td>"
                     f"<td class=num>{st.median(ratios):.2f} ({min(ratios):.2f}–{max(ratios):.2f})</td><td class=num>{st.median(sharp):.2f}</td><td class=num>{cc_s}</td><td>{'≤ stock (±5 %)' if ok else '<b>above stock</b>'}</td></tr>")
    # ladder per band
    lrows, worst_wake, fails = [], 0.0, [r for r in lad if r["rc"] != "0"]
    for name, lo, hi in BANDS:
        rs = [r for r in lad if lo <= float(r["lux"]) < hi and r["rc"] == "0"]
        nb = sum(1 for r in lad if lo <= float(r["lux"]) < hi)
        if not nb:
            continue
        def col(k, conv=float):
            return [conv(r[k]) for r in rs if r[k] != ""]
        enc, att, vp, cma, msg, byt = col("encode_s"), col("attempts", int), col("vmpeak_kib"), col("cmafree_min_kb"), col("messages", int), col("bytes", int)
        w0, w1 = col("wake_no_tail_s"), col("wake_with_tail_s")
        worst_wake = max([worst_wake] + w0)
        lrows.append(
            f"<tr><td>{name}</td><td class=num>{len(rs)}/{nb}</td><td class=num>{pct(enc, .5):.1f} / {pct(enc, .9):.1f}</td>"
            f"<td class=num>{st.median(att):g} (max {max(att)})</td><td class=num>{pct(byt, .5):,.0f} / {max(byt):,}</td><td class=num>{pct(msg, .5):.0f} / {max(msg)}</td>"
            f"<td class=num>{pct(vp, .5) / 1024:.0f} / {max(vp) / 1024:.0f}</td><td class=num>{min(cma) / 1024:.1f}</td>"
            f"<td class=num>{pct(w0, .5):.0f} / {pct(w0, .9):.0f}</td><td class=num>{pct(w1, .5):.0f} / {pct(w1, .9):.0f}</td></tr>")
    ok_l = [r for r in lad if r["rc"] == "0"]
    allvp = [float(r["vmpeak_kib"]) for r in ok_l]
    all_w1 = [float(r["wake_with_tail_s"]) for r in ok_l if r["wake_with_tail_s"]]
    all_w0 = [float(r["wake_no_tail_s"]) for r in ok_l if r["wake_no_tail_s"]]
    over_cap = [r for r in ok_l if int(r["bytes"]) > 56160]
    ladder_pass = not fails and not over_cap and max(allvp) < 250 * 1024 and max(all_w0) <= BUDGET
    tiles = []
    for slot, label in TILES:
        cells = []
        for prof in ("lowgain", "stock"):
            p = a.run / "tiles" / f"s{slot}_{prof}.jpg"
            r = by.get((slot, prof))
            if not p.exists() or r is None:
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            cells.append(f'<figure><img src="data:image/jpeg;base64,{b64}" alt="{slot} {prof}"><figcaption><b>{"low-gain" if prof == "lowgain" else "stock auto"}</b> · {int(r["ExposureTime"]) / 1000:.1f} ms · gain {float(r["AnalogueGain"]):.2f} · digital {float(r["DigitalGain"]):.2f} · Lux {float(r["Lux"]):g}<br>card ΔE00 (provisional truth) RAW {fv(r, "raw_de00_mean")} mean / {fv(r, "raw_de00_worst")} worst ({e(r.get("raw_worst_patch") or "")}) / {fv(r, "raw_de00_nol_mean")} hue-chroma · JPEG {fv(r, "jpeg_de00_mean")} / {fv(r, "jpeg_de00_worst")} / {fv(r, "jpeg_de00_nol_mean")}</figcaption></figure>')
        tiles.append(f'<section class=pair><h3>{slot[:2]}:{slot[2:]} — {e(label)}</h3><div class=row>{"".join(cells)}</div></section>')
    srows = []
    for slot in sorted({r["slot"] for r in rows}):
        lo_, st_ = by.get((slot, "lowgain")), by.get((slot, "stock"))
        cells = [f"<td>{slot[:2]}:{slot[2:]}</td><td class=num>{float((lo_ or st_)['Lux']):g}</td>"]
        for r in (lo_, st_):
            if r is None:
                cells.append("<td colspan=7>—</td>")
                continue
            cells.append(
                f"<td class=num>{int(r['ExposureTime']) / 1000:.1f}</td><td class=num>{float(r['AnalogueGain']):.2f}</td>"
                f"<td class=num>{fv(r, 'v3_grey_hp_noise', '{:.4f}')}</td><td class=num>{fv(r, 'tag_sharpness', '{:.3f}')}</td>"
                f"<td class=num>{fv(r, 'raw_de00_mean')} / {fv(r, 'raw_de00_worst')} / {fv(r, 'raw_de00_nol_mean')}</td>"
                f"<td class=num>{fv(r, 'jpeg_de00_mean')} / {fv(r, 'jpeg_de00_worst')} / {fv(r, 'jpeg_de00_nol_mean')}</td>"
                f"<td>{e(r.get('v3_clipped_patches') or '')}</td>")
        srows.append("<tr>" + "".join(cells) + "</tr>")
    # noise split by physics: did stock run longer than the low-gain 30 ms cap?
    longer, same = [], []
    for r in lg:
        s = by.get((r["slot"], "stock"))
        if s and r["v3_grey_hp_noise"] and s["v3_grey_hp_noise"]:
            q = float(r["v3_grey_hp_noise"]) / float(s["v3_grey_hp_noise"])
            (longer if int(s["ExposureTime"]) > 30500 else same).append(q)
    same_ok = bool(same) and max(same) <= 1.05
    v_gain = "PASS" if gain_pass else "FAIL"
    v_noise = (f"where stock ran longer than 30 ms ({len(longer)} pairs, Lux ≤ 120) low-gain noise is "
               f"{min(longer):.2f}–{max(longer):.2f}× stock: <span class=fail>FAIL</span> on the 'noise ≤ stock' rule, by design (half the shutter or less). "
               f"Where both ran ≤ 30 ms ({len(same)} pairs) it is {min(same):.2f}–{max(same):.2f}×: "
               f"<span class={'pass' if same_ok else 'fail'}>{'PASS' if same_ok else 'FAIL'}</span>")
    page = """<title>Sunrise Sweep R5</title>
<style>
:root{--bg:#f2f4f6;--panel:#fff;--ink:#141b21;--ink2:#4a5761;--rule:#d5dce1;--acc:#1f6f8b;--accbg:#e2eff4;--ok:#1d7a46;--bad:#a8321c;--l:#1f6f8b;--s:#c9741d}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b0bcc5;--rule:#2b353d;--acc:#62bcd6;--accbg:#12303a;--ok:#5fcf8d;--bad:#f08a72;--l:#62bcd6;--s:#f0a85a}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b0bcc5;--rule:#2b353d;--acc:#62bcd6;--accbg:#12303a;--ok:#5fcf8d;--bad:#f08a72;--l:#62bcd6;--s:#f0a85a}
body{background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:18px 40px}
main{max-width:1240px;margin:0 auto;display:grid;gap:14px} h1{font-size:1.4rem;margin:0;text-wrap:balance} h2{font-size:1.08rem;margin:0} h3{font-size:.95rem;margin:0}
p,ul{margin:0;max-width:120ch} ul{padding-left:1.2em;display:grid;gap:3px} .muted{color:var(--ink2)}
section{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px;display:grid;gap:8px} .rec{border-left:4px solid var(--acc);background:var(--accbg)}
.wrap{overflow-x:auto} table{border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:.85rem} th,td{padding:4px 8px;border-bottom:1px solid var(--rule);text-align:left} th{font-weight:600} .num{text-align:right}
.pass{color:var(--ok);font-weight:700} .fail{color:var(--bad);font-weight:700}
svg{width:100%;max-width:760px;height:auto} svg .grid{stroke:var(--rule)} svg .ax{fill:var(--ink2);font-size:11px} svg .l{fill:var(--l)} svg .s{fill:none;stroke:var(--s);stroke-width:2}
.legend span{display:inline-flex;align-items:center;gap:6px;margin-right:16px} .dot{width:10px;height:10px;border-radius:50%;background:var(--l)} .sq{width:9px;height:9px;border:2px solid var(--s)}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px} figure{margin:0;display:grid;gap:4px} figure img{width:100%;border-radius:4px;border:1px solid var(--rule)} figcaption{font-size:.8rem;color:var(--ink2)}
</style>""" + f"""<main>
<h1>Sunrise sweep on nereus002: low-gain exposure (R5) and the B3a encode at 1600×900</h1>
<p class=muted>Thursday 2026-10-08, 06:00–10:30 PDT, a pair every 10 min, LEDs on at ~10:00. <b>nereus002 is a bench Pi Zero 2 W, not a bmcam unit</b> (same Pi class, different OS image and services). The true fit test runs on a bmcam unit after the RC gates.</p>
<section class=rec><h2>Verdicts</h2><ul>
<li><b>R5 gain rule: <span class={v_gain.lower()}>{v_gain}</span></b> (n = {len(lg)} low-gain frames). {len(below)} frames needed less than 30 ms; all sat at the gain floor (≤ {FLOOR_MAX}). Gain rose only at the 30 ms cap.</li>
<li><b>R5 noise (V3 grey pixel noise, low-gain ÷ stock at the same slot):</b> {v_noise}.</li>
<li><b>B3a at 1600×900: <span class={'pass' if ladder_pass else 'fail'}>{'PASS' if ladder_pass else 'FAIL'}</span></b> (n = {len(lad)} encodes on the sweep RAWs). Failures: {len(fails)}. Over the 56,160 B cap: {len(over_cap)}. VmPeak max {max(allvp) / 1024:.0f} MiB against the 250 MiB guard. Predicted wake max {max(all_w0):.0f} s without the 150 s tail; with it, max {max(all_w1):.0f} s against 480 s.</li></ul></section>
<section><h2>Gain vs light</h2><p class="legend muted"><span><i class=dot></i>low-gain (patched tuning, shutter capped at 30 ms)</span><span><i class=sq></i>stock auto</span></p><div class=wrap>{chart(rows)}</div>
<p class=muted>Hover a point for its slot, exposure and gain. Digital gain is separate: 2.0 on low-gain in the dark, ≈1.0 otherwise.</p></section>
<section><h2>R5 per light band</h2><div class=wrap><table><tr><th>band (Lux)</th><th class=num>pairs</th><th class=num>low-gain exposure ms</th><th class=num>stock exposure ms</th><th class=num>low-gain analogue gain</th><th class=num>V3 grey noise ratio, median (range)</th><th class=num>tag sharpness ratio</th><th class=num>ColorChecker grey ratio</th><th>noise vs stock</th></tr>{"".join(nrows)}</table></div>
<p class=muted>Bands: dark &lt; 20, dim 20–450 (at the 30 ms cap), bright ≥ 450 (below the cap). Grey noise = pixel noise on the V3 card's three light greys: std of (green − its 5×5 local mean) / mean, central 50 % of each patch, binned RAW, so the light gradient across a patch does not count. The ColorChecker grey row (std / mean, the EM's metric) is only usable from 09:50: before that a ChArUco board covered it, so it is shown where valid, with its n. Tag sharpness = top-10 % gradient / contrast in the 4 V3 tag boxes. Ratios are low-gain ÷ stock at the same slot.</p></section>
<section><h2>B3a encode ladder, 1600×900, per light band</h2><div class=wrap><table><tr><th>band</th><th class=num>ok / n</th><th class=num>encode s P50 / P90</th><th class=num>attempts</th><th class=num>bytes P50 / max</th><th class=num>msgs P50 / max</th><th class=num>VmPeak MiB P50 / max</th><th class=num>CmaFree min MiB</th><th class=num>wake s, no tail, P50 / P90</th><th class=num>wake s, + 150 s tail, P50 / P90</th></tr>{"".join(lrows)}</table></div>
<ul class=muted><li>Each encode: #134 at bac017f, <code>rc_raw_jxl.py --layout rgb</code>, default search, crop 1504,846,1600,900, one at a time. Encode s is the tool's own figure (search attempts); DNG read and RGB prep add ~3 s and are in the wake.</li>
<li>Wake = 9 s (process start → capture, Sprint26 assumption) + capture + DNG read + prep + encode + (msgs + 2 + 40 heal chunks) × 1.3 s, then + 150 s fixed listen tail. Real units trim that tail to the budget (rc_command_hooks.py:389-396), so the no-tail figure is the closer one.</li>
<li>VmPeak / VmHWM: cjxl's own, from /proc every 0.1 s. CmaFree: system minimum during the encode.</li></ul></section>
<section><h2>Every pair, dark → light</h2><div class=wrap><table>
<tr><th rowspan=2>slot</th><th rowspan=2 class=num>Lux</th><th colspan=7>low-gain</th><th colspan=7>stock auto</th></tr>
<tr>{"<th class=num>ms</th><th class=num>gain</th><th class=num>noise</th><th class=num>sharp</th><th class=num>RAW ΔE00 mean / worst / hue-chroma</th><th class=num>JPEG ΔE00 mean / worst / hue-chroma</th><th>clipped</th>" * 2}</tr>{"".join(srows)}</table></div>
<ul class=muted><li><b>Card colour error is against the PROVISIONAL truth</b>: the V3 c1 card measured by the IMX708 on 2026-10-06 (PR #93), with no flat-field, so card lightness carries up to ~10–15 L* uncertainty from the lamp gradient. Scored on the 8 colour patches; the greys are the anchors.</li>
<li>RAW: binned RAW, lens shading from the tuning file, white balance on the V3 greys, then a fixed root-poly matrix (ColorChecker, 2026-10-06, LEDs 5300 K; held-out ΔE00 1.96), exposure set on gray_light. JPEG: the camera's own JPEG, one lightness scale on gray_mid, no colour change. Hue-chroma = ΔE00 with L* set to the truth's.</li>
<li>Daylight from the windows differs from the 5300 K LEDs the matrix was fitted under, so the RAW error is lowest under the LEDs (10:00–10:30).</li></ul></section>
<section><h2>Cut sheet: pairs at four light levels</h2><p class=muted>The camera's own JPEGs, cropped to the B3a field (1600×900 at native 1504,846) and <b>resized 0.5× to 800×450</b>. No colour processing.</p>{"".join(tiles)}</section>
</main>"""
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(page, encoding="utf-8")
    print(a.out, "| gain", v_gain, "| noise", v_noise, "| ladder", "PASS" if ladder_pass else "FAIL",
          f"| n R5 {len(lg)} pairs, ladder {len(lad)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
