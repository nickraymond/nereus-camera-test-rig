"""Nick's grid: every sweep ROI × budgets 180 / 250 / 500 messages × effort 5 / 7, on the
3 locked nereus002 frames of 2026-10-03 (via EM, 2026-10-03).

    python -m compression_study.presets.roi_grid --run <runs/sweep_…> --prod <BM_Devel_Pi>

Per cell the distance comes from the Step C byte-target search (``byte_target.search``, no
quality floor here: d_max 15), with an exact bisection as a fallback when the 3-encode search
ends without a fit below d 15. Metrics vs the RAW render of the same frame: SSIM on the card
area (the 800×450 core every ROI contains) and on the ROI's texture window (the most detailed
480×270 window away from the card, picked on the RAW of frame 0), whole-ROI SSIM, tag
acutance, ΔE00 vs RAW on the 10 usable card colour patches (no fit: compression only), ΔE00
vs the card truth after a per-image 3×3 fit (flattering with 10 patches), and today's pjpg
per frame. Writes <run>/grid.csv, <run>/grid_pjpg.csv, <run>/grid_texture.json and, for
frame 0, lossless WebP slider crops in <run>/grid_slides/ (git-ignored output folder).
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import tempfile
import types
from pathlib import Path

import numpy as np
from PIL import Image

from compression_study.common import tool
from compression_study.metrics import ssim
from compression_study.methods import raw_planes as rp
from compression_study.presets import preset_study as S
from compression_study.presets import roi_sweep as W

BUDGETS = (180, 250, 500)
EFFORTS = (5, 7)
SIZES = [(800, 450), (1200, 676), (1600, 900), (2000, 1124), (2304, 1296), (3072, 1728),
         (4608, 2592)]
CARD_AVOID = (2060, 1110, 580, 400)


def load_bt():
    spec = importlib.util.spec_from_file_location(
        "byte_target", Path(__file__).with_name("byte_target.py"))
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    return bt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--prod", required=True)
    ap.add_argument("--frames", type=int, nargs="+", default=[0, 1, 2])
    args = ap.parse_args(argv)
    run = Path(args.run)
    rc, rc_jpeg = S.load_prod(Path(args.prod))
    import sys
    sys.modules["rc_raw_jxl"] = rc
    bt = load_bt()
    bt.shutil = types.SimpleNamespace(which=lambda _n, p=str(tool("cjxl")): p)
    from compression_study import rois
    from nereus_camera_test_rig.color.card import load_card
    from nereus_camera_test_rig.color.metrics import srgb8_to_linear
    card = load_card(rois.CARD)
    truth = {p.id: srgb8_to_linear(np.asarray(p.truth, float)) for p in card.patches}
    roi = json.loads((run / "rois_r0.json").read_text())
    ids = json.loads((run / "patches.json").read_text())["usable"]
    slides = run / "grid_slides"
    slides.mkdir(exist_ok=True)
    tex_path = run / "grid_texture.json"
    textures = json.loads(tex_path.read_text()) if tex_path.exists() else {}
    rows, prow = [], []
    for fi in args.frames:
        stem = run / "cap" / f"stop_+0_r{fi}"
        meta = json.loads(stem.with_suffix(".json").read_text())
        colour = rc.colour_params(meta)
        full = rc.read_dng_crop(str(stem.with_suffix(".dng")), (0, 0, 4608, 2592))
        mos, cfa, black, white = full["mosaic"], full["cfa"], full["black"], full["white"]
        gr, gb = meta["ColourGains"]
        wb = (gr, 1.0, gb)
        core_ref = S.render_linear(mos, black, white, cfa, wb, W.CORE)
        core_ref8 = S.to_srgb8(core_ref)
        ref_m = S.patch_means(core_ref, roi, ids, W.CORE)
        raw_M, raw_de = W.card_fit(ref_m, truth, ids)
        enc = S.pjpg_today(rc_jpeg, stem.with_suffix(".jpg"))
        pj = Image.open(io.BytesIO(enc["jpeg_data"])).convert("RGB")
        pj_lin = S.from_srgb8(np.asarray(pj.resize(S.TODAY[2:], Image.Resampling.LANCZOS)))
        today_ref = S.render_linear(mos, black, white, cfa, wb, S.TODAY)
        pj_tm = S.tone_match(pj_lin, today_ref)
        pj_core = W.sub(pj_tm, S.TODAY, W.CORE)
        _, pj_de = W.card_fit(S.patch_means(W.sub(pj_lin, S.TODAY, W.CORE), roi, ids, W.CORE),
                              truth, ids)
        prow.append({"frame": fi, "quality": enc["quality"], "bytes": enc["jpeg_bytes"],
                     "messages": enc["message_count"],
                     "ssim_card": round(ssim(S.luma(core_ref8), S.luma(S.to_srgb8(pj_core))), 4),
                     "de_raw": round(S.de_vs_raw(ref_m, S.patch_means(pj_core, roi, ids, W.CORE)), 3),
                     "de_truth": round(pj_de, 3), "raw_de_truth": round(raw_de, 3),
                     "acu": round(S.tag_stats(core_ref8, S.to_srgb8(pj_core))["acutance_rel"], 3)})
        print(f"[r{fi}] pjpg {prow[-1]}", flush=True)
        for w, h in SIZES:
            name = f"{w}x{h}"
            box = W.roi_box(w, h)
            x, y = box[:2]
            crop = rc.read_dng_crop(str(stem.with_suffix(".dng")), box)
            codes = rc.code_planes(crop)
            ref_lin = S.render_linear(mos, black, white, cfa, wb, box)
            ref8 = S.to_srgb8(ref_lin)
            if name not in textures:
                textures[name] = list(W.texture_window(ref8, box, CARD_AVOID))
            tex = tuple(textures[name])
            tex_ref8 = S.to_srgb8(W.sub(ref_lin, box, tex))
            for eff in EFFORTS:
                S.EFFORT = eff
                blobs: dict = {}
                with tempfile.TemporaryDirectory(prefix="s28g_") as td:
                    def make(d, _td=td):
                        d = round(d, 3)
                        if d not in blobs:
                            blobs[d] = S.nrjxl_blob(rc, crop, codes, colour, d, "native", box,
                                                    (4608, 2592), Path(_td))
                        return blobs[d]
                    bt.encode = lambda _c, _k, _col, d, _e, _x, _w: (len(make(d)), 0)
                    for cap in BUDGETS:
                        target = cap * 288
                        r = bt.search(crop, codes, colour, box, cap, eff, 15.0, td)
                        method = "byte_target"
                        if r["status"] == "ok":
                            d = r["best"]["d"]
                        elif max([t["d"] for t in r["tries"]] or [0]) < 15.0:
                            d, _, ok = S.search_distance(make, target, {})
                            method = "bisection"
                            if not ok:
                                d = None
                        else:
                            d = None
                        n_enc = len(r["tries"])
                        row = {"frame": fi, "roi": name, "budget": cap, "effort": eff,
                               "method": method, "encodes": n_enc}
                        if d is None or len(make(d)) > target:
                            b = len(make(15.0))
                            row.update({"fits": False, "distance": 15.0, "bytes": b,
                                        "messages": rc.message_count(b, S.CHUNK)})
                            rows.append(row)
                            print(f"[r{fi}] {name} e{eff} {cap}: does not fit ({b} B at d 15)",
                                  flush=True)
                            continue
                        blob = make(d)
                        canvas = np.zeros((2592, 4608))
                        canvas[y:y + h, x:x + w] = rp.decode(blob)
                        lin = S.render_linear(canvas, black, white, cfa, wb, box)
                        core = W.sub(lin, box, W.CORE)
                        core8 = S.to_srgb8(core)
                        im = S.patch_means(core, roi, ids, W.CORE)
                        row.update({
                            "fits": True, "distance": d, "bytes": len(blob),
                            "messages": rc.message_count(len(blob), S.CHUNK),
                            "fill": round(len(blob) / target, 4),
                            "ssim_card": round(ssim(S.luma(core_ref8), S.luma(core8)), 4),
                            "ssim_tex": round(ssim(S.luma(tex_ref8),
                                                   S.luma(S.to_srgb8(W.sub(lin, box, tex)))), 4),
                            "ssim_roi": round(ssim(S.luma(ref8), S.luma(S.to_srgb8(lin))), 4),
                            "acu": round(S.tag_stats(core_ref8, core8)["acutance_rel"], 3),
                            "de_raw": round(S.de_vs_raw(ref_m, im), 3),
                            "de_truth": round(W.card_fit(im, truth, ids)[1], 3)})
                        rows.append(row)
                        print(f"[r{fi}] {name} e{eff} {cap}: d={d:.3f} {len(blob)} B "
                              f"{row['messages']} msgs card {row['ssim_card']:.3f} tex "
                              f"{row['ssim_tex']:.3f} acu {row['acu']:.2f} ΔEraw {row['de_raw']:.2f}",
                              flush=True)
                        if fi == args.frames[0]:
                            for tag, view in (("card", W.CARD_VIEW), ("tex", tex)):
                                Image.fromarray(W.show(W.sub(lin, box, view), raw_M)).save(
                                    slides / f"{name}_{cap}_e{eff}_{tag}.webp", "WEBP",
                                    lossless=True)
                if fi == args.frames[0]:
                    for tag, view in (("card", W.CARD_VIEW), ("tex", tex)):
                        Image.fromarray(W.show(W.sub(ref_lin, box, view), raw_M)).save(
                            slides / f"{name}_ref_{tag}.webp", "WEBP", lossless=True)
            tex_path.write_text(json.dumps(textures, indent=1))
    S.EFFORT = 5
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(run / "grid.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=keys)
        wr.writeheader()
        wr.writerows(rows)
    with open(run / "grid_pjpg.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(prow[0]))
        wr.writeheader()
        wr.writerows(prow)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
