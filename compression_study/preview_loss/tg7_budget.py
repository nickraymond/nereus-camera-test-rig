"""nrjxl crop x budget on Nick's TG-7 RAW set (Channel Islands kelp + card), Nick via EM 2026-10-05.

    NRJXL_BM_DIR=<bm #120 export> python -m compression_study.preview_loss.tg7_budget \
        --tg7 <primary>/data/tg7_channel_islands --corners <S1 locate corners.json> \
        --manual configs/datasets/tg7_channel_islands_manual_corners.json --out tg7_budget.json

"TG-7 raw, resampled to IMX708 geometry, an approximation": for each IMX708 ROI (w x h of the
4608x2592 sensor) the TG-7 crop covers the same FOV fraction (w/4608 of the TG-7 width, 16:9,
centred); each CFA plane is Lanczos-resampled to the IMX708 pixel count (x1.148), the TG-7 CFA
(GRBG) is kept, and levels are normalised to 10 bit (black 64, white 1023). The TG-7's own
sensor noise stays (12-bit, 1.55 um, ISO 100); no IMX708 noise is added; resampling correlates
it slightly. Then the production encoder and the analysis of reef_budget.py, plus Delta E00 on
the V2 card patches inside the crop (patch means, nrjxl / pjpg vs the RAW render).
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from compression_study import common
from compression_study.preview_loss import codec as C
from compression_study.preview_loss import reef_budget as R

IMX = (4608, 2592)
SKIP = {"P9160606", "P9160616", "P9160617"}          # diver torch
OLY = np.array([[420, -164, 0], [-64, 392, -72], [8, -112, 360]], np.float64) / 256
CARD = Path(__file__).resolve().parents[2] / "configs" / "cards" / "nereus_v2.yaml"


def select(tg7: Path) -> list[dict]:
    """Scene-diverse subset, by capture time within each folder (spreads dives and depths)."""
    rows = [r for r in csv.DictReader(open(tg7 / "manifest.csv"))
            if r["has_orf"] == "True" and r["flash_fired"] == "False" and r["stem"] not in SKIP]
    by = {}
    for r in sorted(rows, key=lambda r: r["datetime"]):
        by.setdefault(r["category"], []).append(r)

    def spread(lst, n):
        if len(lst) <= n:
            return lst
        return [lst[round(i * (len(lst) - 1) / (n - 1))] for i in range(n)]
    pick = (spread([r for r in by["0_surface_card"] if r["capture_mode"] == "A_iso100"], 2)
            + spread(by["1_reference_A_iso100"], 16)
            + spread(by["2_underwater_preset"], 4)
            + by["3_scene_card_offcenter"]
            + spread([r for r in by["4_no_card"] if r["capture_mode"] == "A_iso100"], 15))
    return [{"name": r["stem"], "category": r["category"], "depth_m": r["water_depth_m"],
             "path": str(tg7 / "raw" / r["category"] / f"{r['stem']}.orf")} for r in pick]


def map_to_imx(f, roi_wh) -> tuple[dict, tuple, float]:
    """TG-7 RawFrame -> IMX708-geometry 10-bit mosaic for one ROI size. -> (scene, tg7 box, s)."""
    raw, cfa, black = f.active()
    H, W = raw.shape
    rw, rh = roi_wh
    cw = int(round(rw / IMX[0] * W / 2)) * 2
    ch = int(round(cw * rh / rw / 2)) * 2
    x0, y0 = (W - cw) // 2 // 2 * 2, (H - ch) // 2 // 2 * 2
    sub = raw[y0:y0 + ch, x0:x0 + cw].astype(np.float32)
    bl = np.asarray(black, np.float32).reshape(2, 2)
    out = np.empty((rh, rw), np.float32)
    for dy in (0, 1):
        for dx in (0, 1):
            p = (sub[dy::2, dx::2] - bl[dy, dx]) / (f.white_level - bl[dy, dx])
            out[dy::2, dx::2] = cv2.resize(p, (rw // 2, rh // 2), interpolation=cv2.INTER_LANCZOS4)
    dn = np.clip(np.round(np.clip(out, 0, 1) * (1023 - 64) + 64), 0, 1023).astype(np.uint16)
    wb = f.as_shot_wb or (2.0, 1.0, 1.6)
    return ({"mosaic": dn, "cfa": cfa, "black": 64, "white": 1023, "gains": wb, "ccm": OLY},
            (x0, y0, cw, ch), rw / cw)


def card_patches(quad_raw, box, s):
    """V2 colour/grey patch quads (inner 60 %) mapped into the IMX708-geometry crop."""
    from nereus_camera_test_rig.color.card import load_card
    from nereus_camera_test_rig.color.patches import INNER, homography
    card = load_card(CARD)
    Hm = homography(card, np.asarray(quad_raw, float))
    out = {}
    for p in card.patches:
        b = p.box
        cx, cy, hw, hh = b.x + b.w / 2, b.y + b.h / 2, b.w * INNER / 2, b.h * INNER / 2
        q = cv2.perspectiveTransform(np.array([[[cx - hw, cy - hh], [cx + hw, cy - hh],
                                                [cx + hw, cy + hh], [cx - hw, cy + hh]]]), Hm)[0]
        q = (q - [box[0], box[1]]) * s
        if q.min() >= 2 and q[:, 0].max() < box[2] * s - 2 and q[:, 1].max() < box[3] * s - 2 \
                and cv2.contourArea(q.astype(np.float32)) >= 64:
            out[p.id] = q
    return out


def ssim_lo(img8, ref8) -> float:
    """Luma SSIM at today's delivered size (1000x562, area downscale of both)."""
    from compression_study.metrics import ssim
    lo = lambda x: cv2.resize(x, (1000, 562), interpolation=cv2.INTER_AREA)  # noqa: E731
    return round(ssim(R.luma(lo(img8)), R.luma(lo(ref8))), 4)


def patch_de(lin_img, lin_ref, quads) -> float | None:
    from nereus_camera_test_rig.color.metrics import delta_e2000, linear_to_lab
    if len(quads) < 4:
        return None
    de = []
    for q in quads.values():
        m = np.zeros(lin_ref.shape[:2], np.uint8)
        cv2.fillPoly(m, [np.round(q).astype(np.int32)], 1)
        k = m.astype(bool)
        a, b = lin_img[k].mean(0), lin_ref[k].mean(0)
        de.append(float(delta_e2000(linear_to_lab(np.clip(a, 0, None)), linear_to_lab(np.clip(b, 0, None)))))
    return round(float(np.median(de)), 2)


def run(job: dict) -> dict:
    from host_tools.tg7.orf_io import read_orf
    t0 = time.time()
    f = read_orf(job["path"])
    out = {**{k: job[k] for k in ("name", "category", "depth_m")}, "crops": {}}
    for cname, (rw, rh) in R.CROPS.items():
        scene, tbox, s = map_to_imx(f, (rw, rh))
        ref_lin = R.render_lin(scene["mosaic"], scene)
        if cname == "1600x900":
            scale = 0.9 / max(float(np.percentile(ref_lin[..., 1], 99.5)), 1e-6)
            ref8 = R.to8(ref_lin, scale)
            # today's pjpg the production way from the same data: render -> 1000x562 Lanczos ->
            # progressive JPEG on the quality ladder under 195 messages
            pj, pjm = R.pjpg_today(ref8, (0, 0, rw, rh))
            up = np.asarray(Image.fromarray(pj).resize((rw, rh), Image.Resampling.LANCZOS))
            pj_lin = R.tone_match(common.srgb_eotf(up / 255.0), common.srgb_eotf(ref8 / 255.0))
            pj8 = np.round(common.srgb_oetf(np.clip(pj_lin, 0, 1)) * 255).astype(np.uint8)
            quads = card_patches(job["quad"], tbox, s) if job.get("quad") else {}
            quads = quads if len(quads) >= 4 else {}
            pj_lo = np.asarray(Image.fromarray(pj))                # the delivered 1000x562
            out["pjpg"] = {**pjm, **R.metrics(pj8, ref8), "ssim_lo": ssim_lo(
                np.asarray(Image.fromarray(pj_lo).resize((rw, rh), Image.Resampling.LANCZOS)), ref8),
                           "patch_de": patch_de(pj_lin / scale, ref_lin, quads)}
            out["card_patches_in_crop"] = len(quads)
            out["tg7_box"], out["scale"] = list(tbox), round(s, 4)
        else:
            ref8 = R.to8(ref_lin, 0.9 / max(float(np.percentile(ref_lin[..., 1], 99.5)), 1e-6))
            quads = {}
        rows = []
        for d in R.DISTANCES:
            n, mos = R.nrjxl_bytes_and_decode(scene, (0, 0, rw, rh), d)
            lin = R.render_lin(mos, scene)
            sc = 0.9 / max(float(np.percentile(ref_lin[..., 1], 99.5)), 1e-6)
            img8 = R.to8(lin, sc)
            row = {"d": d, "bytes": n, "messages": C.chunks(n), **R.metrics(img8, ref8)}
            if cname == "1600x900":
                row["ssim_lo"] = ssim_lo(img8, ref8)
            if quads:
                row["patch_de"] = patch_de(lin, ref_lin, quads)
            rows.append(row)
        out["crops"][cname] = {"grid": rows}
    out["seconds"] = round(time.time() - t0, 1)
    return out


def summarise(scenes) -> list[dict]:
    out = []
    for s in scenes:
        row = {"name": s["name"], "category": s["category"], "depth_m": s["depth_m"],
               "pjpg": s["pjpg"], "card_patches_in_crop": s.get("card_patches_in_crop", 0)}
        for cname, c in s["crops"].items():
            g = c["grid"]
            cells = {}
            for b in R.BUDGETS:
                d = R.d_for_bytes(g, R.FILL * b * C.MSG_B)
                cells[str(b)] = None if d is None else {
                    "d": round(d, 2), "ssim": round(R._interp_logd(g, "ssim", d), 4),
                    "detail": round(R._interp_logd(g, "detail", d), 3),
                    "patch_de": (round(R._interp_logd(g, "patch_de", d), 2)
                                 if g[0].get("patch_de") is not None else None)}
            row[cname] = {"budgets": cells,
                          "msgs_for_d": {str(d): R.msgs_for_d(g, d) for d in (4.0, 4.5, 5.0, 10.4)}}
            if cname == "1600x900":
                row[cname]["msgs_match_pjpg_ssim"] = R.msgs_for_quality(g, "ssim", s["pjpg"]["ssim"])
                row[cname]["msgs_match_pjpg_detail"] = R.msgs_for_quality(g, "detail", s["pjpg"]["detail"])
                row[cname]["msgs_match_pjpg_ssim_lo"] = R.msgs_for_quality(g, "ssim_lo", s["pjpg"]["ssim_lo"])
                for b in R.BUDGETS:
                    if cells[str(b)]:
                        cells[str(b)]["ssim_lo"] = round(R._interp_logd(g, "ssim_lo", cells[str(b)]["d"]), 4)
        out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tg7", type=Path, required=True)
    ap.add_argument("--corners", type=Path, required=True)
    ap.add_argument("--manual", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--jobs", type=int, default=7)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    auto = json.loads(a.corners.read_text())
    manual = json.loads(a.manual.read_text())
    js = select(a.tg7)[: a.limit]
    for j in js:
        q = (manual.get(j["name"]) or {}).get("quad_raw") or \
            ((auto.get(j["name"]) or {}).get("quad_raw") if (auto.get(j["name"]) or {}).get("located") else None)
        j["quad"] = q
    res = {"selection": [{k: j[k] for k in ("name", "category", "depth_m")} | {"card": bool(j["quad"])}
                         for j in js], "scenes": []}
    print(len(js), "frames:", {c: sum(j["category"] == c for j in js) for c in sorted({j["category"] for j in js})},
          "with card quad:", sum(bool(j["quad"]) for j in js), flush=True)
    with ProcessPoolExecutor(a.jobs) as ex:
        for r in ex.map(run, js):
            res["scenes"].append(r)
            print(r["name"], r["category"], r["seconds"], "s", flush=True)
            a.out.write_text(json.dumps(res))
    res["summary"] = summarise(res["scenes"])
    a.out.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
