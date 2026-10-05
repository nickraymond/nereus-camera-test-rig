"""One small sheet for an exposure-sweep experiment: each camera's frames side by side with their
scores and the pick highlighted (pool tool, 2026-10-05).

    python -m compression_study.presets.sweep_sheet <experiment folder> <out dir>

Display: each frame rendered from its RAW (2x2 binning, a grey-world white balance taken from the
longest frame) with ONE display scale per camera — the longest frame's 99th percentile maps to
white — so shorter shutters look darker, as captured. A 1:1 crop of
the frame centre (or the card box) under each frame shows blur. Downscaled thumbnails are
labelled as such.
"""

from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from nereus_camera_test_rig.color.raw_io import bin2x2, normalize, read_dng, read_openmv_bayer


def _b64(im: Image.Image, fmt="JPEG") -> str:
    buf = io.BytesIO()
    im.save(buf, fmt, **({"quality": 88} if fmt == "JPEG" else {}))
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode()


def main(exp: str, out: str) -> int:
    exp_dir, out_dir = Path(exp), Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rec = json.loads((exp_dir / "experiment.json").read_text())
    rows = []
    for cam, e in rec["exposure_sweeps"].items():
        frames = e.get("frames", [])
        ext = "dng" if cam == "imx708" else "bayer"
        paths = {p.name: p for p in (exp_dir / "captures" / cam).glob(f"*sweep*.{ext}")}
        rgb = []
        for f in frames:
            p = paths[f["file"]]
            fr = read_dng(p) if ext == "dng" else read_openmv_bayer(p)
            lin, sat, cfa = normalize(fr)
            b, _ = bin2x2(lin, cfa, sat)
            rgb.append(b)
        wb = np.array([0.5, 1.0, 0.5]) / np.maximum(np.median(rgb[-1].reshape(-1, 3), 0), 1e-4)
        wb = wb / wb[1]
        scale = 1 / max(float(np.percentile(rgb[-1][..., 1], 99)), 1e-4)
        pick = (e.get("pick") or {}).get("shutter_us")
        cells = []
        for f, b in zip(frames, rgb):
            disp = (np.clip(b * wb * scale, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
            h, w = disp.shape[:2]
            thumb = Image.fromarray(disp).resize((360, round(360 * h / w)), Image.Resampling.LANCZOS)
            cy, cx = h // 2, w // 2
            crop = Image.fromarray(disp[cy - 90:cy + 90, cx - 120:cx + 120])
            sc = f["scores"]
            sharp = {k: (f"{v:.3f}" if v is not None else "n/a") for k, v in sc["sharpness"].items()}
            cells.append({"shutter": f["shutter_us"], "thumb": _b64(thumb), "crop": _b64(crop, "PNG"),
                          "level": sc["level_p995"], "clip": sc["clip_frac"],
                          "clipped": sc["clipped"], "sharp": sharp,
                          "snr": sc["red_snr_global"], "pick": f["shutter_us"] == pick})
        rows.append({"cam": cam, "card": e.get("card"), "reason": (e.get("pick") or {}).get("reason"),
                     "pick": pick, "cells": cells, "metric": (e.get("pick") or {}).get("metric")})

    def frac(us):
        return f"1/{round(1e6 / us)} s"

    html = ['''<meta charset="utf-8">
<title>Exposure Sweep Check</title>
<style>
:root{--bg:#f3f5f6;--panel:#fff;--ink:#121a20;--ink2:#46535d;--rule:#d6dde2;--pick:#0f7c4a;--pickbg:#e3f3ea;--warn:#9b2c1f}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--pick:#7fd3a3;--pickbg:#16301f;--warn:#f0a597}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b2bec7;--rule:#2a343c;--pick:#7fd3a3;--pickbg:#16301f;--warn:#f0a597}
body{background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:18px 40px}
main{max-width:1900px;margin:0 auto;display:grid;gap:16px} h1{font-size:1.45rem;margin:0} h2{font-size:1.05rem;margin:0}
p{margin:0;max-width:110ch} .muted{color:var(--ink2)} section{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px;display:grid;gap:8px}
.strip{display:grid;grid-template-columns:repeat(5,minmax(180px,1fr));gap:8px;overflow-x:auto}
.cell{border:1px solid var(--rule);border-radius:5px;padding:6px;display:grid;gap:4px;align-content:start}
.cell.pick{border:2px solid var(--pick);background:var(--pickbg)} .cell img{width:100%;height:auto;display:block;border-radius:3px}
.crop{image-rendering:pixelated} .tag{font-weight:600} .pick .tag::after{content:" ← PICK";color:var(--pick)}
dl{display:grid;grid-template-columns:auto 1fr;gap:1px 8px;margin:0;font-variant-numeric:tabular-nums;font-size:.85rem} dt{color:var(--ink2)} dd{margin:0}
.clip{color:var(--warn);font-weight:600}
</style><main>
<h1>Exposure sweep: bench dry run</h1>''']
    html.append(f'''<p class="muted">{rec["experiment_id"]} on nereus002 ({rec["timestamp"]}). Operator note (written before the frames were seen): “{rec.get("operator_notes","")}”.
Each camera took 5 RAWs back to back at 1/250, 1/125, 1/60, 1/30 and 1/15 s, with gain locked at its floor (IMX708 1.12×, N6/AE3 3.15 dB).
Rule: drop clipped frames and frames too dark to judge (&lt; 3 % of full scale); keep frames whose sharpness is within 10 % of the sharpest; pick the <b>longest</b> shutter (most light, least red noise).
Sharpness = Laplacian energy of the binned green plane minus its noise share, over mean² (card area when the card is found, else the frame centre).</p>
<p class="muted"><b>Display:</b> thumbnails are DOWNSCALED whole frames, one display scale per camera (the 1/15 s frame's 99th percentile = white), so shorter shutters look darker, as captured. Under each: a 1:1 crop (240×180 binned px) of the frame centre. During this run someone at the rig was adjusting a light panel; their moving hands are the motion, and the panel lit the OpenMV boards straight on.</p>''')
    for r in rows:
        html.append(f'<section><h2>{r["cam"]}: pick {frac(r["pick"]) if r["pick"] else "none"}</h2>'
                    f'<p class="muted">{r["reason"]}. Metric: {r["metric"]}; {r["card"]}.</p><div class="strip">')
        for c in r["cells"]:
            sharp = " · ".join(f"{k} {v}" for k, v in c["sharp"].items())
            html.append(f'''<div class="cell{' pick' if c['pick'] else ''}"><span class="tag">{frac(c['shutter'])}</span>
<img src="{c['thumb']}" alt="{r['cam']} at {frac(c['shutter'])}, downscaled"><img class="crop" src="{c['crop']}" alt="1:1 centre crop">
<dl><dt>sharpness</dt><dd>{sharp}</dd><dt>level p99.5</dt><dd>{c['level']:.3f}</dd>
<dt>clipped</dt><dd{' class="clip"' if c['clipped'] else ''}>{c['clip'] * 100:.2f} %{' (CLIPPED)' if c['clipped'] else ''}</dd>
<dt>red SNR</dt><dd>{c['snr']}</dd></dl></div>''')
        html.append("</div></section>")
    html.append("</main>")
    (out_dir / "index.html").write_text("\n".join(html), encoding="utf-8")
    print(out_dir / "index.html", round((out_dir / "index.html").stat().st_size / 1e6, 1), "MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:3]))
