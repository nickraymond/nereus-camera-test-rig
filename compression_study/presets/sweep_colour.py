"""Full colour-correctness detail for an exposure-sweep experiment (Nick, 2026-10-05).

    python -m compression_study.presets.sweep_colour <experiment folder> --prod <BM_Devel_Pi> \
        --tuning imx708_wide.json [--s4 <data/s4_20260930/cool_imx708/stop_-1_r0>] --out <dir>

Mac-side (full-resolution card location; the Pi's own sweep scorer works on decimated copies).
Per IMX708 sweep frame, and for the picked frame:

* **ΔE2000 per patch vs the card truth** (V1 card YAML ``truth`` = the card as measured on the
  IMX708 in air, 2026-09-28), on the card's 18 patches minus any clipped patch:
  - *uncorrected* = the camera's own colour, without tone curve: linear camera RGB × the frame's
    AWB ``ColourGains`` → the frame's ``ColourCorrectionMatrix`` → linear sRGB, one exposure scale
    matching grey 128's luminance to the truth (L*-only match, the S1 protocol);
  - *after the card fit* = a 3×3 least-squares matrix from camera RGB to the truth on the same
    patches (in-sample: it flatters, and says so);
* grey-patch neutrality (a*, b*) in both; white-patch level (fraction of full scale);
* scene CCT from the card greys: R/G and B/G of the greys on the camera's own AWB curve
  (``ct_curve`` in the libcamera tuning file), with the matching WB gains, vs nominal 5300 K;
* red SNR on grey 128 (mean / std of the binned pixels in the patch); clipping % per channel
  in the card area, and in the whole frame excluding the light panels (clipped blobs larger
  than 0.2 % of the frame are treated as light sources and masked);
* sharpness (card-area Laplacian energy, noise-corrected, ``color.exposure_sweep``) and a Mac
  re-pick with the same rule on the card area.

Baselines on the same patches: today's production JPEG of the same scene (the auto full-res
JPEG the experiment took first → bm #120 ``rc_jpeg_encoder`` pjpg, 1600×900 → 1000×562 under the
195-message cap), and, optionally, the S4 single-lamp study frame (``--s4``).
Writes ``<out>/colour.json`` and prints a summary.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

from compression_study.rois import find_rois  # noqa: E402
from nereus_camera_test_rig.color.card import load_card  # noqa: E402
from nereus_camera_test_rig.color.exposure_sweep import _laplacian_energy, pick  # noqa: E402
from nereus_camera_test_rig.color.metrics import (  # noqa: E402
    delta_e2000,
    linear_to_lab,
    srgb8_to_linear,
)
from nereus_camera_test_rig.color.raw_io import bin2x2, normalize, read_dng  # noqa: E402

CARD = load_card(REPO / "configs" / "cards" / "nereus_v1.yaml")
TRUTH = {p.id: srgb8_to_linear(np.asarray(p.truth, float)) for p in CARD.patches}
DESIGN = {p.id: srgb8_to_linear(np.asarray(p.design, float)) for p in CARD.patches}
GREYS = ["gray_white", "gray_light", "gray_mid", "gray_dark", "gray_black"]
CARD_IDS = [p.id for p in CARD.patches]
CLIP_PATCH = 0.01  # a patch with > 1 % clipped pixels in any channel is excluded
ROIS = {"MEDIUM": (1504, 846, 1600, 900), "SMALL": (2172, 1000, 800, 450)}  # native px
LIGHT_NOTE = ("2× 5300 K LED panels in frame, excluded from the analysis; possible veiling flare "
              "(Nick, 2026-10-05: the LEDs stay where they are; angling them causes card "
              "reflections)")


def rpicam_meta(dng: Path) -> dict:
    """The rpicam --metadata JSON written next to a DNG (``<stem>.jpg.rpicam.json``)."""
    p = dng.with_name(dng.with_suffix(".jpg").name + ".rpicam.json")
    if not p.exists():
        return {}
    m = json.loads(p.read_text())
    return m[-1] if isinstance(m, list) else m


def patch_stats(binned, clip, rois, ids):
    out = {}
    for pid in ids:
        q = np.round(np.asarray(rois["patches"][pid]["quad"])).astype(np.int32)
        m = np.zeros(binned.shape[:2], np.uint8)
        cv2.fillPoly(m, [q], 1)
        m = m.astype(bool)
        px, cl = binned[m], clip[m]
        out[pid] = {"mean": px.mean(0), "std": px.std(0), "n": int(m.sum()),
                    "clip": cl.mean(0)}
    return out


def de_table(rgb_lin: dict, truth: dict, ids) -> dict:
    return {k: float(delta_e2000(linear_to_lab(np.clip(rgb_lin[k], 0, None)),
                                 linear_to_lab(truth[k]))) for k in ids}


def summary(t: dict) -> dict:
    v = np.array(list(t.values()))
    return {"mean": round(float(v.mean()), 2), "median": round(float(np.median(v)), 2),
            "max": round(float(v.max()), 2), "worst": max(t, key=t.get), "n": len(v)}


def ab(rgb_lin) -> list:
    lab = linear_to_lab(np.clip(np.asarray(rgb_lin, float), 0, None))
    return [round(float(lab[1]), 1), round(float(lab[2]), 1)]


def colour_metrics(stats: dict, gains, ccm) -> dict:
    """Uncorrected (camera WB + CCM, exposure-matched on grey 128) and card-fit ΔE per patch."""
    use = [k for k in CARD_IDS if k in stats and stats[k]["clip"].max() <= CLIP_PATCH
           and k != "gray_black"]
    excluded = {k: ("clipped" if k in stats and stats[k]["clip"].max() > CLIP_PATCH else
                    "black (no stable chroma; L*-only reference)" if k == "gray_black"
                    else "not sampled") for k in CARD_IDS if k not in use}
    cam = {k: stats[k]["mean"] for k in use}
    out: dict = {"patches_used": use, "patches_excluded": excluded}
    if gains is not None and ccm is not None and "gray_mid" in cam:
        g = np.array([gains[0], 1.0, gains[1]])
        M = np.asarray(ccm, float).reshape(3, 3)
        srgb = {k: M @ (cam[k] * g) for k in use}
        y = lambda v: float(v @ [0.2126, 0.7152, 0.0722])  # noqa: E731
        s = y(TRUTH["gray_mid"]) / max(y(srgb["gray_mid"]), 1e-9)
        srgb = {k: v * s for k, v in srgb.items()}
        t = de_table(srgb, TRUTH, use)
        out["uncorrected"] = {"de": {k: round(v, 2) for k, v in t.items()}, **summary(t),
                              "grey_ab": {k: ab(srgb[k]) for k in GREYS if k in srgb}}
    A = np.array([cam[k] for k in use])
    T = np.array([TRUTH[k] for k in use])
    Mfit, *_ = np.linalg.lstsq(A, T, rcond=None)
    fit = {k: cam[k] @ Mfit for k in use}
    t = de_table(fit, TRUTH, use)
    out["card_fit"] = {"de": {k: round(v, 2) for k, v in t.items()}, **summary(t),
                       "grey_ab": {k: ab(fit[k]) for k in GREYS if k in fit},
                       "note": "in-sample 3x3 fit on the same patches (flatters)"}
    return out


def cct_from_greys(stats: dict, ct_curve: list) -> dict:
    greys = [k for k in ("gray_light", "gray_mid", "gray_dark") if k in stats
             and stats[k]["clip"].max() <= CLIP_PATCH]
    if not greys or not ct_curve:
        return {}
    v = np.mean([stats[k]["mean"] for k in greys], 0)
    r, b = v[0] / v[1], v[2] / v[1]
    pts = np.array(ct_curve, float).reshape(-1, 3)
    best = None
    for (c0, r0, b0), (c1, r1, b1) in zip(pts[:-1], pts[1:]):
        d = np.array([r1 - r0, b1 - b0])
        tt = float(np.clip(np.dot([r - r0, b - b0], d) / np.dot(d, d), 0, 1))
        p = np.array([r0, b0]) + tt * d
        dist = float(np.hypot(r - p[0], b - p[1]))
        if best is None or dist < best[0]:
            best = (dist, c0 + tt * (c1 - c0))
    # gains the tuning would use at 5300 K, for comparison
    i = np.searchsorted(pts[:, 0], 5300)
    c0, r0, b0 = pts[i - 1]
    c1, r1, b1 = pts[i]
    w = (5300 - c0) / (c1 - c0)
    r53, b53 = r0 + w * (r1 - r0), b0 + w * (b1 - b0)
    return {"greys": greys, "r_over_g": round(float(r), 4), "b_over_g": round(float(b), 4),
            "cct_k": round(best[1]), "off_curve": round(best[0], 4),
            "wb_gains_card": [round(1 / r, 3), round(1 / b, 3)],
            "wb_gains_tuning_5300k": [round(1 / r53, 3), round(1 / b53, 3)]}


def frame_clipping(binned, clip, card_box) -> dict:
    x0, y0, x1, y1 = card_box
    card = clip[y0:y1, x0:x1]
    any_clip = clip.any(-1).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(any_clip, connectivity=8)
    big = np.zeros(any_clip.shape, bool)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] > 0.002 * any_clip.size:
            big |= lab == i
    big = cv2.dilate(big.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool)
    keep = ~big
    return {"card_area_pct": [round(float(card[..., c].mean() * 100), 3) for c in range(3)],
            "frame_excl_lights_pct": [round(float(clip[..., c][keep].mean() * 100), 3)
                                      for c in range(3)],
            "light_mask_pct": round(float(big.mean() * 100), 2)}


def analyse_dng(dng: Path, rois: dict, ct_curve, meta: dict | None = None) -> dict:
    fr = read_dng(dng)
    lin, sat, cfa = normalize(fr)
    binned, clip = bin2x2(lin, cfa, sat)
    del lin, sat
    stats = patch_stats(binned, clip, rois, CARD_IDS)
    meta = meta if meta is not None else rpicam_meta(dng)
    x0, y0, x1, y1 = rois["card_box"]
    out = {"file": dng.name, "exposure_us": meta.get("ExposureTime"),
           "analogue_gain": meta.get("AnalogueGain"), "digital_gain": meta.get("DigitalGain"),
           "awb_gains": meta.get("ColourGains"), "awb_cct": meta.get("ColourTemperature"),
           "white_level": [round(float(v), 3) for v in stats["gray_white"]["mean"]],
           "white_clipped": bool(stats["gray_white"]["clip"].max() > CLIP_PATCH),
           "red_snr_grey128": round(float(stats["gray_mid"]["mean"][0]
                                          / max(stats["gray_mid"]["std"][0], 1e-9)), 1),
           "patch_px": stats["gray_mid"]["n"],
           "clipping": {**frame_clipping(binned, clip, rois["card_box"]),
                        **{f"{n}_pct": [round(float(clip[y // 2:(y + h) // 2, x // 2:(x + w) // 2,
                                                          c].mean() * 100), 3) for c in range(3)]
                           for n, (x, y, w, h) in ROIS.items()}},
           "black_patch": {
               "level_rgb": [round(float(v), 4) for v in stats["gray_black"]["mean"]],
               "black_over_white_g": round(float(stats["gray_black"]["mean"][1]
                                                 / max(stats["gray_white"]["mean"][1], 1e-9)), 4),
               "white_clipped": bool(stats["gray_white"]["clip"].max() > CLIP_PATCH),
               "truth_black_over_white": round(float(TRUTH["gray_black"][1]
                                                     / TRUTH["gray_white"][1]), 4)},
           "sharpness_card": _laplacian_energy(binned[y0:y1, x0:x1, 1]),
           "cct": cct_from_greys(stats, ct_curve)}
    out.update(colour_metrics(stats, meta.get("ColourGains"), meta.get("ColourCorrectionMatrix")))
    return out


def analyse_jpeg_pjpg(jpg: Path, rois: dict, prod: Path) -> dict:
    """Today's production path: rc_jpeg_encoder.prepare_source(crop 1504,846,1600,900 → 1000 px)
    and the progressive quality ladder under the 195-message cap; patches sampled through the
    RAW quads (same sensor grid), the pjpg upsampled back to 1600×900."""
    sys.path.insert(0, str(prod))
    import rc_jpeg_encoder as rj
    crop = (1504, 846, 1600, 900)
    src = rj.prepare_source(str(jpg), crop, 1000)
    for q in (90, 80, 70, 60, 50, 40, 30, 25, 20, 15, 13, 11, 9):
        enc = rj.encode_progressive(src, q, 384)
        if enc["message_count"] <= 195:
            break
    pj = np.asarray(Image.open(io.BytesIO(enc["jpeg_data"])).convert("RGB").resize(
        crop[2:], Image.Resampling.LANCZOS))
    lin = srgb8_to_linear(pj.astype(float))
    stats = {}
    for pid in CARD_IDS:
        q = np.asarray(rois["patches"][pid]["quad"]) * 2 - [crop[0], crop[1]]
        m = np.zeros(lin.shape[:2], np.uint8)
        cv2.fillPoly(m, [np.round(q).astype(np.int32)], 1)
        m = m.astype(bool)
        px8 = pj[m]
        stats[pid] = {"mean": lin[m].mean(0), "std": lin[m].std(0), "n": int(m.sum()),
                      "clip": (px8 >= 254).mean(0)}
    use = [k for k in CARD_IDS if stats[k]["clip"].max() <= CLIP_PATCH and k != "gray_black"]
    srgb = {k: stats[k]["mean"] for k in use}
    y = lambda v: float(v @ [0.2126, 0.7152, 0.0722])  # noqa: E731
    s = y(TRUTH["gray_mid"]) / max(y(srgb["gray_mid"]), 1e-9) if "gray_mid" in srgb else 1.0
    t = de_table({k: v * s for k, v in srgb.items()}, TRUTH, use)
    A = np.array([srgb[k] for k in use])
    T = np.array([TRUTH[k] for k in use])
    M, *_ = np.linalg.lstsq(A, T, rcond=None)
    tf = de_table({k: srgb[k] @ M for k in use}, TRUTH, use)
    return {"quality": enc["quality"], "bytes": enc["jpeg_bytes"],
            "messages": enc["message_count"], "patches_used": use,
            "patches_excluded": {k: "clipped (8-bit 254+)" for k in CARD_IDS
                                 if k not in use and k != "gray_black"},
            "uncorrected": {"de": {k: round(v, 2) for k, v in t.items()}, **summary(t),
                            "grey_ab": {k: ab(srgb[k] * s) for k in GREYS if k in srgb}},
            "card_fit": {"de": {k: round(v, 2) for k, v in tf.items()}, **summary(tf),
                         "grey_ab": {k: ab(srgb[k] @ M) for k in GREYS if k in srgb}}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("experiment")
    ap.add_argument("--prod", required=True)
    ap.add_argument("--tuning", required=True)
    ap.add_argument("--s4", help="S4 single-lamp frame stem (…/stop_-1_r0) for comparison")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    exp = Path(a.experiment)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tuning = json.loads(Path(a.tuning).read_text())
    ct_curve = next((x["rpi.awb"]["ct_curve"] for x in tuning["algorithms"] if "rpi.awb" in x),
                    None)
    rec = json.loads((exp / "experiment.json").read_text())
    cam_dir = exp / "captures" / "imx708"
    dngs = sorted(cam_dir.glob("imx708_raw_sweep*.dng"))
    sweep = rec["exposure_sweeps"]["imx708"]
    shutters = sweep["shutters_us"]
    rois, located_on = None, None
    for dng, sh in sorted(zip(dngs, shutters), key=lambda t: -t[1]):  # brightest first
        try:
            rois = find_rois(read_dng(dng))
            located_on = dng.name
            break
        except ValueError:
            continue
    if rois is None:
        raise SystemExit("card not located on any IMX708 sweep frame")
    res: dict = {"experiment": rec["experiment_id"], "notes": rec.get("operator_notes"),
                 "lights": LIGHT_NOTE,
                 "card_located_on": located_on, "tags_found": rois.get("tags_found"),
                 "truth_source": CARD.truth_source, "quad_raw": rois["quad_raw"],
                 "card_box_binned": rois["card_box"], "frames": []}
    # distance ESTIMATE: tag-centre spacing (V1 print master 364.9 mm) over its pixel span, with
    # the imx708_wide's nominal 2.75 mm lens on 1.4 um pixels (f = 1964 px), no distortion model
    q = np.asarray(rois["quad_raw"], float)
    span = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    res["distance_m_estimate"] = round(1964.0 * 0.3649 / span, 2)
    for dng, sh in zip(dngs, shutters):
        f = analyse_dng(dng, rois, ct_curve)
        f["shutter_us"] = sh
        res["frames"].append(f)
        print(f"{sh:6d} us: white {f['white_level']} clipped={f['white_clipped']} "
              f"uncorr ΔE {f.get('uncorrected', {}).get('median')} fit ΔE "
              f"{f['card_fit']['median']} red SNR {f['red_snr_grey128']} black/white "
              f"{f['black_patch']['black_over_white_g']} black G {f['black_patch']['level_rgb'][1]} "
              f"card clip "
              f"{f['clipping']['card_area_pct']} sharp {f['sharpness_card']}", flush=True)
    frames_for_pick = [{"shutter_us": f["shutter_us"],
                        "scores": {"sharpness": {"card": f["sharpness_card"]},
                                   "clipped": f["white_clipped"]
                                   or max(f["clipping"]["card_area_pct"]) > 0.5,
                                   "level_p995": max(f["white_level"])}} for f in res["frames"]]
    res["mac_pick"] = pick(frames_for_pick)
    res["pi_pick"] = sweep.get("pick")
    still = sorted(cam_dir.glob("imx708_image_*.jpg"))[0]
    res["production_jpeg"] = analyse_jpeg_pjpg(still, rois, Path(a.prod))
    res["production_jpeg"]["source"] = still.name + " (auto exposure / AWB, as production)"
    if a.s4:
        s4 = Path(a.s4)
        try:
            r4 = find_rois(read_dng(s4.with_suffix(".dng")))
            s4m = json.loads(s4.with_suffix(".json").read_text())
            s4m = s4m[-1] if isinstance(s4m, list) else s4m
            f4 = analyse_dng(s4.with_suffix(".dng"), r4, ct_curve, s4m)
            f4["source"] = str(s4)
            res["s4_single_lamp"] = f4
        except (ValueError, OSError) as exc:
            res["s4_single_lamp"] = {"error": str(exc)}
    (out / "colour.json").write_text(json.dumps(res, indent=1, default=float))
    print("mac pick:", res["mac_pick"].get("shutter_us"), "—", res["mac_pick"].get("reason"))
    pj = res["production_jpeg"]
    print("pjpg:", pj["quality"], pj["bytes"], "uncorr", pj["uncorrected"]["median"], "fit",
          pj["card_fit"]["median"], "excluded", list(pj["patches_excluded"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
