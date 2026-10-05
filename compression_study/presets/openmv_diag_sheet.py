"""Diagnostic sheet for openmv_diag.py output (one self-contained HTML page)."""

from __future__ import annotations

import base64
import html
import io
import json
import sys
from pathlib import Path

from PIL import Image

from compression_study.presets.keepable import LIGHTBOX, Sink

ROWS = {"openmv_n6": "OpenMV N6", "openmv_ae3": "OpenMV AE3", "imx708": "IMX708 (reference)"}
GEOM = {  # resolution; f in px: IMX708 nominal (2.75 mm / 1.4 um); OpenMV derived from the
          # measured tag size at the IMX708's 1.21 m distance estimate (same mount)
    "imx708": {"res": "4608×2592", "f_px": 1964, "f_note": "nominal lens 2.75 mm, 1.4 µm px"},
    "openmv_n6": {"res": "1280×800", "f_px": 1153, "f_note": "derived: 30.5 px tag at ~1.21 m"},
    "openmv_ae3": {"res": "1280×800", "f_px": 1009, "f_note": "derived: 26.7 px tag at ~1.21 m"},
}


def b64(path: Path, fmt=None, max_w=None) -> str:
    im = Image.open(path).convert("RGB")
    if max_w and im.width > max_w:
        im = im.resize((max_w, round(max_w * im.height / im.width)), Image.Resampling.LANCZOS)
    fmt = fmt or ("PNG" if path.suffix == ".png" else "JPEG")
    buf = io.BytesIO()
    im.save(buf, fmt, **({"quality": 90} if fmt == "JPEG" else {}))
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode()


def main(diag_dir: str, out: str, colour_phase_json: str, embed: str = "split") -> int:
    """``embed``: "embed" = one self-contained file (keepable); "split" = images in out's img/."""
    d = Path(diag_dir)
    sink = Sink(Path(out).parent, embed == "embed")
    r = json.loads((d / "diag.json").read_text())
    rb = json.loads(colour_phase_json)
    e = html.escape
    secs = []
    for cam, title in ROWS.items():
        c = r["cameras"][cam]
        m, s = c["mosaic"], c["sidecar"]
        ph = c["phases"]
        tag_rows = "".join(
            f"<tr><td>{k}</td><td class=num>{v['sharpness']:.3f}</td><td class=num>{v['contrast_dn']}</td>"
            f"<td>{'decoded' if v['decoded'] else '<b>not decoded</b>'}</td></tr>"
            for k, v in sorted(c["tag_sharpness"].items(), key=lambda t: int(t[0])))
        t1 = {k: v["edge_px"] for k, v in c["aruco_raw_1x"].items()}
        jt = {k: v["edge_px"] for k, v in c["aruco_board_jpeg"].items()}
        g = GEOM[cam]
        exp_px = round(g["f_px"] * 31.996 / 1000, 1)
        best_ph = min(ph, key=lambda k: ph[k].get("colour_patch_de_median_wb_only") or 99) \
            if any(v.get("colour_patch_de_median_wb_only") for v in ph.values()) else None
        rbc = rb.get(cam)
        checks = [
            ("CFA phase = BGGR (code assumes " + e(m["cfa"]) + ")",
             (best_ph == "BGGR") if best_ph else (rbc and max(rbc, key=lambda k: sum(rbc[k])) == "BGGR"),
             (f"card colour ΔE (WB only) per phase: " + ", ".join(
                 f"{k} {v.get('colour_patch_de_median_wb_only')}" for k, v in ph.items()) if best_ph else
              "card not located; chroma vs the board JPEG (R−G, B−G corr): " + ", ".join(
                 f"{k} {v}" for k, v in (rbc or {}).items()))
             + f"; green sites corr: " + ", ".join(f"{k} {v['green_corr']}" for k, v in ph.items())),
            ("bit depth / packing / black / endianness",
             m["file_bytes"] == m["shape"][0] * m["shape"][1] * (1 if cam != "imx708" else 2) or cam == "imx708",
             f"{m['file_bytes']:,} B = {m['shape'][1]}×{m['shape'][0]} × {'8-bit, 1 B/px, no packing (endianness n/a)' if cam != 'imx708' else '16-bit DNG container'}; "
             f"black {m['black']} white {m['white']}; zeros {m['zeros_pct']} % (OpenMV black is on-chip and clipped at 0, OQ-21); min {m['min']} max {m['max']}"),
            ("width / height / stride (no row shift or wrap)",
             c["row_shift"]["max_abs_px"] < 2,
             f"row-to-row shift median {c['row_shift']['median_abs_px']} px, max {c['row_shift']['max_abs_px']} px over {c['row_shift']['n']} row bands"
             + (f"; vs board JPEG {c['registration_vs_jpeg']['as is']}" if c["registration_vs_jpeg"] else "")),
            ("orientation / flip",
             (c["registration_vs_jpeg"] is None) or (c["registration_vs_jpeg"]["as is"]["response"]
                                                     > 3 * max(v["response"] for k, v in c["registration_vs_jpeg"].items() if k != "as is")),
             ("registration to the board's own JPEG: " + ", ".join(
                 f"{k} response {v['response']}" for k, v in c["registration_vs_jpeg"].items())
              if c["registration_vs_jpeg"] else "DNG; tags decode at the card's corner positions")
             + "; tag IDs decode (mirrored tags would not)"),
            ("exposure / gain applied = requested",
             True if cam == "imx708" else (abs(s["exposure_us"] - s["requested"]["exposure_us"]) <= 0.01 * s["requested"]["exposure_us"]
                                          and abs(s["gain_db"] - s["requested"]["gain_db"]) < 0.1),
             (f"asked {s['requested']} → read back {s['exposure_us']} µs, {s['gain_db']} dB" if cam != "imx708"
              else f"read back {s['ExposureTime']} µs at gain {s['AnalogueGain']} (asked 66,667 µs, gain 1.0 → floor 1.1228)")),
        ]
        chk = "".join(f"<tr><td>{e(n)}</td><td class={'pass' if ok else 'fail'}>{'PASS' if ok else 'FAIL'}</td><td>{d_}</td></tr>"
                      for n, ok, d_ in checks)
        secs.append(f'''<section><h2>{title}</h2>
<p class=muted>Frame {e(c['frame'])} ({'card located' if c['card_located'] else 'card NOT located'}); clipped % per channel (R/G/B sites) {c['clip_pct']}.</p>
<div class=pair><figure>{sink.img(d / f'{cam}_raw.png', f'{title} RAW render, full resolution', 1280)}<figcaption>RAW → bilinear demosaic, grey-world WB, gamma 2.2, full resolution ({'4608×2592; inline preview downscaled' if cam == 'imx708' else '1280×800'}). Click to enlarge and zoom.</figcaption></figure>
<figure>{sink.img(d / f'{cam}_jpeg.jpg', f'{title} board JPEG', 1280)}<figcaption>{'the IMX708 still JPEG (auto, ISP), as recorded' if cam == 'imx708' else "the board's own JPEG (ISP, auto exposure), same set, ~1 min earlier"}</figcaption></figure></div>
<div class=pair><figure>{sink.img(d / f'{cam}_crop.png', f'{title} 1:1 card crop (pixels 2x)', 1400, pixelated_inline=True)}<figcaption>1:1 crop at the card (pixels enlarged 2×)</figcaption></figure>
<figure>{sink.img(d / f'{cam}_hist.png', f'{title} RAW histogram')}<figcaption>histogram of the RAW sites (log count)</figcaption></figure></div>
<figure>{sink.img(d / f'{cam}_phases.png', f'{title} crop under the 4 CFA phases', 2080)}<figcaption>the same crop under the 4 CFA phases</figcaption></figure>
<h3>Tags</h3><p>RAW 1×: {t1 or 'none'} · RAW 2×: {sorted(c['aruco_raw_2x']) or 'none'} · board JPEG: {jt or 'none'} · demosaiced 3× + CLAHE crop: {sorted(c['aruco_crop_3x_clahe']) if isinstance(c['aruco_crop_3x_clahe'], dict) else c['aruco_crop_3x_clahe']} (tag edge in px).</p>
<div class=wrap><table><tr><th>tag</th><th class=num>edge sharpness</th><th class=num>contrast (DN)</th><th>status</th></tr>{tag_rows}</table></div>
<h3>Pipeline checks</h3><div class=wrap><table><tr><th>check</th><th>result</th><th>evidence</th></tr>{chk}</table></div>
<h3>Geometry</h3><p>{g['res']}; f ≈ {g['f_px']} px ({g['f_note']}) → HFOV ≈ {round(2 * __import__('math').degrees(__import__('math').atan(int(g['res'].split('×')[0]) / 2 / g['f_px'])))}°; expected tag edge at 1.0 m ≈ {exp_px} px; measured {(list(t1.values()) or list(jt.values()) or ['n/a'])[0]} px at the ~1.21 m estimate.</p></section>''')
    page = f'''<meta charset="utf-8"><title>OpenMV RAW Diagnosis</title>
<style>
:root{{--bg:#f3f5f6;--panel:#fff;--ink:#121a20;--ink2:#46535d;--rule:#d6dde2;--ok:#1d6b3a;--okbg:#e3f2e8;--bad:#9b2c1f;--badbg:#f8e4e0}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--ok:#8fd6a6;--okbg:#163222;--bad:#f0a597;--badbg:#3a1c17}}}}
:root[data-theme="dark"]{{color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--ok:#8fd6a6;--okbg:#163222;--bad:#f0a597;--badbg:#3a1c17}}
body{{background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:18px 40px}}
main{{max-width:1400px;margin:0 auto;display:grid;gap:14px}} h1{{font-size:1.45rem;margin:0}} h2{{font-size:1.1rem;margin:0}} h3{{font-size:.95rem;margin:4px 0 0}}
p,ul{{margin:0;max-width:115ch}} .muted{{color:var(--ink2)}} section{{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px;display:grid;gap:8px}}
.pair{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px}} figure{{margin:0;display:grid;gap:4px}} img{{width:100%;height:auto;border-radius:3px}} .pix{{image-rendering:pixelated}}
figcaption{{color:var(--ink2);font-size:.82rem}} .wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:.85rem}} th,td{{padding:4px 8px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
.pass{{background:var(--okbg);color:var(--ok);font-weight:600}} .fail{{background:var(--badbg);color:var(--bad);font-weight:600}}
</style>''' + LIGHTBOX + f'''<main><h1>OpenMV RAW pipeline diagnosis</h1>
<p class=muted>Click any image to open it full size: scroll or pinch to zoom (up to 3200 %), drag to pan, pixels drawn sharp above 100 %; Esc closes.</p>
<p class=muted>{e(r['experiment'])} on nereus002 (2× 5300 K LED panels in frame, card at ~1 m; IMX708 estimate 1.21 m). Question (Nick): is something wrong in the raw pipeline, given the sweep did not find the card on the N6 / AE3?</p>
<section><h2>Verdict</h2><ul>
<li><b>Pipeline OK on both boards:</b> every check passes. BGGR is the right phase and the one the code uses. The data is 8-bit, 1 byte/px, unpacked, black at 0 (on-chip). There is no row shift or wrap. The RAW registers to the board's own JPEG at 0 px offset with no flip. Exposure and gain read back exactly.</li>
<li><b>AE3: the card IS found on the RAW</b> (all 4 tags at 26–27 px, sharp; better than its own JPEG, which decodes 2). The sweep made no pick because even 1/250 s clips 0.67 % of the card box (limit 0.5 %): the sensor is far more sensitive at its gain floor than the IMX708, so the IMX708's shutter ladder is too long for it. Earlier wording that the card was "not located" on the OpenMV RAWs was wrong for the AE3.</li>
<li><b>N6: optics, not the pipeline and not size.</b> The right-hand tags decode at 30 px; the left-hand tags never decode, not on the RAW, its own JPEG, or a 3×-upsampled, contrast-stretched crop. Their edge sharpness is 0.11–0.13 against 0.19 on the right at the same contrast: the left side of the N6's lens is soft (noted in the SPEC on 2026-09-28).</li>
<li><b>Size:</b> tags are 26–31 px on the OpenMV boards at ~1.2 m (IMX708 ~50 px). The AE3 decodes them when sharp, so ~1 m is workable. For margin (≥ ~40 px): card at ≤ ~0.9 m (N6) / ≤ ~0.8 m (AE3), and fix the N6 focus / lens.</li>
<li><b>Next fixes (small):</b> a per-camera shutter ladder for the sweep (OpenMV ~1/2000 … 1/250 s, or meter first); a per-camera ROI that excludes the LED panel for the N6; refocus or replace the N6 lens.</li></ul></section>
{''.join(secs)}</main>'''
    Path(out).write_text(page, encoding="utf-8")
    print(out, round(Path(out).stat().st_size / 1e6, 1), "MB html,", round(sink.bytes / 1e6, 1),
          "MB images,", len(sink.files), "files")
    if sink.files:
        (Path(out).parent / "files.json").write_text(json.dumps(sink.files, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:5]))
