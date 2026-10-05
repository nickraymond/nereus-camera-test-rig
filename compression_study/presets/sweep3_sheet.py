"""Sweep sheet v4: three cameras side by side (IMX708, N6, AE3) from sweep_colour3.py output.

    python -m compression_study.presets.sweep3_sheet <experiment> <colour3.json> <out.html> \
        [embed|split]

Keepable (embed: one self-contained HTML, images inside) or artifact (split: images as files).
Every image opens in the zoom lightbox at full resolution (keepable.py). Best frames are lossless
WebP; the other ladder frames lossy WebP q90 (labelled) to stay under ~50 MB.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

from compression_study.presets.keepable import LIGHTBOX, Sink  # noqa: E402
from nereus_camera_test_rig.color.raw_io import (  # noqa: E402
    demosaic_bilinear,
    normalize,
    read_dng,
    read_openmv_bayer,
)

CAMS = [("imx708", "IMX708 (Pi, rolling shutter, 12 MP)"),
        ("openmv_n6", "OpenMV N6 (global shutter, 1 MP)"),
        ("openmv_ae3", "OpenMV AE3 (global shutter, 1 MP)")]
ORDER = ["gray_white", "gray_light", "gray_mid", "gray_dark", "cream", "tan", "ochre", "orange",
         "brown", "dark_brown", "coral_pink", "red_orange", "yellow", "green", "cyan", "blue",
         "magenta"]
COLS = {"imx708": "var(--c1)", "openmv_n6": "var(--c2)", "openmv_ae3": "var(--c3)", "jpeg": "var(--c4)"}


def frac(us):
    return f"1/{round(1e6 / us)} s"


def read(p: Path):
    return read_dng(p) if p.suffix == ".dng" else read_openmv_bayer(p)


def render(fr, wb, scale) -> np.ndarray:
    lin, _, cfa = normalize(fr)
    rgb = demosaic_bilinear(lin, cfa).astype(np.float32)
    del lin
    return (np.clip(rgb * wb * scale, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)


def chart(c: dict) -> str:
    """Grouped bars: card-fit ΔE per patch for the three cameras + today's JPEG as delivered."""
    series = [(k, (c["cameras"][k].get("best") or {}).get("card_fit", {}).get("de", {}))
              for k, _ in CAMS] + [("jpeg", c["production_jpeg"]["uncorrected"]["de"])]
    W, H, left, top, bottom = 980, 320, 44, 18, 90
    n = len(ORDER)
    cw = (W - left - 10) / n
    vmax = max([v for _, d in series for v in d.values()] + [5]) * 1.1
    vmax = float(np.ceil(vmax / 5) * 5)
    y = lambda v: top + (H - top - bottom) * (1 - v / vmax)  # noqa: E731
    bw = cw * 0.8 / len(series)
    p = [f'<svg viewBox="0 0 {W} {H}" style="width:100%;height:auto" role="img" '
         'aria-label="ΔE2000 per patch after the card fit, per camera, and today\'s JPEG">',
         '<defs><pattern id="hx" width="6" height="6" patternUnits="userSpaceOnUse" '
         'patternTransform="rotate(45)"><rect width="6" height="6" fill="var(--panel)"/><line x1="0" '
         'y1="0" x2="0" y2="6" stroke="var(--ink2)" stroke-width="1.5"/></pattern></defs>']
    for t in range(0, int(vmax) + 1, 5):
        p.append(f'<line x1="{left}" x2="{W - 10}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="var(--rule)"/>'
                 f'<text x="{left - 6}" y="{y(t) + 4:.1f}" text-anchor="end" font-size="11" '
                 f'fill="var(--ink2)">{t}</text>')
    for i, pid in enumerate(ORDER):
        x0 = left + i * cw + cw * 0.1
        for j, (k, d) in enumerate(series):
            v = d.get(pid)
            x = x0 + j * bw
            if v is None:
                p.append(f'<rect x="{x:.1f}" y="{y(0) - 8:.1f}" width="{bw - 1:.1f}" height="8" '
                         f'fill="url(#hx)"><title>{pid}: {k} not available</title></rect>')
            else:
                p.append(f'<rect x="{x:.1f}" y="{y(v):.1f}" width="{bw - 1:.1f}" height="{y(0) - y(v):.1f}" '
                         f'rx="1.5" fill="{COLS[k]}"><title>{pid}: {k} ΔE {v:.2f}</title></rect>')
        p.append(f'<text transform="translate({left + i * cw + cw / 2:.1f},{H - bottom + 10}) rotate(55)" '
                 f'font-size="11" fill="var(--ink2)">{pid.replace("gray_", "grey ")}</text>')
    p.append("</svg>")
    return "".join(p)


def main(exp: str, colour: str, out: str, mode: str = "split") -> int:
    exp_dir, c = Path(exp), json.loads(Path(colour).read_text())
    sink = Sink(Path(out).parent, mode == "embed")
    e = html.escape
    rec = json.loads((exp_dir / "experiment.json").read_text())
    best_cards, strips = [], []
    for cam, title in CAMS:
        cc = c["cameras"][cam]
        b = cc.get("best") or {}
        frames = cc["frames"]
        d = exp_dir / "captures" / cam
        # one display scale + WB per camera, from the best frame
        bf = read(d / b["file"])
        lin, _, cfa = normalize(bf)
        rgb = demosaic_bilinear(lin, cfa)
        g = np.median(rgb.reshape(-1, 3), 0)
        wb = (g[1] / np.maximum(g, 1e-4)).astype(np.float32)
        scale = np.float32(1 / max(float(np.percentile(rgb[..., 1], 99.5)), 1e-4))
        del lin, rgb
        best_img = sink.img(render(bf, wb, scale), f"{title}: best frame {frac(b['shutter_us'])}, "
                            "full resolution, lossless", 640)
        fl = cc["flicker"]
        unc = b.get("uncorrected", {})
        fit = b.get("card_fit", {})
        cct = b.get("cct") or {}
        rows = [("pick (Mac, card area)", f"{frac(b['shutter_us'])} — {e(cc['mac_pick']['reason'])}"),
                ("Pi pick", e(str((cc.get('pi_pick') or {}).get('shutter_us'))) + " µs"),
                ("card located", f"{e(cc['method'])} on {e(cc['located_on'])}; tags {cc['tags_found']}"),
                ("patches used", f"{b.get('n')} (median {cc['patch_px']} binned px each)"),
                ("ΔE uncorrected", (f"median {unc['median']} · mean {unc['mean']} · max {unc['max']} — "
                                    f"{e(b.get('uncorrected_kind', ''))}") if unc else "n/a (no grey usable)"),
                ("ΔE card fit", f"median {fit.get('median')} · mean {fit.get('mean')} · max {fit.get('max')} (n {fit.get('n')})"),
                ("white level (R/G/B)", " / ".join(f"{v:.3f}" for v in b["white_level"]) if b.get("white_level") else "n/a"),
                ("CCT / WB", (f"{cct['cct_k']} K (R/G {cct['r_over_g']}, B/G {cct['b_over_g']}) vs nominal 5300 K"
                              if cct.get("cct_k") else
                              (f"R/G {cct['r_over_g']}, B/G {cct['b_over_g']} (no CCT model for this RAW)"
                               if cct else "n/a (no grey usable)"))),
                ("red SNR", f"{b['red_snr']['snr']} on {b['red_snr']['patch']} ({b['red_snr']['n_px']} px)" if b.get("red_snr") else "n/a"),
                ("black / white (flare)", f"{b['black_patch']['black_over_white_g']} (truth {b['black_patch']['truth']})" if b.get("black_patch") else "n/a (left side not sampled)"),
                ("clipping % card / ROI", f"{b['card_clip_pct']} / {b['roi_clip_pct']}"),
                ("sharpness (card)", f"{b['sharpness_card']:.3f}" if b.get("sharpness_card") else "n/a"),
                ("flicker", "none: brightest-patch spread over 3 repeats ≤ "
                 + f"{max(v['level_cv_pct'] for v in fl.values() if v['flag'] is not None):.2f} %"
                 + (" (" + ", ".join(frac(int(k)) for k, v in fl.items() if v["flag"] is None)
                    + " too dark to judge)" if any(v["flag"] is None for v in fl.values()) else "")
                 if not any(v["flag"] for v in fl.values()) else
                 "FLAGGED at " + ", ".join(frac(int(k)) for k, v in fl.items() if v["flag"]))]
        dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
        best_cards.append(f'<div class="cam"><h3>{e(title)}</h3>{best_img}<dl>{dl}</dl></div>')
        # ladder strip: the first repeat of each shutter, same WB and display scale
        seen, cells = set(), []
        for f in frames:
            if f["shutter_us"] in seen:
                continue
            seen.add(f["shutter_us"])
            reps = [x for x in frames if x["shutter_us"] == f["shutter_us"]]
            is_best = f["file"] == b["file"] or f["shutter_us"] == b["shutter_us"]
            im = sink.img(render(read(d / f["file"]), wb, scale),
                          f"{title}: {frac(f['shutter_us'])}, full resolution (WebP q90)", 300,
                          lossless=False)
            sharp = [x["sharpness_card"] for x in reps if x["sharpness_card"]]
            fli = fl[str(f["shutter_us"])]
            cells.append(f'''<div class="cell{' pick' if is_best else ''}"><b>{frac(f['shutter_us'])}{' ← pick' if is_best else ''}</b>{im}
<dl><dt>read-back</dt><dd>{e(str({k: v for k, v in (f.get('readback') or {}).items() if v is not None}))}</dd>
<dt>brightest patch</dt><dd>{fli['patch_level']:.3f}</dd>
<dt>card clip % RGB</dt><dd>{' / '.join(f'{v:.2f}' for v in f['card_clip_pct'])}</dd>
<dt>sharpness ×{len(reps)}</dt><dd>{' · '.join(f'{v:.3f}' for v in sharp) or 'n/a'}</dd>
<dt>repeat spread</dt><dd>{fli['level_cv_pct']} %{' (too dark)' if fli['flag'] is None else ''}</dd></dl></div>''')
        strips.append(f'<section><h2>{e(title)} — shutter ladder</h2><div class="strip">{"".join(cells)}</div></section>')

    # per-patch table
    pj = c["production_jpeg"]

    def cell(cam, kind, pid):
        v = ((c["cameras"][cam].get("best") or {}).get(kind) or {}).get("de", {}).get(pid)
        return f"{v:.2f}" if v is not None else '<span class="muted">—</span>'
    def jcell(pid):
        v = pj["uncorrected"]["de"].get(pid)
        return f"{v:.2f}" if v is not None else '<span class="muted">clipped</span>'
    trs = "".join(
        f"<tr><td>{pid}</td><td class=num>{cell('imx708', 'uncorrected', pid)}</td>"
        f"<td class=num>{cell('imx708', 'card_fit', pid)}</td><td class=num>{cell('openmv_n6', 'card_fit', pid)}</td>"
        f"<td class=num>{cell('openmv_ae3', 'uncorrected', pid)}</td><td class=num>{cell('openmv_ae3', 'card_fit', pid)}</td>"
        f"<td class=num>{jcell(pid)}</td></tr>"
        for pid in ORDER)

    def summ(cam, kind):
        s = (c["cameras"][cam].get("best") or {}).get(kind) or {}
        return f"{s['median']} / {s['max']} (n {s['n']})" if s else "—"
    grey = "".join(
        f"<tr><td>{g}</td>" + "".join(
            f"<td class=num>{((c['cameras'][cam].get('best') or {}).get(kind) or {}).get('grey_ab', {}).get(g, '—')}</td>"
            for cam, kind in (("imx708", "uncorrected"), ("imx708", "card_fit"), ("openmv_ae3", "uncorrected"),
                              ("openmv_ae3", "card_fit"))) + "</tr>"
        for g in ("gray_white", "gray_light", "gray_mid", "gray_dark"))
    n6_overlay = sink.img(Path(colour).parent / "openmv_n6_overlay_crop.png",
                          "N6: the sampled patch boxes from the 2-tag homography", 900)
    caveat = e(c["caveat"])
    page = '''<meta charset="utf-8"><title>Three-Camera Sweep</title>
<style>
:root{--bg:#f3f5f6;--panel:#fff;--ink:#121a20;--ink2:#46535d;--rule:#d6dde2;--pick:#1f7a8c;--pickbg:#e3f1f4;--c1:#1f7a8c;--c2:#8a5cc2;--c3:#c0632a;--c4:#7a8590}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--pick:#5fbfd1;--pickbg:#12303a;--c1:#5fbfd1;--c2:#b493e6;--c3:#e8915a;--c4:#9aa5ae}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--pick:#5fbfd1;--pickbg:#12303a;--c1:#5fbfd1;--c2:#b493e6;--c3:#e8915a;--c4:#9aa5ae}
body{background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:18px 40px}
main{max-width:1600px;margin:0 auto;display:grid;gap:14px} h1{font-size:1.45rem;margin:0} h2{font-size:1.08rem;margin:0} h3{font-size:1rem;margin:0}
p,ul{margin:0;max-width:120ch} .muted{color:var(--ink2)} section{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px;display:grid;gap:8px}
.cams{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px} .cam{display:grid;gap:6px;align-content:start}
.strip{display:grid;grid-template-columns:repeat(5,minmax(190px,1fr));gap:8px;overflow-x:auto}
.cell{border:1px solid var(--rule);border-radius:5px;padding:6px;display:grid;gap:4px;align-content:start} .cell.pick{border:2px solid var(--pick);background:var(--pickbg)}
img{width:100%;height:auto;border-radius:3px}
dl{display:grid;grid-template-columns:auto 1fr;gap:1px 8px;margin:0;font-size:.82rem;font-variant-numeric:tabular-nums} dt{color:var(--ink2)} dd{margin:0}
.wrap{overflow-x:auto} table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:.86rem} th,td{padding:4px 8px;border-bottom:1px solid var(--rule);text-align:left} .num{text-align:right}
.legend span{display:inline-flex;align-items:center;gap:6px;margin-right:14px} .sw{width:12px;height:12px;border-radius:2px;display:inline-block}
.cav{border-left:3px solid var(--c3);padding-left:8px}
</style>''' + LIGHTBOX + f'''<main>
<h1>Exposure sweep, three cameras</h1>
<p class="muted">{e(c['experiment'])} · nereus002 · 2026-10-04 PDT evening (Nick at the rig) · click any image to open it full size (zoom to 3200 %, drag to pan, pixels sharp above 100 %)</p>
<section><h2>Setup</h2><ul>
<li><b>Light:</b> {e(c['lights'])}.</li>
<li><b>Card:</b> V1 + Pixel Perfect checker at ~1 m (IMX708 estimate 1.21 m). N6 lens unchanged (its left side is soft).</li>
<li><b>Ladders, gain at each sensor's floor, 3 repeats per step:</b> IMX708 1/250 → 1/15 s (gain 1.12×, focus locked to the still's lens position); N6 and AE3 1/4000 → 1/250 s (3.15 dB). Global-shutter OpenMV sensors: no skew, but blur still scales with exposure.</li>
<li><b>Analysis regions exclude the LED panels:</b> the card area, and per camera ROI (IMX708 1504,846,1600,900; N6 480,260,500,380; AE3 500,250,460,360).</li>
<li><b>Pick rule:</b> drop frames with the card area or white patch clipped, or too dark; keep frames within 10 % of the sharpest (edge acutance on the card); take the longest shutter.</li></ul>
<p class="cav muted"><b>Two fixes since v3:</b> sharpness is now direction-wise edge acutance; the old brightness-normalised Laplacian fell by half on static frames as the exposure rose (8-bit quantisation in dark frames). And the IMX708 focus is locked: its autofocus had moved 0.95 → 2.6 dioptres across the old sweep.</p></section>
<section><h2>Best frame per camera</h2><div class="cams">{''.join(best_cards)}</div>
<p class="muted">Renders: bilinear demosaic of the RAW, grey-world WB from the best frame, one display scale per camera (best frame's 99.5th percentile = white), gamma 2.2. Not colour-corrected.</p></section>
<section><h2>ΔE2000 per patch vs the card truth</h2>
<p class="cav">{caveat}</p>
<p class="legend"><span><i class="sw" style="background:var(--c1)"></i>IMX708 (card fit)</span><span><i class="sw" style="background:var(--c2)"></i>N6 (card fit, right-side patches)</span><span><i class="sw" style="background:var(--c3)"></i>AE3 (card fit)</span><span><i class="sw" style="background:var(--c4)"></i>today's production JPEG (as delivered)</span><span>hatched = not available (not sampled or clipped)</span></p>
{chart(c)}
<div class="wrap"><table><thead><tr><th>patch</th><th class=num>IMX708 · camera colour</th><th class=num>IMX708 · card fit</th><th class=num>N6 · card fit</th><th class=num>AE3 · WB only</th><th class=num>AE3 · card fit</th><th class=num>today's JPEG · as delivered</th></tr></thead>
<tbody>{trs}</tbody><tfoot><tr><th>median / max (n)</th><td class=num>{summ('imx708', 'uncorrected')}</td><td class=num>{summ('imx708', 'card_fit')}</td><td class=num>{summ('openmv_n6', 'card_fit')}</td><td class=num>{summ('openmv_ae3', 'uncorrected')}</td><td class=num>{summ('openmv_ae3', 'card_fit')}</td><td class=num>{pj['uncorrected']['median']} / {pj['uncorrected']['max']} (n {pj['uncorrected']['n']})</td></tr></tfoot></table></div>
<p class="muted">Patches: the card's 17 (black excluded: L*-only reference) minus clipped ones; the N6 uses only the 13 right-hand colour patches (no greys: its left tags do not decode). Card fit = in-sample 3×3 least squares on the same patches. IMX708 camera colour = its AWB gains + colour matrix; the OpenMV RAW has neither, so its "uncorrected" is WB on grey 128 only. Today's JPEG = the production pjpg of the auto still; auto exposure clipped 12 of 17 patches, so it is scored on 5 dark ones.</p>
<p class="cav muted">{caveat}</p></section>
<section><h2>Grey neutrality (a*, b*)</h2><div class="wrap"><table><tr><th>grey</th><th class=num>IMX708 camera colour</th><th class=num>IMX708 card fit</th><th class=num>AE3 WB only</th><th class=num>AE3 card fit</th></tr>{grey}</table></div>
<p class="muted">N6: no grey patch usable (left side). {caveat}</p></section>
<section><h2>N6: where the patches were sampled</h2>{n6_overlay}<p class="muted">Card located from the 8 corners of tags 1 and 3 only; magenta boxes = the 13 colour patches sampled (central 60 %).</p></section>
{''.join(strips)}
<section><h2>Notes</h2><ul>
<li>Every RAW is kept on nereus002 (experiment {e(c['experiment'])}); this run took 45 of 45 frames (the previous run had one N6 USB short read, 1 of 15).</li>
<li>The Pi's own sweep scoring uses the ROIs above (it cannot locate the card at this distance on decimated frames). OpenMV picks match the Mac's card-area picks: N6 {e(str((c['cameras']['openmv_n6'].get('pi_pick') or {}).get('shutter_us')))} µs, AE3 {e(str((c['cameras']['openmv_ae3'].get('pi_pick') or {}).get('shutter_us')))} µs. The IMX708's Pi pick ({e(str((c['cameras']['imx708'].get('pi_pick') or {}).get('shutter_us')))} µs) is one step longer than the Mac's: at 1/30 s the card's white patch clips, which the MEDIUM ROI alone does not see.</li>
<li>Operator notes in experiment.json: “{e(rec.get('operator_notes', ''))}”.</li></ul></section>
</main>'''
    Path(out).write_text(page, encoding="utf-8")
    print(out, round(Path(out).stat().st_size / 1e6, 1), "MB html,", round(sink.bytes / 1e6, 1),
          "MB images,", len(sink.files), "files")
    if sink.files:
        (Path(out).parent / "files.json").write_text(json.dumps(sink.files, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:5]))
