"""WIDE reading (b): a production-style pjpg of the FULL frame at the same budget (EM, 2026-10-02).

Full view → prepare_source (Lanczos) at output width 1000 (today's still.output_width) and 2304
(the binned nrjxl's sampling) → the field ladder under the 195-message cap. Scored on the same
ΔE ROI and patch set as the other WIDE rows; rows appended to metrics.csv, renders added.
"""
import csv
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from compression_study.presets import preset_study as P
from compression_study import rois

run = Path(sys.argv[1])
rc, rc_jpeg = P.load_prod(Path(sys.argv[2]))
data = Path(sys.argv[3])
from nereus_camera_test_rig.color.card import load_card  # noqa: E402
from nereus_camera_test_rig.color.metrics import srgb8_to_linear  # noqa: E402
card = load_card(rois.CARD)
truth = {p.id: srgb8_to_linear(np.asarray(p.truth, float)) for p in card.patches}
roi_all = rois.load(P.REPO / "compression_study" / "config" / "card_rois.yaml")
ids = json.loads((run / "patch_sets.json").read_text())["WIDE"]
comp = P.TODAY
new = []
for lamp in ("cool", "warm"):
    full = rc.read_dng_crop(str(data / f"{lamp}_imx708" / "stop_-1_r0.dng"), (0, 0, 4608, 2592))
    mos, cfa, black, white = full["mosaic"], full["cfa"], full["black"], full["white"]
    roi = roi_all[f"imx708_{lamp}"]
    gq = np.asarray(roi["patches"]["gray_mid"]["quad"]) * 2
    (gx0, gy0), (gx1, gy1) = np.floor(gq.min(0)).astype(int), np.ceil(gq.max(0)).astype(int)
    g = P.render_linear(mos, black, white, cfa, (1, 1, 1), (gx0, gy0, gx1 - gx0, gy1 - gy0))
    g = g.reshape(-1, 3).mean(0)
    wb = g[1] / g
    ref_lin = P.render_linear(mos, black, white, cfa, wb, comp)
    ref8 = P.to_srgb8(ref_lin)
    ref_m = P.patch_means(ref_lin, roi, ids, comp)
    for ow in (1000, 2304):
        src = rc_jpeg.prepare_source(str(data / f"{lamp}_imx708" / "stop_-1_r0.jpg"),
                                     (0, 0, 4608, 2592), ow)
        for q in P.LADDER:
            enc = rc_jpeg.encode_progressive(src, q, P.CHUNK)
            if enc["message_count"] <= P.MESSAGE_CAP:
                break
        im = Image.open(io.BytesIO(enc["jpeg_data"])).convert("RGB")
        up = np.asarray(im.resize((4608, 2592), Image.Resampling.LANCZOS))
        x, y, w, h = comp
        box = up[y:y + h, x:x + w]
        lin = P.tone_match(P.from_srgb8(box), ref_lin)
        img8 = P.to_srgb8(lin)
        row = {"lamp": lamp, "preset": "WIDE", "candidate": f"W-pjpg{ow}",
               "label": f"pjpg of the full frame at {ow}×{round(ow * 2592 / 4608)} (production "
                        "prepare_source + ladder)", "target": "50kB", "bytes": enc["jpeg_bytes"],
               "messages": enc["message_count"], "quality": enc["quality"], "distance": "",
               "compare_box": list(comp), "patches": len(ids),
               "ssim": P.ssim(P.luma(ref8), P.luma(img8)),
               "patch_de": P.de_vs_raw(ref_m, P.patch_means(lin, roi, ids, comp)),
               "de_truth": P.de_vs_truth(P.patch_means(P.from_srgb8(box), roi, ids, comp), truth),
               **P.tag_stats(ref8, img8), "verdict": "reading (b)"}
        new.append(row)
        print(lamp, ow, f"q{enc['quality']} {enc['jpeg_bytes']} B {enc['message_count']} msgs "
              f"ssim {row['ssim']:.3f} dEtruth {row['de_truth']:.2f} acu {row['acutance_rel']}")
        if lamp == "cool":
            Image.fromarray(img8).save(run / "renders" / f"WIDE_W-pjpg{ow}_50kB_cmp.webp", "WEBP",
                                       lossless=True)
            P.save_patch_zoom(run / "renders", "WIDE", f"W-pjpg{ow}_50kB", img8, roi, ids, comp)
rows = list(csv.DictReader((run / "metrics.csv").open()))
cols = list(rows[0].keys())
with (run / "metrics.csv").open("a", newline="") as fh:
    wr = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
    for r in new:
        wr.writerow({k: (f"{v:.5g}" if isinstance(v, float) else v) for k, v in r.items()})
