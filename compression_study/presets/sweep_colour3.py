"""Three-camera colour detail for an exposure sweep with repeats (Nick via EM, 2026-10-05).

    python -m compression_study.presets.sweep_colour3 <experiment> --prod <BM_Devel_Pi> \
        --tuning imx708_wide.json --out <dir>

Per camera (IMX708, N6, AE3):

* card location: ``find_rois`` (3+ tags) or, on the N6 whose left tags do not decode (soft left
  side of its lens), a homography from the 8 corners of tags 1 and 3, keeping only the
  right-hand colour patches (the patches nearest the two tags); an overlay PNG shows every
  sampled patch box so the placement can be checked by eye;
* every frame: card level (grey 128 green, or the mean green of the usable colour patches on the
  N6), card-area and ROI clipping per channel, edge-acutance sharpness on the card area;
* flicker: per shutter step, the card level and R/G, B/G spread over the repeats (CV %);
* the best frame (Mac re-pick: card area unclipped incl. the white patch, not too dark,
  sharpness within 10 % of the sharpest, longest shutter) with the full colour detail:
  per-patch ΔE2000 vs the card truth (uncorrected and card fit, with the patch count), grey
  a*/b*, white level, CCT (IMX708 only: the tuning file's AWB curve), red SNR, black/white
  (flare), clipping.
Uncorrected: IMX708 = its RAW × the frame's AWB gains → its colour matrix → sRGB; OpenMV boards
have no ISP gains or matrix in the RAW, so uncorrected = white balance on grey 128 only (camera
RGB shown as sRGB) — and on the N6 (no grey usable) there is no uncorrected column.
CAVEAT on every ΔE: the card truth was recorded as IMX708 camera RGB under the earlier single
lamp (2026-09-28), not a spectro measurement.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

from compression_study.presets import sweep_colour as SC  # noqa: E402
from compression_study.rois import _inner, _map, find_rois  # noqa: E402
from nereus_camera_test_rig.color.exposure_sweep import _edge_acutance, pick  # noqa: E402
from nereus_camera_test_rig.color.patches import mosaic_to_binned  # noqa: E402
from nereus_camera_test_rig.color.raw_io import (  # noqa: E402
    bin2x2,
    demosaic_bilinear,
    normalize,
    read_dng,
    read_openmv_bayer,
)

CAVEAT = ("Card truth caveat: the truth values were recorded as IMX708 camera RGB under the "
          "earlier single lamp (2026-09-28), not measured with a spectrophotometer; ΔE includes "
          "that reference's own error.")
DET = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11),
                              cv2.aruco.DetectorParameters())
ROIS = {"imx708": (1504, 846, 1600, 900), "openmv_n6": (480, 260, 500, 380),
        "openmv_ae3": (500, 250, 460, 360)}


def read(path: Path):
    return read_dng(path) if path.suffix == ".dng" else read_openmv_bayer(path)


def two_tag_rois(fr, ids=(1, 3)) -> dict:
    """Card patches from the 8 corners of two decoded tags; only patches right of the card's
    centre (nearest tags 1 and 3) are kept."""
    lin, _, cfa = normalize(fr)
    g = demosaic_bilinear(lin, cfa)[..., 1]
    g8 = (np.clip(g / max(np.percentile(g, 99.5), 1e-3), 0, 1) * 255).astype(np.uint8)
    co, found, _ = DET.detectMarkers(g8)
    if found is None:
        raise ValueError("no tags")
    corners = {int(k): q.reshape(4, 2) for k, q in zip(found.ravel(), co)}
    if not all(i in corners for i in ids):
        raise ValueError(f"tags {ids} not both decoded: {sorted(corners)}")
    card = SC.CARD
    src, dst = [], []
    for i in ids:
        t = card.tags[i]
        cx, cy = t.center
        ex, ey = t.edge
        src += [(cx - ex / 2, cy - ey / 2), (cx + ex / 2, cy - ey / 2),
                (cx + ex / 2, cy + ey / 2), (cx - ex / 2, cy + ey / 2)]
        dst += [tuple(p) for p in corners[i]]
    H, _ = cv2.findHomography(np.array(src, np.float64), np.array(dst, np.float64))
    Hb = mosaic_to_binned(None) @ H
    mid = card.canonical_w / 2
    patches = {p.id: {"group": p.group, "quad": _map(Hb, _inner(p.box))}
               for p in card.patches if p.box.x > mid}
    q = cv2.perspectiveTransform(np.array([[[0, 0], [card.canonical_w, 0],
                                            [card.canonical_w, card.canonical_h],
                                            [0, card.canonical_h]]], np.float64), Hb)[0]
    return {"patches": patches, "tags_found": sorted(corners), "method": "2-tag homography (1, 3)",
            "card_box": [int(q[:, 0].min()), int(q[:, 1].min()), int(np.ceil(q[:, 0].max())),
                         int(np.ceil(q[:, 1].max()))]}


def locate(paths: list[Path]) -> tuple[dict, str]:
    for p in paths:  # brightest-first order is given by the caller
        try:
            return {**find_rois(read(p)), "method": "find_rois (3+ tags)"}, p.name
        except ValueError:
            continue
    for p in paths:
        try:
            return two_tag_rois(read(p)), p.name
        except ValueError:
            continue
    raise ValueError("card not located on any frame")


def stats_for(fr, rois, ids):
    lin, sat, cfa = normalize(fr)
    binned, clip = bin2x2(lin, cfa, sat)
    return binned, clip, SC.patch_stats(binned, clip, rois, ids)


def card_level(st: dict) -> tuple[float, list]:
    if "gray_mid" in st:
        v = st["gray_mid"]["mean"]
    else:
        v = np.mean([st[k]["mean"] for k in st], 0)
    return float(v[1]), [float(v[0] / v[1]), float(v[2] / v[1])]


def overlay(fr, rois, path: Path):
    lin, _, cfa = normalize(fr)
    rgb = demosaic_bilinear(lin, cfa)
    g = np.median(rgb.reshape(-1, 3), 0)
    disp = (np.clip(rgb * (g[1] / np.maximum(g, 1e-4)) / max(np.percentile(rgb[..., 1], 99.5), 1e-3),
                    0, 1) ** (1 / 2.2) * 255).astype(np.uint8).copy()
    for pid, p in rois["patches"].items():
        q = np.round(np.asarray(p["quad"]) * 2).astype(np.int32)
        cv2.polylines(disp, [q], True, (255, 0, 255) if p["group"] != "chart" else (0, 200, 255),
                      1, cv2.LINE_AA)
    x0, y0, x1, y1 = (np.asarray(rois["card_box"]) * 2).astype(int)
    pad = 40
    crop = disp[max(y0 - pad, 0):y1 + pad, max(x0 - pad, 0):x1 + pad]
    Image.fromarray(disp).save(path)
    Image.fromarray(crop).save(path.with_name(path.stem + "_crop.png"))


def colour_block(st: dict, meta: dict, cam: str, ct_curve) -> dict:
    """Uncorrected + card-fit ΔE and neutrality on the patches available."""
    use = [k for k in SC.CARD_IDS if k in st and st[k]["clip"].max() <= SC.CLIP_PATCH
           and k != "gray_black"]
    excluded = {k: ("clipped" if k in st and st[k]["clip"].max() > SC.CLIP_PATCH else
                    "black: L*-only reference" if k == "gray_black" else
                    "not sampled (left side, N6 lens soft)" if cam == "openmv_n6" else
                    "not sampled") for k in SC.CARD_IDS if k not in use}
    cam_rgb = {k: st[k]["mean"] for k in use}
    out: dict = {"patches_used": use, "n": len(use), "patches_excluded": excluded}
    y = lambda v: float(v @ [0.2126, 0.7152, 0.0722])  # noqa: E731
    unc = None
    if cam == "imx708" and meta.get("ColourGains") and meta.get("ColourCorrectionMatrix"):
        g = np.array([meta["ColourGains"][0], 1.0, meta["ColourGains"][1]])
        M = np.asarray(meta["ColourCorrectionMatrix"], float).reshape(3, 3)
        unc = {k: M @ (cam_rgb[k] * g) for k in use}
        out["uncorrected_kind"] = "camera colour: AWB gains + colour matrix (rpicam metadata)"
    elif "gray_mid" in cam_rgb:
        wb = cam_rgb["gray_mid"][1] / np.maximum(cam_rgb["gray_mid"], 1e-9)
        unc = {k: cam_rgb[k] * wb for k in use}
        out["uncorrected_kind"] = "WB on grey 128 only (no colour matrix in the OpenMV RAW)"
    if unc is not None and "gray_mid" in unc:
        s = y(SC.TRUTH["gray_mid"]) / max(y(unc["gray_mid"]), 1e-9)
        unc = {k: v * s for k, v in unc.items()}
        t = SC.de_table(unc, SC.TRUTH, use)
        out["uncorrected"] = {"de": {k: round(v, 2) for k, v in t.items()}, **SC.summary(t),
                              "grey_ab": {k: SC.ab(unc[k]) for k in SC.GREYS if k in unc}}
    A = np.array([cam_rgb[k] for k in use])
    T = np.array([SC.TRUTH[k] for k in use])
    Mfit, *_ = np.linalg.lstsq(A, T, rcond=None)
    t = SC.de_table({k: cam_rgb[k] @ Mfit for k in use}, SC.TRUTH, use)
    out["card_fit"] = {"de": {k: round(v, 2) for k, v in t.items()}, **SC.summary(t),
                       "grey_ab": {k: SC.ab(cam_rgb[k] @ Mfit) for k in SC.GREYS if k in cam_rgb},
                       "note": f"in-sample 3x3 fit on {len(use)} patches"}
    if cam == "imx708":
        out["cct"] = SC.cct_from_greys(st, ct_curve)
    else:
        greys = [k for k in ("gray_light", "gray_mid", "gray_dark") if k in use]
        if greys:
            v = np.mean([st[k]["mean"] for k in greys], 0)
            out["cct"] = {"r_over_g": round(float(v[0] / v[1]), 4),
                          "b_over_g": round(float(v[2] / v[1]), 4), "cct_k": None,
                          "note": "no AWB / CCT model for the PAG7936 RAW (OQ-21): ratios only"}
    flat = "gray_mid" if "gray_mid" in st else ("cream" if "cream" in st else None)
    if flat:
        out["red_snr"] = {"patch": flat, "snr": round(float(st[flat]["mean"][0]
                                                             / max(st[flat]["std"][0], 1e-9)), 1),
                          "n_px": st[flat]["n"]}
    if "gray_black" in st and "gray_white" in st:
        out["black_patch"] = {"black_over_white_g": round(float(
            st["gray_black"]["mean"][1] / max(st["gray_white"]["mean"][1], 1e-9)), 4),
            "truth": round(float(SC.TRUTH["gray_black"][1] / SC.TRUTH["gray_white"][1]), 4)}
    if "gray_white" in st:
        out["white_level"] = [round(float(v), 3) for v in st["gray_white"]["mean"]]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("experiment")
    ap.add_argument("--prod", required=True)
    ap.add_argument("--tuning", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    exp, out = Path(a.experiment), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tuning = json.loads(Path(a.tuning).read_text())
    ct_curve = next((x["rpi.awb"]["ct_curve"] for x in tuning["algorithms"] if "rpi.awb" in x),
                    None)
    rec = json.loads((exp / "experiment.json").read_text())
    res: dict = {"experiment": rec["experiment_id"], "notes": rec.get("operator_notes"),
                 "lights": SC.LIGHT_NOTE, "caveat": CAVEAT, "cameras": {}}
    for cam in ("imx708", "openmv_n6", "openmv_ae3"):
        sweep = rec["exposure_sweeps"][cam]
        caps = [c for c in sweep["captures"] if c["status"] == "completed"]
        paths = [exp / "captures" / cam / c["file"] for c in caps]
        shutters = [c["shutter_us"] for c in caps]
        order = [p for _, p in sorted(zip(shutters, paths), key=lambda t: -t[0])]
        rois, located_on = locate(order)
        ids = list(rois["patches"])
        x0, y0, x1, y1 = rois["card_box"]
        rx, ry, rw, rh = ROIS[cam]
        frames = []
        for p, sh, c in zip(paths, shutters, caps):
            fr = read(p)
            binned, clip, st = stats_for(fr, rois, ids)
            lvl, ratios = card_level(st)
            frames.append({
                "file": p.name, "shutter_us": sh, "card_level_g": round(lvl, 4),
                "max_patch_g": round(float(max(v["mean"][1] for v in st.values())), 4),
                "rg_bg": [round(v, 4) for v in ratios],
                "card_clip_pct": [round(float(clip[y0:y1, x0:x1, k].mean() * 100), 3)
                                  for k in range(3)],
                "roi_clip_pct": [round(float(clip[ry // 2:(ry + rh) // 2, rx // 2:(rx + rw) // 2,
                                                  k].mean() * 100), 3) for k in range(3)],
                "white_clipped": bool("gray_white" in st and st["gray_white"]["clip"].max()
                                      > SC.CLIP_PATCH),
                "sharpness_card": _edge_acutance(binned[y0:y1, x0:x1, 1]),
                "readback": c.get("readback")})
        # flicker per shutter step over the repeats, on the brightest card patch (white, or
        # cream on the N6): judged only where it is >= 3 % of full scale (8 DN on 8-bit) —
        # below that, quantisation, not the light, sets the spread
        flick = {}
        for sh in sorted(set(shutters)):
            fs = [f for f in frames if f["shutter_us"] == sh]
            lv = np.array([f["max_patch_g"] for f in fs])
            rg = np.array([f["rg_bg"] for f in fs])
            e = {"n": len(fs), "patch_level": round(float(lv.mean()), 4),
                 "level_cv_pct": round(float(lv.std() / max(lv.mean(), 1e-9) * 100), 2),
                 "rg_cv_pct": round(float(rg[:, 0].std() / max(rg[:, 0].mean(), 1e-9) * 100), 2),
                 "bg_cv_pct": round(float(rg[:, 1].std() / max(rg[:, 1].mean(), 1e-9) * 100), 2)}
            if lv.mean() < 0.03:
                e["flag"] = None
                e["note"] = "too dark to judge (< 3 % of full scale)"
            else:
                e["flag"] = e["level_cv_pct"] > 2.0
            flick[str(sh)] = e
        for_pick = [{"shutter_us": f["shutter_us"],
                     "scores": {"sharpness": {"card": f["sharpness_card"]},
                                "clipped": f["white_clipped"] or max(f["card_clip_pct"]) > 0.5,
                                "level_p995": f["max_patch_g"]}}
                    for f in frames]
        mpick = pick(for_pick)
        best = frames[mpick["index"]] if mpick["index"] is not None else None
        entry = {"located_on": located_on, "method": rois.get("method"),
                 "tags_found": rois.get("tags_found"), "patch_px": None, "frames": frames,
                 "flicker": flick, "mac_pick": mpick, "pi_pick": sweep.get("pick")}
        if best:
            fr = read(exp / "captures" / cam / best["file"])
            _, _, st = stats_for(fr, rois, ids)
            meta = {}
            if cam == "imx708":
                meta = SC.rpicam_meta(exp / "captures" / cam / best["file"])
            entry["best"] = {"file": best["file"], "shutter_us": best["shutter_us"],
                             **colour_block(st, meta, cam, ct_curve),
                             "sharpness_card": best["sharpness_card"],
                             "card_clip_pct": best["card_clip_pct"],
                             "roi_clip_pct": best["roi_clip_pct"]}
            entry["patch_px"] = int(np.median([st[k]["n"] for k in st]))
            overlay(fr, rois, out / f"{cam}_overlay.png")
        res["cameras"][cam] = entry
        b = entry.get("best", {})
        print(cam, entry["method"], "pick", mpick.get("shutter_us"), "| n", b.get("n"),
              "unc", b.get("uncorrected", {}).get("median"), "fit", b.get("card_fit", {}).get("median"),
              "| flicker flags", [k for k, v in flick.items() if v["flag"]],
              "not judged", [k for k, v in flick.items() if v["flag"] is None], flush=True)
    # today's production JPEG (IMX708) on the IMX708's patches
    still = sorted((exp / "captures" / "imx708").glob("imx708_image_*.jpg"))[0]
    order = sorted((exp / "captures" / "imx708").glob("imx708_raw_sweep*.dng"))
    r708, _ = locate(order[::-1])
    res["production_jpeg"] = SC.analyse_jpeg_pjpg(still, r708, Path(a.prod))
    (out / "colour3.json").write_text(json.dumps(res, indent=1, default=float))
    print("pjpg", res["production_jpeg"]["uncorrected"]["median"], "n",
          res["production_jpeg"]["uncorrected"]["n"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
