"""One sheet for the 2x-LED exposure-sweep dry run: frames + scores, the pick, the per-patch ΔE
table and a ΔE-per-patch bar chart for the pick vs today's production JPEG (Nick, 2026-10-05).

    python -m compression_study.presets.sweep_colour_sheet <experiment> <colour.json> <out.html>

Self-contained HTML (thumbnails embedded as JPEG, chart as inline SVG). Thumbnails are
DOWNSCALED renders of each RAW with ONE display scale (the 1/15 s frame's 99th percentile =
white), so shorter shutters look darker, as captured.
"""

from __future__ import annotations

import base64
import html
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

from nereus_camera_test_rig.color.raw_io import bin2x2, normalize, read_dng  # noqa: E402

ORDER = ["gray_white", "gray_light", "gray_mid", "gray_dark", "cream", "tan", "ochre", "orange",
         "brown", "dark_brown", "coral_pink", "red_orange", "yellow", "green", "cyan", "blue",
         "magenta"]


def frac(us):
    return f"1/{round(1e6 / us)} s"


def thumbs(exp: Path, colour: dict) -> list[str]:
    dngs = sorted((exp / "captures" / "imx708").glob("imx708_raw_sweep*.dng"))
    rgb = []
    for d in dngs:
        fr = read_dng(d)
        lin, sat, cfa = normalize(fr)
        b, _ = bin2x2(lin, cfa, sat)
        rgb.append(b[::3, ::3].copy())
        del lin, sat, b
    gains = colour["frames"][-1]["awb_gains"]
    g = np.array([gains[0], 1.0, gains[1]])
    scale = 1 / max(float(np.percentile(rgb[-1][..., 1], 99)), 1e-4)
    out = []
    for b in rgb:
        im = Image.fromarray((np.clip(b * g * scale, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8))
        im = im.resize((420, round(420 * im.height / im.width)), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        out.append("data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode())
    return out


def bar_chart(pick_de: dict, jpg_de: dict) -> str:
    """Grouped bars per patch: pick (uncorrected) vs today's JPEG (uncorrected); clipped JPEG
    patches drawn as a hatched stub labelled 'clipped'."""
    W, H, left, top, bottom = 900, 300, 44, 16, 86
    n = len(ORDER)
    cw = (W - left - 10) / n
    vmax = max(max(pick_de.values()), max(jpg_de.values() or [0]), 1) * 1.1
    vmax = float(np.ceil(vmax / 5) * 5)
    y = lambda v: top + (H - top - bottom) * (1 - v / vmax)  # noqa: E731
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="ΔE2000 per patch, pick vs '
             f"today's JPEG\" style=\"width:100%;height:auto\">",
             '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" '
             'patternTransform="rotate(45)"><rect width="6" height="6" fill="var(--panel)"/>'
             '<line x1="0" y1="0" x2="0" y2="6" stroke="var(--jpg)" stroke-width="2"/></pattern>'
             '</defs>']
    for t in range(0, int(vmax) + 1, 5):
        parts.append(f'<line x1="{left}" x2="{W - 10}" y1="{y(t):.1f}" y2="{y(t):.1f}" '
                     f'stroke="var(--rule)" stroke-width="1"/><text x="{left - 6}" y="{y(t) + 4:.1f}" '
                     f'text-anchor="end" font-size="11" fill="var(--ink2)">{t}</text>')
    for i, pid in enumerate(ORDER):
        x = left + i * cw
        bw = cw * 0.36
        v = pick_de.get(pid)
        if v is not None:
            parts.append(f'<rect x="{x + cw * 0.12:.1f}" y="{y(v):.1f}" width="{bw:.1f}" '
                         f'height="{y(0) - y(v):.1f}" rx="2" fill="var(--pick)"><title>{pid}: pick '
                         f'ΔE {v:.2f}</title></rect>')
        j = jpg_de.get(pid)
        if j is not None:
            parts.append(f'<rect x="{x + cw * 0.12 + bw + 2:.1f}" y="{y(j):.1f}" width="{bw:.1f}" '
                         f'height="{y(0) - y(j):.1f}" rx="2" fill="var(--jpg)"><title>{pid}: JPEG '
                         f'ΔE {j:.2f}</title></rect>')
        else:
            parts.append(f'<rect x="{x + cw * 0.12 + bw + 2:.1f}" y="{y(0) - 14:.1f}" width="{bw:.1f}" '
                         f'height="14" fill="url(#hatch)" stroke="var(--jpg)"><title>{pid}: clipped in '
                         'the JPEG</title></rect>')
        parts.append(f'<text transform="translate({x + cw / 2:.1f},{H - bottom + 10}) rotate(55)" '
                     f'font-size="11" fill="var(--ink2)">{pid.replace("gray_", "grey ")}</text>')
    parts.append(f'<text x="{left}" y="{top - 4}" font-size="11" fill="var(--ink2)">ΔE2000 vs card '
                 'truth</text></svg>')
    return "".join(parts)


def main(exp: str, colour_path: str, out: str) -> int:
    exp_dir = Path(exp)
    c = json.loads(Path(colour_path).read_text())
    rec = json.loads((exp_dir / "experiment.json").read_text())
    pick_us = c["mac_pick"]["shutter_us"]
    pf = next(f for f in c["frames"] if f["shutter_us"] == pick_us)
    pj = c["production_jpeg"]
    s4 = c.get("s4_single_lamp", {})
    imgs = thumbs(exp_dir, c)
    e = html.escape

    cells = []
    for f, img in zip(c["frames"], imgs):
        is_pick = f["shutter_us"] == pick_us
        clip = f["clipping"]["card_area_pct"]
        cells.append(f'''<div class="cell{' pick' if is_pick else ''}"><span class="tag">{frac(f['shutter_us'])}
{'<b class="pk">PICK</b>' if is_pick else ''}</span><img src="{img}" alt="IMX708 at {frac(f['shutter_us'])}, downscaled">
<dl><dt>read-back</dt><dd>{f['exposure_us']} µs · gain {f['analogue_gain']:.3f} · DG {f['digital_gain']:.3f}</dd>
<dt>white level</dt><dd>{' / '.join(f'{v:.2f}' for v in f['white_level'])}{' CLIPPED' if f['white_clipped'] else ''}</dd>
<dt>card clip % RGB</dt><dd>{' / '.join(f'{v:.2f}' for v in clip)}</dd>
<dt>MEDIUM clip % RGB</dt><dd>{' / '.join(f'{v:.2f}' for v in f['clipping']['MEDIUM_pct'])}</dd>
<dt>SMALL clip % RGB</dt><dd>{' / '.join(f'{v:.2f}' for v in f['clipping']['SMALL_pct'])}</dd>
<dt>black patch (flare)</dt><dd>G {f['black_patch']['level_rgb'][1]:.4f} · black/white {f['black_patch']['black_over_white_g']:.3f}{' (white clipped)' if f['black_patch']['white_clipped'] else ''}</dd>
<dt>sharpness (card)</dt><dd>{f['sharpness_card']:.3f}</dd>
<dt>red SNR grey 128</dt><dd>{f['red_snr_grey128']}</dd>
<dt>ΔE camera colour</dt><dd>med {f['uncorrected']['median']} · max {f['uncorrected']['max']} (n {f['uncorrected']['n']})</dd>
<dt>ΔE card fit</dt><dd>med {f['card_fit']['median']} · max {f['card_fit']['max']} (n {f['card_fit']['n']})</dd>
<dt>CCT (card greys)</dt><dd>{f['cct'].get('cct_k', 'n/a')} K</dd></dl></div>''')

    rows = []
    for pid in ORDER:
        def cell(d, k):
            v = d.get(k, {}).get("de", {}).get(pid)
            return f"{v:.2f}" if v is not None else '<span class="muted">clipped</span>'
        rows.append(f"<tr><td>{pid}</td><td class=num>{cell(pf, 'uncorrected')}</td>"
                    f"<td class=num>{cell(pf, 'card_fit')}</td><td class=num>{cell(pj, 'uncorrected')}</td>"
                    f"<td class=num>{cell(pj, 'card_fit')}</td>"
                    f"<td class=num>{cell(s4, 'uncorrected') if s4 else '—'}</td>"
                    f"<td class=num>{cell(s4, 'card_fit') if s4 else '—'}</td></tr>")

    def srow(label, d):
        return (f"<tr><th>{label}</th>" + "".join(
            f"<td class=num>{d[k]['mean']} / {d[k]['median']} / {d[k]['max']} (n {d[k]['n']})</td>"
            for k in ("uncorrected", "card_fit")) + "</tr>")

    grey_rows = "".join(
        f"<tr><td>{g}</td><td class=num>{pf['uncorrected']['grey_ab'].get(g)}</td>"
        f"<td class=num>{pf['card_fit']['grey_ab'].get(g)}</td>"
        f"<td class=num>{pj['uncorrected']['grey_ab'].get(g, 'clipped')}</td></tr>"
        for g in ("gray_white", "gray_light", "gray_mid", "gray_dark"))
    cct = pf["cct"]
    page = f'''<meta charset="utf-8"><title>Two-LED Sweep Check</title>
<style>
:root{{--bg:#f3f5f6;--panel:#fff;--ink:#121a20;--ink2:#46535d;--rule:#d6dde2;--pick:#1f7a8c;--jpg:#c0632a;--pickbg:#e3f1f4}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--pick:#5fbfd1;--jpg:#e8915a;--pickbg:#12303a}}}}
:root[data-theme="dark"]{{color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--pick:#5fbfd1;--jpg:#e8915a;--pickbg:#12303a}}
body{{background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:18px 40px}}
main{{max-width:1500px;margin:0 auto;display:grid;gap:14px}} h1{{font-size:1.45rem;margin:0}} h2{{font-size:1.05rem;margin:0}}
p,ul{{margin:0;max-width:110ch}} .muted{{color:var(--ink2)}} section{{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px;display:grid;gap:8px}}
.strip{{display:grid;grid-template-columns:repeat(5,minmax(200px,1fr));gap:8px;overflow-x:auto}}
.cell{{border:1px solid var(--rule);border-radius:5px;padding:6px;display:grid;gap:4px;align-content:start}} .cell.pick{{border:2px solid var(--pick);background:var(--pickbg)}}
.cell img{{width:100%;height:auto;border-radius:3px}} .tag{{font-weight:600}} .pk{{color:var(--pick);margin-left:6px}}
dl{{display:grid;grid-template-columns:auto 1fr;gap:1px 8px;margin:0;font-size:.82rem;font-variant-numeric:tabular-nums}} dt{{color:var(--ink2)}} dd{{margin:0}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}} th,td{{padding:4px 8px;border-bottom:1px solid var(--rule);text-align:left}} .num{{text-align:right}}
.legend span{{display:inline-flex;align-items:center;gap:6px;margin-right:16px}} .sw{{width:12px;height:12px;border-radius:2px;display:inline-block}}
</style><main>
<h1>Exposure sweep under two 5300 K LEDs</h1>
<p class="muted">{e(c['experiment'])} · nereus002 IMX708 · 2026-10-05 (PDT evening, Nick at the rig)</p>
<section><h2>Setup</h2><ul>
<li><b>Light:</b> {e(c['lights'])}. Both panels in frame (left near centre-left, right at the right edge); room otherwise dark. The first test with two lamps instead of one. All analysis runs on the card area and the MEDIUM / SMALL ROIs, which do not include the panels; whole-frame clipping masks them.</li>
<li><b>Card:</b> V1 + Pixel Perfect checker, ~1 m (Nick, deliberate, pool-like). Estimated from the card's tag spacing: {c['distance_m_estimate']} m (ESTIMATE: nominal lens, no distortion model). Located on all 4 tags (on the 1/15 s frame); grey 128 patch ≈ {pf['patch_px']} binned px.</li>
<li><b>Sweep:</b> 1/250 → 1/15 s at the gain floor (asked 1.0, applied 1.1228), AWB auto per frame. Truth = the V1 card as measured on the IMX708 in air, 2026-09-28 (single lamp).</li></ul></section>
<section><h2>Frames — pick {frac(pick_us)}</h2>
<p class="muted">Mac (card area, full resolution): {e(c['mac_pick']['reason'])}. Pi (on the rig, MEDIUM ROI because its decimated card search misses the card at ~1 m): {e(str((c.get('pi_pick') or {}).get('reason')))}. Thumbnails DOWNSCALED, one display scale (the 1/15 s frame's 99th percentile = white).</p>
<p class="muted"><b>Flare check:</b> the black patch's level relative to white is {', '.join(f"{f['black_patch']['black_over_white_g']:.3f}" for f in c['frames'])} across the frames (rising only where the white clips), against {c['frames'][0]['black_patch']['truth_black_over_white']:.3f} for the printed card as measured. Blacks are not lifted: no visible veiling flare on the card from the panels.</p>
<div class="strip">{''.join(cells)}</div></section>
<section><h2>ΔE2000 per patch — pick ({frac(pick_us)}) vs today's JPEG</h2>
<p class="legend"><span><i class="sw" style="background:var(--pick)"></i>pick, camera colour (uncorrected)</span><span><i class="sw" style="background:var(--jpg)"></i>today's production JPEG (pjpg q{pj['quality']}, {pj['bytes']:,} B, {pj['messages']} msgs), uncorrected</span><span>hatched = clipped in the JPEG</span></p>
{bar_chart(pf['uncorrected']['de'], pj['uncorrected']['de'])}
<div class="wrap"><table><thead><tr><th>patch</th><th class=num>pick · camera colour</th><th class=num>pick · card fit</th><th class=num>JPEG · as delivered</th><th class=num>JPEG · card fit</th><th class=num>single lamp S4 · camera colour</th><th class=num>single lamp S4 · card fit</th></tr></thead><tbody>{''.join(rows)}</tbody>
<tfoot>
<tr><th>mean / median / max (n)</th><td colspan=6></td></tr>
{srow('pick', pf)}{srow("today's JPEG", pj)}{srow('single lamp (S4)', s4) if s4 and 'uncorrected' in s4 else ''}</tfoot></table></div>
<p class="muted">Patches used: the 17 card patches minus clipped ones (black excluded: no stable chroma). Camera colour = linear RAW × the frame's AWB gains → the frame's colour matrix (rpicam metadata) → sRGB, one exposure scale matching grey 128's luminance. Card fit = 3×3 least squares to the truth on the same patches (in-sample). The JPEG is today's production path: the auto full-res JPEG → rc_jpeg_encoder pjpg (1600×900 → 1000×562, q ladder under 195 msgs), decoded and sampled on the same patches. <b>Auto exposure (49 ms, gain 2.0) clipped 12 of 17 patches in the JPEG</b>, so it is scored on 5 dark patches only: its numbers are not comparable patch-for-patch.</p></section>
<section><h2>Neutrality, white balance, noise, clipping (pick)</h2>
<div class="wrap"><table><thead><tr><th>grey</th><th class=num>a*, b* camera colour</th><th class=num>a*, b* card fit</th><th class=num>a*, b* JPEG</th></tr></thead><tbody>{grey_rows}</tbody></table></div>
<ul>
<li><b>White patch level:</b> {' / '.join(f'{v:.3f}' for v in pf['white_level'])} of full scale (R/G/B, linear RAW) — headroom, not clipped.</li>
<li><b>Scene CCT from the card greys:</b> {cct.get('cct_k')} K on the IMX708's own AWB curve (R/G {cct.get('r_over_g')}, B/G {cct.get('b_over_g')}, {cct.get('off_curve')} off the curve) vs nominal 5300 K; card WB gains R {cct.get('wb_gains_card', ['?','?'])[0]}, B {cct.get('wb_gains_card', ['?','?'])[1]} vs the tuning's 5300 K gains {cct.get('wb_gains_tuning_5300k')}; the camera's AWB chose {pf['awb_gains'][0]:.3f}, {pf['awb_gains'][1]:.3f} ({pf['awb_cct']} K). The AWB's warmer choice is the blue cast (b* −6 to −11) on the light greys in camera colour.</li>
<li><b>Red SNR on grey 128:</b> {pf['red_snr_grey128']} (binned pixels; includes print texture).</li>
<li><b>Clipping %</b> (R/G/B): card area {pf['clipping']['card_area_pct']}; whole frame excluding the light panels {pf['clipping']['frame_excl_lights_pct']} (panels masked: {pf['clipping']['light_mask_pct']} % of the frame).</li>
<li><b>Sharpness</b> (card area, noise-corrected): {pf['sharpness_card']:.3f}; 97 % of the sharpest frame.</li></ul></section>
<section><h2>Against the earlier single-lamp frame (S4, 2026-09-30, cool lamp, stop −1)</h2>
<p>Single lamp: camera colour ΔE {s4.get('uncorrected', {}).get('median')} median, card fit {s4.get('card_fit', {}).get('median')}; CCT {s4.get('cct', {}).get('cct_k')} K; red SNR {s4.get('red_snr_grey128')}. Two LEDs (this pick): camera colour {pf['uncorrected']['median']}, card fit {pf['card_fit']['median']}; CCT {cct.get('cct_k')} K; red SNR {pf['red_snr_grey128']}.</p>
<p class="muted">What differs (more than one variable — CLAUDE.md §10): one lamp vs two, lamp CCT (S4 cool lamp ≈ {s4.get('cct', {}).get('cct_k')} K by the same estimate), card at ~0.5 m vs ~1 m (patches ~{s4.get('patch_px')} vs ~{pf['patch_px']} binned px), lamps straight-on vs in frame, exposure 103 ms vs 17 ms. The card truth was measured in the S4-style setup, so S4 is partly in-sample.</p>
<p class="muted"><b>Why the card fit is worse than the camera colour here:</b> the truth's greys come from the single-lamp measurement session and are compressed (truth black/white 0.074; today's card reads 0.028, so no veiling flare on the card: rather the opposite). A 3×3 fit spreads that tone mismatch over every patch; a 3×3 + offset fit does not fix it (median 3.69). The greys' error is mostly the truth, not the camera.</p></section>
<section><h2>OpenMV N6 / AE3</h2><p>Swept too (5 RAWs each, read-backs exact), but no colour metrics: the card was not located on their 1280×800 RAWs at ~1 m, and their centre fallback region read clipped on the longer frames (an LED panel in view): N6 picked 1/250 s, AE3 none. They need the card closer (pool spec: ~0.5 m) or a card ROI hint.</p></section>
</main>'''
    Path(out).write_text(page, encoding="utf-8")
    print(out, round(Path(out).stat().st_size / 1e6, 2), "MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:4]))
