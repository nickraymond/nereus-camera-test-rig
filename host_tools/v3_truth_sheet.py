# ruff: noqa: E501  (inline HTML template)
"""Sheet for the V3 c1 truth shoot (v3_truth_shoot.py output): design vs measured swatches (A),
what the missing flat-field costs, and how each camera handles colour (B).

    python -m host_tools.v3_truth_sheet <alsc.json> --noshade <none.json> --plane <plane.json> \
        --out <dir>/index.html

<alsc.json> is the primary result (IMX708 lens shading from its tuning file); --noshade is the
same run with no shading correction, --plane adds the chart-fitted light gradient.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import numpy as np

from host_tools.v3_truth_shoot import CARD, srgb8_to_lab50
from nereus_camera_test_rig.color import card_truth as T
from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.metrics import delta_e2000

NAMES = {"imx708": "IMX708 (Pi camera, wide)", "n6": "OpenMV N6", "ae3": "OpenMV AE3"}
COVER = {
    "imx708": "all 24",
    "n6": "top 2 rows only (no greys, no red / green / blue)",
    "ae3": "23 of 24 (black cut)",
}


def hexc(rgb) -> str:
    return "#" + "".join(f"{int(round(min(max(v, 0), 255))):02x}" for v in rgb)


def fit_s(s: dict) -> str:
    f = s["fit"]
    return f"{f['linear3x3']['median']} / {f['rootpoly2']['median']}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path)
    ap.add_argument("--noshade", type=Path, required=True)
    ap.add_argument("--plane", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    R, N, P = (json.loads(p.read_text()) for p in (a.results, a.noshade, a.plane))
    C = R["cams"]
    card = load_card(CARD)
    e = html.escape
    im = C["imx708"]["stops"]["stop_+0"]
    imp = P["cams"]["imx708"]["stops"]["stop_+0"]
    rows, de_design = [], []
    for p in card.patches:
        design = p.design or p.truth
        d_lab = srgb8_to_lab50(np.array(design, float))
        m_lab = np.array(im["v3_lab"][p.id])
        m_rgb, oog = T.xyz50_to_srgb8(T.lab_to_xyz(m_lab))
        p_lab = np.array(imp["v3_lab"][p.id])
        p_rgb, _ = T.xyz50_to_srgb8(T.lab_to_xyz(p_lab))
        de = float(delta_e2000(m_lab, d_lab))
        de_design.append(de)
        xc = {cam: C[cam]["v3_de_vs_imx708"]["per_patch"][p.id] for cam in ("n6", "ae3")}
        rows.append(
            f'<tr><td>{e(p.id)}</td><td><span class=sw style="background:{hexc(design)}"></span></td>'
            f'<td><span class=sw style="background:{hexc(m_rgb)}"></span></td>'
            f"<td class=num>{design[0]:.0f}, {design[1]:.0f}, {design[2]:.0f}</td>"
            f"<td class=num>{m_rgb[0]:.0f}, {m_rgb[1]:.0f}, {m_rgb[2]:.0f}{' *' if oog else ''}</td>"
            f"<td class=num>{m_lab[0]:.1f} / {m_lab[1]:.1f} / {m_lab[2]:.1f}</td>"
            f"<td class=num><b>{de:.1f}</b></td>"
            f"<td class=num>{xc['n6']:.1f}</td><td class=num>{xc['ae3']:.1f}</td>"
            f'<td><span class="sw sm" style="background:{hexc(p_rgb)}"></span> L* {p_lab[0]:.1f}, ΔE00 {float(delta_e2000(p_lab, m_lab)):.1f}</td></tr>'
        )
    lrows = []
    for cam in ("imx708", "n6", "ae3"):
        n0, r0, p0 = (X["cams"][cam]["stops"]["stop_+0"] for X in (N, R, P))
        lp = p0["light_plane"]
        shade = "tuning file (rpi.alsc)" if cam == "imx708" else "on-chip, kept on (OQ-54)"
        lrows.append(
            f"<tr><td>{NAMES[cam]}</td><td>{COVER[cam]}</td><td>{shade}</td>"
            f"<td class=num>{fit_s(n0)}</td><td class=num>{fit_s(r0) if cam == 'imx708' else '—'}</td>"
            f"<td class=num>{fit_s(p0)}</td><td class=num>{lp['pct_across_chart_y']} %</td>"
            f"<td class=num>{'—' if cam == 'imx708' else C[cam]['v3_de_vs_imx708']['median']}</td>"
            f"<td class=num>{'—' if cam == 'imx708' else N['cams'][cam]['v3_de_vs_imx708']['median']}</td></tr>"
        )
    brows = []
    for cam in ("imx708", "n6", "ae3"):
        s0, pr = C[cam]["stops"]["stop_+0"], C[cam]["processed"]
        best = s0["fit"][s0["model"]]
        g_raw = s0["grey_ab_3x3"]
        g_raw_s = (
            "max |a*| %.1f, |b*| %.1f (5 greys, white b* %+.1f)"
            % (
                max(abs(v[0]) for k, v in g_raw.items()),
                max(abs(v[1]) for k, v in g_raw.items() if k != "white"),
                g_raw.get("white", [0, 0])[1],
            )
            if g_raw
            else "no greys visible"
        )
        g_j = (
            "a* %+.1f…%+.1f, b* %+.1f…%+.1f"
            % (
                min(v[0] for v in pr["grey_ab"].values()),
                max(v[0] for v in pr["grey_ab"].values()),
                min(v[1] for v in pr["grey_ab"].values()),
                max(v[1] for v in pr["grey_ab"].values()),
            )
            if pr["grey_ab"]
            else "no greys visible"
        )
        snr = s0["red_snr_db"]
        en = pr["exposure_normal"]
        exp_n = (
            f"{en['ExposureTime'] / 1000:.1f} ms, gain {en['AnalogueGain']:.2f}"
            if "ExposureTime" in en
            else f"{en['exposure_us'] / 1000:.1f} ms, {en['gain_db']:.1f} dB"
        )
        brows.append(
            f"<tr><td>{NAMES[cam]}</td><td class=num>{C[cam]['cc_coverage']}</td>"
            f"<td class=num><b>{best['median']}</b> ({s0['model']}, p90 {best['p90']})</td>"
            f"<td class=num><b>{pr['de2000_median']}</b> ({pr['patches']} patches; colours {pr['de2000_colours']})</td>"
            f"<td>{e(g_raw_s)}</td><td>{e(g_j)}</td>"
            f"<td class=num>{snr.get('v3_red', '—')}</td>"
            f"<td>{len(pr['clipped_raw_normal'])} patches in RAW · JPEG: {e(', '.join(pr['clipped_jpeg']) or 'none')}<br><span class=muted>{e(exp_n)}</span></td></tr>"
        )
    lp_im = imp["light_plane"]
    page = (
        """<title>V3 c1 Truth</title>
<style>
:root{--bg:#f3f5f6;--panel:#fff;--ink:#121a20;--ink2:#46535d;--rule:#d6dde2;--acc:#1f7a8c;--accbg:#e3f1f4;--warn:#9a5b00;--warnbg:#fbf0de}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--acc:#5fbfd1;--accbg:#12303a;--warn:#f0b25a;--warnbg:#33260f}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--acc:#5fbfd1;--accbg:#12303a;--warn:#f0b25a;--warnbg:#33260f}
body{background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:18px 40px}
main{max-width:1300px;margin:0 auto;display:grid;gap:14px} h1{font-size:1.4rem;margin:0;text-wrap:balance} h2{font-size:1.08rem;margin:0}
p,ul{margin:0;max-width:120ch} ul{padding-left:1.2em;display:grid;gap:3px} .muted{color:var(--ink2)} section{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px;display:grid;gap:8px}
.rec{border-left:4px solid var(--acc);background:var(--accbg)} .warn{border-left:4px solid var(--warn);background:var(--warnbg)}
.wrap{overflow-x:auto} table{border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:.85rem} th,td{padding:4px 8px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:middle} th{font-weight:600} .num{text-align:right}
.sw{display:inline-block;width:56px;height:34px;border-radius:4px;border:1px solid var(--rule);vertical-align:middle} .sw.sm{width:22px;height:16px}
</style>"""
        + f"""<main>
<h1>V3 card c1: measured colours, and how each camera handles colour</h1>
<p class=muted>nereus002, 2026-10-06. Two LEDs at 5300 K, full brightness. ColorChecker Classic (post-2014 Lab D50 values) in the card's plane, below it. No physical flat-field (no board large enough).</p>
<section class=rec><h2>In short</h2><ul>
<li><b>A, card colours:</b> measured through the IMX708 (24/24 ColorChecker patches, held-out ΔE00 {im["fit"][im["model"]]["median"]}). The N6 agrees to a median ΔE00 of {C["n6"]["v3_de_vs_imx708"]["median"]} and the AE3 to {C["ae3"]["v3_de_vs_imx708"]["median"]}, both within the 2 ΔE00 target. The printed card is far from its design file: median ΔE00 {np.median(de_design):.1f}. The black prints at L* {im["v3_lab"]["gray_black"][0]:.0f} instead of 0, and the greys are compressed.</li>
<li><b>B, cameras:</b> with RAW + a fitted matrix, all three cameras land at 1.2–2.2 ΔE00 on the ColorChecker. Their own JPEGs land at 7.6–9.5, with a green-blue grey cast (a* −3.6 to −6.2 on the IMX708 and AE3; the N6 sees no greys). At normal settings the N6 and AE3 RAWs clip the light greys, yellow and cyan; the IMX708 RAW clips nothing.</li>
<li><b>Limit:</b> the lamps light the chart unevenly, {lp_im["pct_across_chart_y"]} % brighter at its top than at its bottom, and the same on two cameras. The card sits above the chart, so its absolute lightness is uncertain: up to 15 L* in the worst case (below). Hues are less affected. <b>A few sheets of printer paper as a flat-field would settle it.</b></li></ul></section>
<section><h2>A. Design (print file) vs measured, per V3 patch</h2>
<p>IMX708 RAW at locked exposure (gain 1.12, 40.9 ms, focus 1.094 dpt). Lens shading is removed with the camera's own tuning-file tables. Camera RGB → XYZ D50 by a {im["model"]} fit on the 24 ColorChecker patches; median over 3 repeats. The −½ stop bracket agrees to ΔE00 {R["imx708_stop_consistency"]}.</p>
<div class=wrap><table>
<tr><th>patch</th><th>design</th><th>measured</th><th class=num>design sRGB</th><th class=num>measured sRGB</th><th class=num>measured Lab D50</th><th class=num>ΔE00 design→measured</th><th class=num>N6 vs IMX708</th><th class=num>AE3 vs IMX708</th><th>if the chart's light gradient continues onto the card</th></tr>{"".join(rows)}</table></div>
<p class=muted>Swatches are sRGB on your screen (approximate). * = outside sRGB, clipped for display; the Lab value is exact. Last column: the same measurement with the chart-fitted light gradient carried up onto the card. It is an upper bound on the error the missing flat-field could cause, not a better estimate (see below).</p></section>
<section class=warn><h2>What the missing flat-field costs</h2>
<div class=wrap><table>
<tr><th>camera</th><th>ColorChecker patches seen</th><th>lens shading</th><th class=num>held-out ΔE00, no correction (3×3 / root-poly)</th><th class=num>+ tuning-file shading</th><th class=num>+ chart light gradient</th><th class=num>gradient top→bottom of chart</th><th class=num>card vs IMX708, with shading</th><th class=num>card vs IMX708, no shading</th></tr>{"".join(lrows)}</table></div>
<ul>
<li><b>IMX708:</b> the tuning-file shading tables (luminance at full strength, colour tables at 5300 K) lower held-out ΔE00 from {fit_s(N["cams"]["imx708"]["stops"]["stop_+0"])} to {fit_s(im)}. The AE3 cross-check improves from {N["cams"]["ae3"]["v3_de_vs_imx708"]["median"]} to {C["ae3"]["v3_de_vs_imx708"]["median"]}. These tables are used for the values above.</li>
<li><b>Remaining error is the lamps, not the lens.</b> After shading, the ColorChecker's top row reads 1–3 L* bright and its grey row 2–5 L* dark, on the IMX708 and the AE3 alike. A plane fitted on the chart takes the held-out error to {fit_s(imp)}, so a real flat-field would clearly help the camera fit.</li>
<li><b>Why the gradient is not applied to the card:</b> the chart is below the card, so the plane has to be extended upward past where it was measured. That moves the card by 4–11 ΔE00, mostly in lightness, and makes the cross-camera checks worse (N6 {P["cams"]["n6"]["v3_de_vs_imx708"]["median"]}, AE3 {P["cams"]["ae3"]["v3_de_vs_imx708"]["median"]}). The light field across the card is unknown without a flat. The card's own grey surround cannot stand in: the gaps between patches are 2–10 px wide in the image and neighbouring colours bleed in.</li>
<li><b>N6 and AE3:</b> their RAWs carry on-chip lens shading correction (kept on, OQ-54), so no tuning file applies. The N6 sees only the top two ColorChecker rows: no greys and no saturated primaries. Its 1.18 held-out error is on easy patches, and its grey neutrality cannot be measured.</li>
<li><b>Ask:</b> 4–6 sheets of plain printer paper taped flat over the card and the chart, same place, lights untouched. About 3 minutes of capture. It fixes the IMX708 and AE3 fits and the card's absolute lightness.</li></ul></section>
<section><h2>B. How each camera handles colour</h2><div class=wrap><table>
<tr><th>camera</th><th class=num>ColorChecker coverage</th><th class=num>RAW + fitted matrix, held-out ΔE00</th><th class=num>own processed JPEG ΔE00 (normal settings)</th><th>grey neutrality, RAW + 3×3</th><th>grey neutrality, own JPEG</th><th class=num>red SNR on the V3 red patch (dB)</th><th>clipped at normal exposure</th></tr>{"".join(brows)}</table></div>
<ul class=muted>
<li>RAW + fit: locked exposure with the card's light grey at ~0.6 of full scale, median of 3 repeats, the better of 3×3 and root-poly by leave-one-patch-out. The IMX708 has tuning-file shading; the OpenMV boards have on-chip shading. The lamp gradient is not removed (see above).</li>
<li>Own JPEG: the camera's normal-settings still (auto exposure and AWB), vs the ColorChecker values, after one lightness offset on the mid greys and no colour change. A negative a* is a green cast; a negative b* is blue.</li>
<li>Red SNR: mean / RMS temporal noise over 3 repeats on red Bayer sites, at each camera's locked exposure (IMX708 40.9 ms, gain 1.12; N6 9.4 ms, AE3 7.6 ms, 3.15 dB). The OpenMV RAW has on-chip denoise on, which flatters its SNR.</li>
<li>Clipped: V3 and ColorChecker patches with clipped RAW pixels in the normal-settings capture. The OpenMV auto exposure lets the light patches clip in RAW (8-bit). The N6 still ran at 8.6 dB gain.</li></ul></section>
</main>"""
    )
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(page, encoding="utf-8")
    print(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
