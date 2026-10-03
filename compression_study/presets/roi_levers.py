"""Step A: the two untried levers on the 180-message sweep frames — cjxl effort 7 vs 5, and
the 195-message production cap vs 180 (EM, 2026-10-03).

    python -m compression_study.presets.roi_levers --run <runs/sweep_…> --prod <BM_Devel_Pi>

For 1600×900, 2000×1124 and 2304×1296 (centred on the card, as in roi_sweep) × effort {5, 7}
× budget {180, 195} messages × the 3 frames: the lowest distance that fits, then the same
metrics and verdict rules as roi_sweep / roi_sweep_sheet against today's pjpg. Writes
<run>/levers.csv (per frame) and <run>/levers_summary.csv (medians).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import statistics as st
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from compression_study.metrics import ssim
from compression_study.methods import raw_planes as rp
from compression_study.presets import preset_study as S
from compression_study.presets import roi_sweep as W

SIZES = [(1600, 900), (2000, 1124), (2304, 1296)]
EFFORTS = (5, 7)
BUDGETS = (180, 195)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--prod", required=True)
    ap.add_argument("--frames", type=int, nargs="+", default=[0, 1, 2])
    args = ap.parse_args(argv)
    run = Path(args.run)
    rc, rc_jpeg = S.load_prod(Path(args.prod))
    from compression_study import rois
    from nereus_camera_test_rig.color.card import load_card
    from nereus_camera_test_rig.color.metrics import srgb8_to_linear
    card = load_card(rois.CARD)
    truth = {p.id: srgb8_to_linear(np.asarray(p.truth, float)) for p in card.patches}
    roi = json.loads((run / "rois_r0.json").read_text())
    ids = json.loads((run / "patches.json").read_text())["usable"]
    rows, base_rows = [], []
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
        enc = S.pjpg_today(rc_jpeg, stem.with_suffix(".jpg"))
        pj = Image.open(io.BytesIO(enc["jpeg_data"])).convert("RGB")
        pj_lin = S.from_srgb8(np.asarray(pj.resize(S.TODAY[2:], Image.Resampling.LANCZOS)))
        today_ref = S.render_linear(mos, black, white, cfa, wb, S.TODAY)
        pj_core_tm = W.sub(S.tone_match(pj_lin, today_ref), S.TODAY, W.CORE)
        _, pj_de = W.card_fit(S.patch_means(W.sub(pj_lin, S.TODAY, W.CORE), roi, ids, W.CORE),
                              truth, ids)
        base = {"ssim": ssim(S.luma(core_ref8), S.luma(S.to_srgb8(pj_core_tm))), "de": pj_de,
                "acu": S.tag_stats(core_ref8, S.to_srgb8(pj_core_tm))["acutance_rel"]}
        base_rows.append(base)
        for w, h in SIZES:
            box = W.roi_box(w, h)
            x, y = box[:2]
            crop = rc.read_dng_crop(str(stem.with_suffix(".dng")), box)
            codes = rc.code_planes(crop)
            ref8 = S.to_srgb8(S.render_linear(mos, black, white, cfa, wb, box))
            for eff in EFFORTS:
                S.EFFORT = eff  # nrjxl_blob reads the module global at call time
                cache: dict = {}
                with tempfile.TemporaryDirectory(prefix="s28l_") as td:
                    make = lambda d: S.nrjxl_blob(rc, crop, codes, colour, d, "native",  # noqa: E731
                                                  box, (4608, 2592), Path(td))
                    for msgs in BUDGETS:
                        d, blob, ok = S.search_distance(make, msgs * 288, cache)
                        n = rc.message_count(len(blob), S.CHUNK)
                        row = {"frame": fi, "roi": f"{w}x{h}", "effort": eff, "budget": msgs,
                               "distance": d, "bytes": len(blob), "messages": n,
                               "fits": bool(ok and n <= msgs)}
                        if row["fits"]:
                            canvas = np.zeros((2592, 4608))
                            canvas[y:y + h, x:x + w] = rp.decode(blob)
                            lin = S.render_linear(canvas, black, white, cfa, wb, box)
                            core = W.sub(lin, box, W.CORE)
                            im = S.patch_means(core, roi, ids, W.CORE)
                            row.update({
                                "ssim": round(ssim(S.luma(core_ref8), S.luma(S.to_srgb8(core))), 4),
                                "ssim_roi": round(ssim(S.luma(ref8), S.luma(S.to_srgb8(lin))), 4),
                                "de_truth": round(W.card_fit(im, truth, ids)[1], 3),
                                "de_raw": round(S.de_vs_raw(ref_m, im), 3),
                                "acu": round(S.tag_stats(core_ref8, S.to_srgb8(core))["acutance_rel"], 3)})
                        rows.append(row)
                        print(f"[r{fi}] {w}x{h} e{eff} {msgs} msgs: d={d:.3f} {len(blob)} B {n} msgs"
                              + (f" SSIM {row['ssim']:.3f}/{base['ssim']:.3f} ΔE {row['de_truth']:.2f}"
                                 f" vsRAW {row['de_raw']:.2f} acu {row['acu']:.2f}/{base['acu']:.2f}"
                                 if row["fits"] else " DOES NOT FIT"), flush=True)
    S.EFFORT = 5
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(run / "levers.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=keys)
        wr.writeheader()
        wr.writerows(rows)
    b_ssim = st.median(b["ssim"] for b in base_rows)
    b_de = st.median(b["de"] for b in base_rows)
    b_acu = st.median(b["acu"] for b in base_rows)
    summ = []
    for w, h in SIZES:
        for eff in EFFORTS:
            for msgs in BUDGETS:
                rr = [r for r in rows if r["roi"] == f"{w}x{h}" and r["effort"] == eff
                      and r["budget"] == msgs]
                s = {"roi": f"{w}x{h}", "effort": eff, "budget": msgs,
                     "fits": all(r["fits"] for r in rr)}
                for k in ("distance", "bytes", "messages", "ssim", "ssim_roi", "de_truth",
                          "de_raw", "acu"):
                    v = [r[k] for r in rr if k in r]
                    s[k] = round(st.median(v), 4) if v else ""
                if s["fits"]:
                    s["colour"] = ("PASS" if s["de_truth"] <= b_de else
                                   "WARN" if s["de_truth"] <= b_de * 1.05 else "FAIL")
                    acu_ok = abs(s["acu"] - 1) <= abs(b_acu - 1) + 0.02
                    s["detail"] = ("PASS" if s["ssim"] >= b_ssim and acu_ok else
                                   "WARN" if s["ssim"] >= b_ssim else "FAIL")
                summ.append(s)
    summ.append({"roi": "pjpg", "ssim": round(b_ssim, 4), "de_truth": round(b_de, 3),
                 "acu": round(b_acu, 3)})
    keys = list(dict.fromkeys(k for r in summ for k in r))
    with open(run / "levers_summary.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=keys)
        wr.writeheader()
        wr.writerows(summ)
    for s in summ:
        print(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
