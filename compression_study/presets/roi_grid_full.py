"""Full-view renders for the grid sheet: the whole decoded crop (RAW vs nrjxl) for each example
ROI at each budget, and the full 4608×2592 frame with the seven ROI rectangles (Nick, 2026-10-03).

    python -m compression_study.presets.roi_grid_full --run <runs/sweep_…> --prod <BM_Devel_Pi>

Frame 0, the effort shown in the heat map (grid_sheet/summary.json) at frame 0's own distance
from grid.csv, re-encoded with the production encoder (deterministic: same bytes as the grid
run, checked). Same render as the zoom sliders (camera WB gains, clipped at sensor white, the
RAW's own card matrix, sRGB), lossless WebP at full crop resolution, in <run>/grid_full/.
"""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from compression_study.methods import raw_planes as rp
from compression_study.presets import preset_study as S
from compression_study.presets import roi_sweep as W

EXAMPLES = ("1600x900", "2304x1296", "3072x1728")
BUDGETS = (180, 250, 500)
FONT_B = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--prod", required=True)
    a = ap.parse_args(argv)
    run = Path(a.run)
    out = run / "grid_full"
    out.mkdir(exist_ok=True)
    rc, _ = S.load_prod(Path(a.prod))
    from compression_study import rois
    from nereus_camera_test_rig.color.card import load_card
    from nereus_camera_test_rig.color.metrics import srgb8_to_linear
    card = load_card(rois.CARD)
    truth = {p.id: srgb8_to_linear(np.asarray(p.truth, float)) for p in card.patches}
    roi = json.loads((run / "rois_r0.json").read_text())
    ids = json.loads((run / "patches.json").read_text())["usable"]
    summ = json.loads((run / "grid_sheet" / "summary.json").read_text())
    grid = list(csv.DictReader((run / "grid.csv").open()))
    stem = run / "cap" / "stop_+0_r0"
    meta = json.loads(stem.with_suffix(".json").read_text())
    colour = rc.colour_params(meta)
    full = rc.read_dng_crop(str(stem.with_suffix(".dng")), (0, 0, 4608, 2592))
    mos, cfa, black, white = full["mosaic"], full["cfa"], full["black"], full["white"]
    gr, gb = meta["ColourGains"]
    wb = (gr, 1.0, gb)
    core_ref = S.render_linear(mos, black, white, cfa, wb, W.CORE)
    raw_M, _ = W.card_fit(S.patch_means(core_ref, roi, ids, W.CORE), truth, ids)
    made = {}
    for name in EXAMPLES:
        w, h = (int(v) for v in name.split("x"))
        box = W.roi_box(w, h)
        x, y = box[:2]
        ref_lin = S.render_linear(mos, black, white, cfa, wb, box)
        Image.fromarray(W.show(ref_lin, raw_M)).save(out / f"{name}_ref.webp", "WEBP", lossless=True)
        crop = rc.read_dng_crop(str(stem.with_suffix(".dng")), box)
        codes = rc.code_planes(crop)
        for b in BUDGETS:
            s = summ[f"{name}@{b}"]
            if s.get("nofit"):
                continue
            e = int(s["effort"])
            g = next(r for r in grid if r["frame"] == "0" and r["roi"] == name
                     and int(r["budget"]) == b and int(r["effort"]) == e)
            d = float(g["distance"])
            S.EFFORT = e
            with tempfile.TemporaryDirectory() as td:
                blob = S.nrjxl_blob(rc, crop, codes, colour, d, "native", box, (4608, 2592), Path(td))
            assert len(blob) == int(g["bytes"]), (name, b, len(blob), g["bytes"])
            canvas = np.zeros((2592, 4608))
            canvas[y:y + h, x:x + w] = rp.decode(blob)
            lin = S.render_linear(canvas, black, white, cfa, wb, box)
            Image.fromarray(W.show(lin, raw_M)).save(out / f"{name}_{b}_e{e}.webp", "WEBP",
                                                     lossless=True)
            made[f"{name}@{b}"] = {"effort": e, "distance": d, "bytes": len(blob),
                                   "messages": rc.message_count(len(blob), S.CHUNK)}
            print(name, b, e, d, len(blob), flush=True)
    S.EFFORT = 5
    # the full frame with the ROI rectangles (RAW render, same look), downscaled 2× for display
    frame_lin = S.render_linear(mos, black, white, cfa, wb, (0, 0, 4608, 2592))
    im = Image.fromarray(W.show(frame_lin, raw_M)).resize((2304, 1296), Image.Resampling.LANCZOS)
    dr = ImageDraw.Draw(im)
    f = ImageFont.truetype(FONT_B, 26)
    cols = [(255, 80, 160), (255, 140, 0), (255, 215, 0), (80, 220, 80), (0, 200, 255),
            (150, 120, 255), (255, 255, 255)]
    for (w, h), c in zip(W.SIZES, cols):
        x, y, _, _ = W.roi_box(w, h)
        dr.rectangle([x / 2, y / 2, (x + w) / 2 - 1, (y + h) / 2 - 1], outline=c, width=3)
        dr.text((x / 2 + 6, y / 2 + 4), f"{w}×{h}", fill=c, font=f, stroke_width=3,
                stroke_fill=(0, 0, 0))
    im.save(out / "frame_rois.jpg", quality=92)
    (out / "made.json").write_text(json.dumps(made, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
