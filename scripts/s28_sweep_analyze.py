"""Sunrise sweep (Sprint28 R5, 2026-10-08) analysis, run ON THE PI (nereus002).

    python scripts/s28_sweep_analyze.py <run_dir> [--ref s1010_stock]

Per frame (s<HHMM>_<lowgain|stock>.dng + .json): rpicam metadata (ExposureTime, AnalogueGain,
DigitalGain, Lux, ColourTemperature); grey-patch noise = std / mean of the binned green
channel on the ColorChecker neutrals (central 60 % of each patch, spatial: shot + read
noise + any print texture); AprilTag sharpness = mean of the top 10 % green gradient
magnitudes in each V3 tag box / (p95 - p5) of that box, averaged over the 4 tags.

Geometry: the camera and the card did not move, so ONE card homography (from --ref, a
bright frame) and ONE set of ColorChecker boxes serve every frame; dark frames at gain 16
need no tag detection of their own. Writes <run_dir>/r5_sweep.csv.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from pathlib import Path

import numpy as np

from nereus_camera_test_rig.color.card import Box, load_card
from nereus_camera_test_rig.color.chart import find_chart, load_chart
from nereus_camera_test_rig.color.jpeg_geometry import JpegMap
from nereus_camera_test_rig.color.locate import locate_frame, tag_geometry, tag_spec
from nereus_camera_test_rig.color.patches import homography, mosaic_to_binned, sample
from nereus_camera_test_rig.color.raw_io import bin2x2, normalize, read_dng

REPO = Path(__file__).resolve().parents[1]
CARD = REPO / "configs/cards/nereus_v3_c1.yaml"
CHART = REPO / "configs/charts/colorchecker_classic_24.yaml"
V3_GREYS = ("gray_light", "gray_light2", "gray_mid")  # big, unclipped at the locked exposures
NEUTRALS = ("neutral_8", "neutral_65", "neutral_5", "neutral_35")  # white may clip, black is dim
CHART_REGION = (-1500, 2700, 5700, 7000)  # canonical px: the ColorChecker sits below the card


def binned(path: str):
    fr = read_dng(path)
    lin, sat, cfa = normalize(fr)
    b, clip = bin2x2(lin, cfa, sat)
    return fr, b, clip


def hp_noise(b: np.ndarray, Hb: np.ndarray, box) -> float | None:
    """Pixel noise on one grey patch: std of (green - its 5x5 local mean) / mean green, over
    the central 50 % of the patch (axis-aligned in the image; the camera faces the card).
    The local mean removes the light gradient across the patch, so this is noise, not shading."""
    import cv2

    x0, y0 = box.x + box.w * 0.25, box.y + box.h * 0.25
    c = np.array([[[x0, y0]], [[x0 + box.w * 0.5, y0 + box.h * 0.5]]], float)
    (u0, v0), (u1, v1) = cv2.perspectiveTransform(c, Hb).reshape(-1, 2)
    u0, u1 = sorted((int(u0), int(u1)))
    v0, v1 = sorted((int(v0), int(v1)))
    g = b[v0:v1, u0:u1, 1].astype(np.float32)
    if g.shape[0] < 12 or g.shape[1] < 12 or g.mean() <= 0:
        return None
    res = (g - cv2.blur(g, (5, 5)))[2:-2, 2:-2]
    return float(res.std() * np.sqrt(25 / 24) / g.mean())


def tag_sharpness(b: np.ndarray, Hb: np.ndarray, card) -> float:
    import cv2

    g = b[..., 1]
    vals = []
    for t in card.tags.values():
        cx, cy = t.center
        ex, ey = t.edge
        c = np.array([[[cx - ex / 2, cy - ey / 2]], [[cx + ex / 2, cy + ey / 2]]], float)
        (x0, y0), (x1, y1) = cv2.perspectiveTransform(c, Hb).reshape(-1, 2)
        x0, x1 = sorted((int(x0), int(x1)))
        y0, y1 = sorted((int(y0), int(y1)))
        roi = g[max(y0, 0) : y1, max(x0, 0) : x1]
        if roi.size < 100:
            continue
        gy, gx = np.gradient(roi)
        mag = np.hypot(gx, gy)
        span = float(np.percentile(roi, 95) - np.percentile(roi, 5))
        if span <= 0:
            continue
        vals.append(float(np.mean(np.sort(mag.ravel())[-max(1, mag.size // 10) :])) / span)
    return round(float(np.mean(vals)), 4) if vals else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--ref", default="s1010_stock")
    ap.add_argument(
        "--geometry",
        type=Path,
        default=None,
        help="geometry.json (Hb + chart boxes) from --write-geometry on another "
        "host: the Zero 2 W OOMs on full-frame multi-scale tag detection",
    )
    ap.add_argument("--write-geometry", type=Path, default=None)
    ap.add_argument(
        "--calib",
        type=Path,
        default=None,
        help="card_calib.json (scripts/s28_card_calib.py): adds the card ΔE columns",
    )
    ap.add_argument(
        "--tuning", type=Path, default=Path("/usr/share/libcamera/ipa/rpi/vc4/imx708_wide.json")
    )
    a = ap.parse_args()
    card, chart = load_card(CARD), load_chart(CHART)
    neutral = [p for p in chart.patches if p.label in NEUTRALS]
    calib = json.loads(a.calib.read_text()) if a.calib else None
    if a.geometry is not None:
        g = json.loads(a.geometry.read_text())
        Hb = np.asarray(g["Hb"], float)
        boxes = {k: Box(*v) for k, v in g["chart_boxes"].items()}
        print(f"geometry from {a.geometry} (reference {g['reference']}, tags {g['tags_found']})")
        return analyse(a.run_dir, card, Hb, boxes, neutral, calib, a.tuning)
    fr, b, _ = binned(str(a.run_dir / f"{a.ref}.dng"))
    rec = locate_frame(
        Path("x"),
        None,
        card.corner_map,
        JpegMap.offset(0, 0),
        raw_reader=lambda _p: fr,
        geometry=tag_geometry(card),
        spec=tag_spec(card),
    )
    if not rec["located"]:
        raise SystemExit(f"card not found on the reference frame {a.ref}: {rec.get('reason')}")
    Hb = mosaic_to_binned(fr.valid_crop) @ homography(card, np.asarray(rec["quad_raw"], float))
    white = sample(b, Hb, card.patch("gray_light").box)["mean"]
    boxes = find_chart(b, Hb, white, chart, CHART_REGION)["boxes"]
    print(f"reference {a.ref}: tags {rec['tags_found']}, chart boxes {len(boxes)}")
    if a.write_geometry is not None:
        a.write_geometry.write_text(
            json.dumps(
                {
                    "reference": f"{a.ref}.dng",
                    "tags_found": rec["tags_found"],
                    "Hb": Hb.tolist(),
                    "chart_boxes": {k: [v.x, v.y, v.w, v.h] for k, v in boxes.items()},
                },
                indent=1,
            )
        )
        return 0
    b = None  # free the reference frame before the loop
    return analyse(a.run_dir, card, Hb, boxes, neutral, calib, a.tuning)


def alsc_flat(tuning: Path, shape, ct: float) -> np.ndarray:
    """IMX708 lens shading (1 / gain) from the tuning file's rpi.alsc, luminance at full
    strength, colour tables interpolated at ``ct`` (copy of host_tools.v3_truth_shoot)."""
    import cv2

    alsc = next(
        x["rpi.alsc"] for x in json.loads(tuning.read_text())["algorithms"] if "rpi.alsc" in x
    )

    def at_ct(cals):
        cts = [c["ct"] for c in cals]
        tabs = [np.array(c["table"], float) for c in cals]
        x = min(max(ct, cts[0]), cts[-1])
        i = max(j for j in range(len(cts)) if cts[j] <= x)
        if i == len(cts) - 1:
            return tabs[i]
        w = (x - cts[i]) / (cts[i + 1] - cts[i])
        return (1 - w) * tabs[i] + w * tabs[i + 1]

    lum = np.array(alsc["luminance_lut"], float)
    gain = np.stack(
        [at_ct(alsc["calibrations_Cr"]) * lum, lum, at_ct(alsc["calibrations_Cb"]) * lum], -1
    )
    g = cv2.resize(
        gain.reshape(12, 16, 3).astype(np.float32),
        (shape[1], shape[0]),
        interpolation=cv2.INTER_LINEAR,
    )
    return 1.0 / g


def srgb8_to_lab50(v) -> np.ndarray:
    from nereus_camera_test_rig.color import card_truth as T
    from nereus_camera_test_rig.color.metrics import SRGB_TO_XYZ

    v = np.asarray(v, float) / 255
    lin = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    d65 = np.array([0.95047, 1.0, 1.08883])
    return T.xyz_to_lab((lin @ SRGB_TO_XYZ.T) @ T.bradford(d65, T.D50).T)


def card_colour(raw_means: dict, flat_means: dict, jpeg_means: dict, card, calib: dict) -> dict:
    """Per-frame V3 card colour error, two paths, vs the provisional truth (and the design).

    RAW: binned linear RAW / lens shading -> WB on the anchor greys -> calibrated camera
    RGB -> XYZ D50 (root-poly 2, 2026-10-06 ColorChecker) -> exposure set so gray_light's Y
    equals its truth Y. JPEG: the camera's own JPEG, sRGB -> Lab D50, lightness matched on
    gray_mid (one Y scale, no colour change). Scored on the 8 colour patches (the greys are
    the anchors). ``nol`` = ΔE00 with each patch's L* set to the truth L*: hue/chroma only."""
    from nereus_camera_test_rig.color import card_truth as T
    from nereus_camera_test_rig.color.metrics import delta_e2000

    model = "rootpoly2"
    M = np.asarray(calib["models"][model]["matrix"], float)
    truth = {k: np.asarray(v, float) for k, v in calib["truth_provisional_lab_d50"].items()}
    design = {k: np.asarray(v, float) for k, v in calib["design_lab_d50"].items()}
    colours = [p.id for p in card.patches if p.group == "color"]
    out: dict = {}

    def score(lab: dict, prefix: str) -> None:
        de = {k: float(delta_e2000(lab[k], truth[k])) for k in colours}
        nol = {k: float(delta_e2000(np.r_[truth[k][0], lab[k][1:]], truth[k])) for k in colours}
        dd = [float(delta_e2000(lab[k], design[k])) for k in colours]
        worst = max(de, key=de.get)
        out.update(
            {
                f"{prefix}_de00_mean": round(float(np.mean(list(de.values()))), 2),
                f"{prefix}_de00_worst": round(de[worst], 2),
                f"{prefix}_worst_patch": worst,
                f"{prefix}_de00_nol_mean": round(float(np.mean(list(nol.values()))), 2),
                f"{prefix}_de00_vs_design_mean": round(float(np.mean(dd)), 2),
            }
        )

    rgb = {k: np.asarray(raw_means[k], float) / np.asarray(flat_means[k], float) for k in raw_means}
    gm = np.mean([rgb[k] for k in calib["anchors"]], axis=0)
    gains = gm[1] / gm
    xyz = {k: T.apply(M, (v * gains)[None], model)[0] for k, v in rgb.items()}
    k_exp = T.lab_to_xyz(truth["gray_light"])[1] / xyz["gray_light"][1]
    score({k: T.xyz_to_lab(v * k_exp) for k, v in xyz.items()}, "raw")
    out["raw_wb_gains"] = [round(float(x), 4) for x in gains]
    if jpeg_means:
        lab_j = {k: srgb8_to_lab50(v) for k, v in jpeg_means.items()}
        y_t, y_j = T.lab_to_xyz(truth["gray_mid"])[1], T.lab_to_xyz(lab_j["gray_mid"])[1]
        lab_j = {k: T.xyz_to_lab(T.lab_to_xyz(v) * (y_t / y_j)) for k, v in lab_j.items()}
        score(lab_j, "jpeg")
    return out


def analyse(
    run_dir: Path,
    card,
    Hb: np.ndarray,
    boxes: dict,
    neutral: list,
    calib: dict | None = None,
    tuning: Path | None = None,
) -> int:
    import cv2

    rows, export = (
        [],
        {
            "Hb_canonical_to_binned": Hb.tolist(),
            "card": str(CARD.relative_to(REPO)),
            "chart_boxes_canonical": {k: [v.x, v.y, v.w, v.h] for k, v in boxes.items()},
            "binned": "2x2 superpixel of the black-subtracted, white-normalised RAW "
            "(R, mean G, B), 2304x1296; px (i, j) centred at mosaic (2j+0.5, 2i+0.5)",
            "jpeg": "camera JPEG decoded at half size (2304x1296) = the binned grid",
            "frames": {},
        },
    )
    flat = None
    for dng in sorted(glob.glob(str(run_dir / "s*_*.dng"))):
        stem = os.path.basename(dng)[:-4]
        slot, prof = stem[1:].split("_")
        m = json.loads(Path(dng[:-4] + ".json").read_text())
        _, bb, clip = binned(dng)
        if calib is not None and flat is None:
            flat = alsc_flat(tuning, bb.shape, calib["alsc_ct"])
        raw_s = {p.id: sample(bb, Hb, p.box, clip=clip) for p in card.patches}
        cc_s = {p.id: sample(bb, Hb, boxes[p.id], clip=clip) for p in neutral}
        jpg = cv2.imread(dng[:-4] + ".jpg", cv2.IMREAD_REDUCED_COLOR_2)
        jpeg_s = (
            {
                p.id: sample(jpg[..., ::-1].astype(np.float32), Hb, p.box)["mean"]
                for p in card.patches
            }
            if jpg is not None and jpg.shape[:2] == bb.shape[:2]
            else {}
        )
        del jpg
        nz, cc_means = [], []
        for p in neutral:
            s = cc_s[p.id]
            if s.get("mean") and s["mean"][1] > 0 and max(s.get("clip_frac") or [0]) == 0:
                nz.append(s["std"][1] / s["mean"][1])
                cc_means.append(s["mean"][1])
        # The ColorChecker grey row is only valid when it reads as a ramp (neutral 8 brightest,
        # 3.5 darkest, >= 2x apart). Before ~09:50 on 2026-10-08 a ChArUco board covered it.
        cc_ok = len(cc_means) == len(neutral) and cc_means[0] > 2 * cc_means[-1]
        # The V3 card greys are in view in every frame: the primary noise metric.
        v3, hp = [], []
        for pid in V3_GREYS:
            s = raw_s[pid]
            if s.get("mean") and s["mean"][1] > 0 and max(s.get("clip_frac") or [0]) == 0:
                v3.append(s["std"][1] / s["mean"][1])
                n = hp_noise(bb, Hb, card.patch(pid).box)
                if n is not None:
                    hp.append(n)
        rows.append(
            {
                "slot": slot,
                "profile": prof,
                "ExposureTime": m["ExposureTime"],
                "AnalogueGain": round(m["AnalogueGain"], 4),
                "DigitalGain": round(m["DigitalGain"], 4),
                "Lux": round(m["Lux"], 2),
                "ColourTemperature": m.get("ColourTemperature"),
                "v3_grey_hp_noise": round(float(np.mean(hp)), 5) if hp else "",
                "v3_grey_noise_cv": round(float(np.mean(v3)), 5) if v3 else "",
                "v3_grey_patches": len(v3),
                "cc_grey_noise_cv": round(float(np.mean(nz)), 5) if (nz and cc_ok) else "",
                "cc_grey_row_ok": cc_ok,
                "tag_sharpness": tag_sharpness(bb, Hb, card),
                "v3_clipped_patches": ";".join(
                    k for k, s in raw_s.items() if max(s.get("clip_frac") or [0]) > 0.001
                ),
            }
        )
        flat_s = (
            {p.id: sample(flat, Hb, p.box)["mean"] for p in card.patches}
            if flat is not None
            else {}
        )
        if calib is not None:
            rows[-1].update(
                card_colour({k: s["mean"] for k, s in raw_s.items()}, flat_s, jpeg_s, card, calib)
            )
        export["frames"][stem] = {
            "metadata": {
                k: m.get(k)
                for k in (
                    "ExposureTime",
                    "AnalogueGain",
                    "DigitalGain",
                    "Lux",
                    "ColourTemperature",
                    "ColourGains",
                )
            },
            "v3_raw": {
                k: {
                    "mean": s["mean"],
                    "std": s["std"],
                    "n_px": s["n_px"],
                    "clip_frac": s.get("clip_frac"),
                }
                for k, s in raw_s.items()
            },
            "v3_lens_shading_flat": flat_s,
            "v3_jpeg_srgb8": jpeg_s,
            "cc_neutrals_raw": {k: s["mean"] for k, s in cc_s.items()},
            "cc_grey_row_ok": cc_ok,
        }
        r = rows[-1]
        print(
            stem,
            r["ExposureTime"],
            r["AnalogueGain"],
            r["v3_grey_hp_noise"],
            r["cc_grey_noise_cv"],
            r["tag_sharpness"],
            flush=True,
        )
        del bb, clip
    with open(run_dir / "r5_sweep.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (run_dir / "card_samples.json").write_text(json.dumps(export, indent=1))
    print("wrote", run_dir / "r5_sweep.csv", len(rows), "rows +", run_dir / "card_samples.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
