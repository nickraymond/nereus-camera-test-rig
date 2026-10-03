"""Largest ROI per message budget — nrjxl at native density vs today's pjpg (Nick, 2026-10-02).

    python -m compression_study.presets.roi_budget --data <primary>/data/s4_20260930 \
        --prod <bm_cam_legacy export>/BM_Devel_Pi

Step 1 of Nick's test design (via the EM session). Cool scene, IMX708 full-sensor DNG.
ROIs at native sensor density (no downsampling): LARGE 2304×1296 centred, MEDIUM = today's
1600×900 crop, SMALL 800×450 over the colour patches. Budgets in 288-byte messages. Per
ROI × budget: the lowest cjxl distance (production e5, range 0.1–15) whose sealed Sprint28
container fits the budget. Metric: ΔE00 vs the V1 card truth after a per-image 3×3 card fit
on the 13 colour patches common to all three ROIs (the cloud step), plus luma SSIM vs the
RAW render of the same ROI. Bar: today's production pjpg (q80) on the same 13 patches.

Also writes the a*/b* of the grey patches and the card paper, uncorrected and card-corrected,
for pjpg / RAW / nrjxl (the "why do pjpg whites look right" question).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from compression_study import rois
from compression_study.metrics import ssim
from compression_study.methods import raw_planes as rp
from compression_study.presets import preset_study as S
from nereus_camera_test_rig.color.metrics import delta_e2000, linear_to_lab

ROIS = {"LARGE": (1152, 648, 2304, 1296), "MEDIUM": S.TODAY, "SMALL": (2172, 1000, 800, 450)}
BUDGETS = (60, 120, 176, 250, 300, 500)
MSG_B = 288
GREYS = ("gray_light", "gray_mid", "gray_dark")


def srgb_oetf(lin: np.ndarray) -> np.ndarray:
    x = np.clip(lin, 0, 1)
    return (np.where(x <= 0.0031308, 12.92 * x, 1.055 * x ** (1 / 2.4) - 0.055) * 255
            + 0.5).astype(np.uint8)


def card_fit(means: dict, truth: dict, ids) -> tuple[np.ndarray, float]:
    """3×3 least squares from this image's patch means to the card truth, mean ΔE00."""
    A = np.array([means[k] for k in ids])
    T = np.array([truth[k] for k in ids])
    M, *_ = np.linalg.lstsq(A, T, rcond=None)
    de = np.mean([delta_e2000(linear_to_lab(np.clip(p, 0, None)), linear_to_lab(t))
                  for p, t in zip(A @ M, T)])
    return M, float(de)


def ab(lin_rgb) -> tuple[float, float]:
    lab = linear_to_lab(np.clip(np.asarray(lin_rgb, float), 0, None))
    return round(float(lab[1]), 1), round(float(lab[2]), 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--prod", required=True)
    ap.add_argument("--out", default=str(S.REPO / "compression_study/presets/runs"))
    args = ap.parse_args(argv)
    rc, rc_jpeg = S.load_prod(Path(args.prod))
    from nereus_camera_test_rig.color.card import load_card
    from nereus_camera_test_rig.color.metrics import srgb8_to_linear
    card = load_card(rois.CARD)
    truth = {p.id: srgb8_to_linear(np.asarray(p.truth, float)) for p in card.patches}
    out = Path(args.out) / "s28_roi_budget_20261002"
    (out / "img").mkdir(parents=True, exist_ok=True)

    data = Path(args.data) / "cool_imx708"
    dng, jpg = data / "stop_-1_r0.dng", data / "stop_-1_r0.jpg"
    meta = json.loads((data / "stop_-1_r0.json").read_text())
    colour = rc.colour_params(meta)
    full = rc.read_dng_crop(str(dng), (0, 0, 4608, 2592))
    mos, cfa, black, white = full["mosaic"], full["cfa"], full["black"], full["white"]
    roi = rois.load(S.REPO / "compression_study/config/card_rois.yaml")["imx708_cool"]
    ids = S.common_patches(roi, list(ROIS.values()))
    assert len(ids) == 13 and all(i in truth for i in ids), ids

    # one white balance for every render: grey 128 of the original (as the MEDIUM sheet)
    gq = np.asarray(roi["patches"]["gray_mid"]["quad"]) * 2
    gx0, gy0 = np.floor(gq.min(0)).astype(int)
    gx1, gy1 = np.ceil(gq.max(0)).astype(int)
    g = S.render_linear(mos, black, white, cfa, (1, 1, 1),
                        (gx0, gy0, gx1 - gx0, gy1 - gy0)).reshape(-1, 3).mean(0)
    wb = g[1] / g

    # card paper: the brightest unclipped card pixels inside today's crop (mask from the RAW)
    ref_today = S.render_linear(mos, black, white, cfa, wb, S.TODAY)
    cb = np.asarray(roi["card_box"]) * 2 - [S.TODAY[0], S.TODAY[1]] * 2
    x0, y0 = max(cb[0], 0), max(cb[1], 0)
    x1, y1 = min(cb[2], S.TODAY[2]), min(cb[3], S.TODAY[3])
    lum = ref_today @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    in_card = np.zeros(lum.shape, bool)
    in_card[y0:y1, x0:x1] = True
    lo, hi = np.percentile(lum[in_card], [93, 99])
    paper = in_card & (lum >= lo) & (lum <= hi) & (ref_today.max(-1) < 0.98)

    def chroma_rows(name, lin_today, M):
        """a*/b* of greys + paper on a TODAY-box linear image, uncorrected and with M."""
        m = S.patch_means(lin_today, roi, GREYS, S.TODAY)
        m["paper"] = lin_today[paper].mean(0)
        return [{"image": name, "state": st, **{k: ab(v if M is None else v @ M)
                                                for k, v in m.items()}}
                for st, M in (("uncorrected", None), ("card-corrected", M))]

    # ---- today's pjpg (the bar)
    enc = S.pjpg_today(rc_jpeg, jpg)
    pj = Image.open(io.BytesIO(enc["jpeg_data"])).convert("RGB")
    pj_up = np.asarray(pj.resize(S.TODAY[2:], Image.Resampling.LANCZOS))
    pj_lin = S.from_srgb8(pj_up)
    pj_M, bar = card_fit(S.patch_means(pj_lin, roi, ids, S.TODAY), truth, ids)
    pj_ssim = ssim(S.luma(S.to_srgb8(ref_today)),
                   S.luma(S.to_srgb8(S.tone_match(pj_lin, ref_today))))
    print(f"bar: pjpg q{enc['quality']} {enc['jpeg_bytes']} B {enc['message_count']} msgs "
          f"ΔE {bar:.2f} SSIM {pj_ssim:.3f}", flush=True)

    rows, keep = [], {}
    for rname, box in ROIS.items():
        x, y, w, h = box
        crop = rc.read_dng_crop(str(dng), box)
        codes = rc.code_planes(crop)
        ref_lin = S.render_linear(mos, black, white, cfa, wb, box)
        ref8 = S.to_srgb8(ref_lin)
        ref_M, ref_de = card_fit(S.patch_means(ref_lin, roi, ids, box), truth, ids)
        keep[(rname, "raw")] = (ref_lin, ref_M)
        rows.append({"roi": rname, "budget_msgs": "RAW", "distance": "", "bytes": w * h * 10 // 8,
                     "messages": rc.message_count(w * h * 10 // 8, S.CHUNK),
                     "de_truth": round(ref_de, 2), "ssim": 1.0, "verdict": "reference"})
        cache: dict = {}
        with tempfile.TemporaryDirectory(prefix="s28r_") as td:
            make = lambda d: S.nrjxl_blob(rc, crop, codes, colour, d, "native", box,  # noqa: E731
                                          (4608, 2592), Path(td))
            for msgs in BUDGETS:
                d, blob, ok = S.search_distance(make, msgs * MSG_B, cache)
                n_msgs = rc.message_count(len(blob), S.CHUNK)
                ok = ok and n_msgs <= msgs
                row = {"roi": rname, "budget_msgs": msgs, "distance": d, "bytes": len(blob),
                       "messages": n_msgs}
                if not ok:
                    rows.append({**row, "de_truth": "", "ssim": "", "verdict": "does not fit"})
                    print(f"{rname:6s} {msgs:3d} msgs: does not fit (d=15 → {len(blob)} B)")
                    continue
                canvas = np.zeros((2592, 4608))
                canvas[y:y + h, x:x + w] = rp.decode(blob)
                lin = S.render_linear(canvas, black, white, cfa, wb, box)
                M, de = card_fit(S.patch_means(lin, roi, ids, box), truth, ids)
                s = ssim(S.luma(ref8), S.luma(S.to_srgb8(lin)))
                v = "PASS" if de < bar else "FAIL"
                rows.append({**row, "de_truth": round(de, 2), "ssim": round(s, 3), "verdict": v})
                keep[(rname, msgs)] = (lin, M)
                print(f"{rname:6s} {msgs:3d} msgs: d={d:6.3f} {len(blob):6d} B {n_msgs:3d} msgs "
                      f"ΔE {de:.2f} (bar {bar:.2f}, RAW {ref_de:.2f}) SSIM {s:.3f} → {v}",
                      flush=True)

    # ---- answer: largest passing ROI, at its fewest messages
    pick = None
    for rname in ROIS:
        ok = [r for r in rows if r["roi"] == rname and r["verdict"] == "PASS"]
        if ok:
            pick = (rname, min(r["budget_msgs"] for r in ok))
            break
    print("answer:", pick)

    with open(out / "table.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[1].keys()))
        wr.writeheader()
        wr.writerows(rows)

    # ---- whites: MEDIUM at today's budget (the sheet Nick saw) + the picked cell
    med_lin, med_M = keep[("MEDIUM", 176)]
    chroma = (chroma_rows("pjpg q80", pj_lin, pj_M)
              + chroma_rows("RAW", ref_today, keep[("MEDIUM", "raw")][1])
              + chroma_rows("nrjxl 176 msgs", med_lin, med_M))
    (out / "chroma.json").write_text(json.dumps(chroma, indent=1))
    for r in chroma:
        print(r)

    # ---- sheet images for the picked cell: uncorrected + card-corrected, RAW / nrjxl / pjpg
    if pick:
        rname, msgs = pick
        lin, M = keep[(rname, msgs)]
        rlin, rM = keep[(rname, "raw")]
        imgs = {"raw_unc": S.to_srgb8(rlin), "raw_cc": srgb_oetf(rlin @ rM),
                "nrjxl_unc": S.to_srgb8(lin), "nrjxl_cc": srgb_oetf(lin @ M),
                "pjpg_unc": pj_up, "pjpg_cc": srgb_oetf(pj_lin @ pj_M)}
        for k, im in imgs.items():
            Image.fromarray(im).save(out / "img" / f"{k}.webp", "WEBP", lossless=True)
    summary = {"bar_de": round(bar, 2), "pjpg": {"quality": enc["quality"],
               "bytes": enc["jpeg_bytes"], "messages": enc["message_count"],
               "ssim": round(pj_ssim, 3)}, "patches": ids, "pick": pick,
               "rois": {k: list(v) for k, v in ROIS.items()}, "budgets": BUDGETS,
               "effort": S.EFFORT, "dng": str(dng.name), "lamp": "cool"}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
