"""ROI-size sweep at a 180-message budget on fresh nereus002 frames (Nick via EM, 2026-10-03).

    python -m compression_study.presets.roi_sweep --run <runs/sweep_…> --prod <BM_Devel_Pi> \
        [--frames 0 1 2]

Card at ~1.5 m. ROIs at native density, centred on the card + checker, from 800×450 up to the
full 4608×2592. Per ROI × frame: the lowest cjxl distance (production encoder, bm #120, e5,
range 0.1–15) whose sealed container fits 180 messages (288 B each), then vs the RAW render
of the same frame: SSIM over the whole ROI and over the common core (the 800×450 box, which
every ROI and today's pjpg contain), ΔE00 vs the V1 card truth after a per-image 3×3 card fit
on the usable card colour patches, ΔE00 vs RAW on the same patches, tag edge acutance. Today's
production pjpg (fixed crop 1504,846,1600,900 → 1000×562, ladder 90→9, cap 195) is the bar.

Writes <run>/sweep.csv, <run>/pjpg.csv, <run>/patches.json and, for frame 0, the slider crops
in <run>/slides/ (card + one texture window per ROI, both sides rendered by one function and
the RAW's own card matrix).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from compression_study.metrics import ssim
from compression_study.methods import raw_planes as rp
from compression_study.presets import preset_study as S
from nereus_camera_test_rig.color.metrics import delta_e2000, linear_to_lab

MSGS = 180
TARGET = MSGS * 288
CENTRE = (2344, 1304)  # card + checker union centre, native px (2026-10-03 frames)
SIZES = [(800, 450), (1200, 676), (1600, 900), (2000, 1124), (2304, 1296), (3072, 1728),
         (4608, 2592)]
CARD_VIEW = (2080, 1130, 540, 350)  # slider window on the card + checker, native px
TEX_WIN = (480, 270)


def roi_box(w: int, h: int) -> tuple[int, int, int, int]:
    x = min(max(CENTRE[0] - w // 2, 0), 4608 - w) // 2 * 2
    y = min(max(CENTRE[1] - h // 2, 0), 2592 - h) // 2 * 2
    return x, y, w, h


CORE = roi_box(800, 450)


def show(lin: np.ndarray, M: np.ndarray) -> np.ndarray:
    """Display render: white-balanced linear clipped at the sensor's white (so clipped glare
    stays white instead of turning magenta), then the card matrix, then sRGB."""
    return srgb_oetf(np.minimum(lin, 1.0) @ M)


def srgb_oetf(lin: np.ndarray) -> np.ndarray:
    x = np.clip(lin, 0, 1)
    return (np.where(x <= 0.0031308, 12.92 * x, 1.055 * x ** (1 / 2.4) - 0.055) * 255
            + 0.5).astype(np.uint8)


def sub(img: np.ndarray, outer, inner) -> np.ndarray:
    x, y = inner[0] - outer[0], inner[1] - outer[1]
    return img[y:y + inner[3], x:x + inner[2]]


def card_fit(means: dict, truth: dict, ids) -> tuple[np.ndarray, float]:
    A = np.array([means[k] for k in ids])
    T = np.array([truth[k] for k in ids])
    M, *_ = np.linalg.lstsq(A, T, rcond=None)
    de = np.mean([delta_e2000(linear_to_lab(np.clip(p, 0, None)), linear_to_lab(t))
                  for p, t in zip(A @ M, T)])
    return M, float(de)


def usable_patches(ref_lin: np.ndarray, roi: dict, ids, box) -> tuple[list, dict]:
    """Card colour patches whose inner box is ≥ 60 native px and free of glare (within-patch
    luma CV ≤ 8 %, no pixel above 0.95). Returns (ids, per-patch stats)."""
    x0, y0, w, h = box
    keep, stats = [], {}
    for pid in ids:
        q = np.asarray(roi["patches"][pid]["quad"]) * 2 - [x0, y0]
        m = np.zeros((h, w), np.uint8)
        cv2.fillPoly(m, [np.round(q).astype(np.int32)], 1)
        px = ref_lin[m.astype(bool)]
        lum = px @ np.array([0.2126, 0.7152, 0.0722])
        cv = float(lum.std() / max(lum.mean(), 1e-6))
        st = {"px": int(len(px)), "cv": round(cv, 3), "max": round(float(px.max()), 3)}
        st["usable"] = bool(len(px) >= 60 and cv <= 0.08 and px.max() < 0.95)
        stats[pid] = st
        if st["usable"]:
            keep.append(pid)
    return keep, stats


def texture_window(ref8: np.ndarray, box, avoid) -> tuple[int, int, int, int]:
    """The most detailed TEX_WIN window in the ROI that does not overlap ``avoid`` (chosen on
    the RAW render, before any codec result is looked at)."""
    g = S.luma(ref8)
    e = np.hypot(*np.gradient(g))
    ii = cv2.integral(e.astype(np.float64))
    tw, th = TEX_WIN
    best, arg = -1.0, None
    for y in range(0, ref8.shape[0] - th + 1, 30):
        for x in range(0, ref8.shape[1] - tw + 1, 30):
            X, Y = box[0] + x, box[1] + y
            if avoid and (X < avoid[0] + avoid[2] and X + tw > avoid[0]
                          and Y < avoid[1] + avoid[3] and Y + th > avoid[1]):
                continue
            s = ii[y + th, x + tw] - ii[y, x + tw] - ii[y + th, x] + ii[y, x]
            if s > best:
                best, arg = s, (X // 2 * 2, Y // 2 * 2, tw, th)
    if arg is None:  # ROI too small to leave the card: most detailed window anywhere
        return texture_window(ref8, box, None)
    return arg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--prod", required=True)
    ap.add_argument("--frames", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--suffix", default="", help="output csv suffix (re-render runs)")
    args = ap.parse_args(argv)
    run = Path(args.run)
    rc, rc_jpeg = S.load_prod(Path(args.prod))
    from compression_study import rois
    from nereus_camera_test_rig.color.card import load_card
    from nereus_camera_test_rig.color.metrics import srgb8_to_linear
    card = load_card(rois.CARD)
    truth = {p.id: srgb8_to_linear(np.asarray(p.truth, float)) for p in card.patches}
    roi = json.loads((run / "rois_r0.json").read_text())
    colour_ids = [p.id for p in card.patches if not p.id.startswith("gray")]
    slides = run / "slides"
    slides.mkdir(exist_ok=True)
    rows, prow = [], []
    for fi in args.frames:
        stem = run / "cap" / f"stop_+0_r{fi}"
        meta = json.loads(stem.with_suffix(".json").read_text())
        colour = rc.colour_params(meta)
        full = rc.read_dng_crop(str(stem.with_suffix(".dng")), (0, 0, 4608, 2592))
        mos, cfa, black, white = full["mosaic"], full["cfa"], full["black"], full["white"]
        gr, gb = meta["ColourGains"]
        wb = (gr, 1.0, gb)  # the locked camera WB gains (the grey patches sit in glare)

        core_ref = S.render_linear(mos, black, white, cfa, wb, CORE)
        ids, pstats = usable_patches(core_ref, roi, colour_ids, CORE)
        if fi == args.frames[0]:
            (run / "patches.json").write_text(json.dumps({"usable": ids, "stats": pstats},
                                                         indent=1))
            print(f"usable card colour patches: {len(ids)} of {len(colour_ids)} → {ids}")
        core_ref8 = S.to_srgb8(core_ref)
        ref_m = S.patch_means(core_ref, roi, ids, CORE)
        raw_M, raw_de = card_fit(ref_m, truth, ids)

        # today's production pjpg
        enc = S.pjpg_today(rc_jpeg, stem.with_suffix(".jpg"))
        pj = Image.open(io.BytesIO(enc["jpeg_data"])).convert("RGB")
        pj_up = np.asarray(pj.resize(S.TODAY[2:], Image.Resampling.LANCZOS))
        pj_lin = S.from_srgb8(pj_up)
        today_ref = S.render_linear(mos, black, white, cfa, wb, S.TODAY)
        pj_tm = S.tone_match(pj_lin, today_ref)
        pj_core_tm = sub(pj_tm, S.TODAY, CORE)
        pj_M, pj_de = card_fit(S.patch_means(sub(pj_lin, S.TODAY, CORE), roi, ids, CORE), truth,
                               ids)
        base = {"ssim": ssim(S.luma(core_ref8), S.luma(S.to_srgb8(pj_core_tm))),
                "patch_de": S.de_vs_raw(ref_m, S.patch_means(pj_core_tm, roi, ids, CORE)),
                "de_truth": pj_de, **S.tag_stats(core_ref8, S.to_srgb8(pj_core_tm))}
        base["ssim_roi"] = ssim(S.luma(S.to_srgb8(today_ref)), S.luma(S.to_srgb8(pj_tm)))
        prow.append({"frame": fi, "quality": enc["quality"], "bytes": enc["jpeg_bytes"],
                     "messages": enc["message_count"], "raw_de_truth": round(raw_de, 2),
                     **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in base.items()}})
        print(f"[r{fi}] pjpg q{enc['quality']} {enc['jpeg_bytes']} B {enc['message_count']} msgs "
              f"core SSIM {base['ssim']:.3f} ΔE {pj_de:.2f} (RAW {raw_de:.2f}) "
              f"acu {S.fm(base['acutance_rel'])} tags {base['tags_found']}/{base['tags_ref']}",
              flush=True)
        if fi == args.frames[0]:
            cv_ = CARD_VIEW
            Image.fromarray(show(sub(pj_lin, S.TODAY, cv_), pj_M)).save(
                slides / "pjpg_card.png")
            Image.fromarray(show(sub(today_ref, S.TODAY, cv_), raw_M)).save(
                slides / "pjpg_card_ref.png")

        for w, h in SIZES:
            box = roi_box(w, h)
            name = f"{w}x{h}"
            x, y = box[:2]
            crop = rc.read_dng_crop(str(stem.with_suffix(".dng")), box)
            codes = rc.code_planes(crop)
            ref_lin = S.render_linear(mos, black, white, cfa, wb, box)
            ref8 = S.to_srgb8(ref_lin)
            cache: dict = {}
            t0 = time.perf_counter()
            with tempfile.TemporaryDirectory(prefix="s28s_") as td:
                make = lambda d: S.nrjxl_blob(rc, crop, codes, colour, d, "native", box,  # noqa: E731
                                              (4608, 2592), Path(td))
                d, blob, ok = S.search_distance(make, TARGET, cache)
            n_msgs = rc.message_count(len(blob), S.CHUNK)
            ok = ok and n_msgs <= MSGS
            row = {"frame": fi, "roi": name, "box": list(box), "distance": d,
                   "bytes": len(blob), "messages": n_msgs, "fits": ok,
                   "search_s": round(time.perf_counter() - t0, 1)}
            if not ok:
                rows.append(row)
                print(f"[r{fi}] {name:10s} does not fit 180 msgs at d 15 → {len(blob)} B "
                      f"{n_msgs} msgs", flush=True)
                continue
            dec = rp.decode(blob)
            canvas = np.zeros((2592, 4608))
            canvas[y:y + h, x:x + w] = dec
            lin = S.render_linear(canvas, black, white, cfa, wb, box)
            img8 = S.to_srgb8(lin)
            core = sub(lin, box, CORE)
            core8 = S.to_srgb8(core)
            im = S.patch_means(core, roi, ids, CORE)
            M, de = card_fit(im, truth, ids)
            m = {"ssim": ssim(S.luma(core_ref8), S.luma(core8)),
                 "ssim_roi": ssim(S.luma(ref8), S.luma(img8)),
                 "patch_de": S.de_vs_raw(ref_m, im), "de_truth": de,
                 **S.tag_stats(core_ref8, core8)}
            row.update({k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()})
            row["raw_de_truth"] = round(raw_de, 2)
            row["verdict"] = S.verdict(m, base)
            row["colour"] = ("PASS" if de <= pj_de else
                             "WARN" if de <= pj_de * 1.05 else "FAIL")
            dc, db = abs((m["acutance_rel"] or 1) - 1), abs((base["acutance_rel"] or 1) - 1)
            row["detail"] = ("PASS" if m["ssim"] >= base["ssim"] and dc <= db else
                             "WARN" if m["ssim"] >= base["ssim"] - 0.005 else "FAIL")
            rows.append(row)
            print(f"[r{fi}] {name:10s} d={d:6.3f} {len(blob):6d} B {n_msgs:3d} msgs core SSIM "
                  f"{m['ssim']:.3f}/{base['ssim']:.3f} roi SSIM {m['ssim_roi']:.3f} ΔE "
                  f"{de:.2f}/{pj_de:.2f} (RAW {raw_de:.2f}) vsRAW {S.fm(m['patch_de'])} acu "
                  f"{S.fm(m['acutance_rel'])}/{S.fm(base['acutance_rel'])} → {row['verdict']}",
                  flush=True)
            if fi == args.frames[0]:
                tex = texture_window(ref8, box, (2060, 1110, 580, 400))
                row["texture"] = list(tex)
                for tag, view in (("card", CARD_VIEW), ("tex", tex)):
                    Image.fromarray(show(sub(ref_lin, box, view), raw_M)).save(
                        slides / f"{name}_{tag}_ref.png")
                    Image.fromarray(show(sub(lin, box, view), raw_M)).save(
                        slides / f"{name}_{tag}_nrjxl.png")
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(run / f"sweep{args.suffix}.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=keys)
        wr.writeheader()
        wr.writerows(rows)
    with open(run / f"pjpg{args.suffix}.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(prow[0]))
        wr.writeheader()
        wr.writerows(prow)
    (run / "sweep_meta.json").write_text(json.dumps(
        {"msgs": MSGS, "target_bytes": TARGET, "centre": CENTRE, "core": CORE,
         "card_view": CARD_VIEW, "rois": {f"{w}x{h}": roi_box(w, h) for w, h in SIZES},
         "effort": S.EFFORT, "frames": args.frames}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
