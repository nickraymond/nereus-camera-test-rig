"""How close is a 24-patch chart to the X-Rite ColorChecker Classic? (card truth, Phase 0)

    python -m host_tools.chart_vs_colorchecker --out results/chart_vs_cc.json

Reads the chart on existing IMX708 RAWs (S4 bench cool + warm, the 2026-10-05 V3 placement
snapshots) and renders each patch through the camera's FACTORY colour, two ways:
  isp: the libcamera tuning's CCM and the frame's AWB ColourGains (rpicam metadata);
  dng: the DNG ColorMatrix1 (XYZ -> camera) adapted from the AsShotNeutral to D65;
  isp_greywb: the tuning CCM after white balance on the chart's own mid greys (the scene AWB
  of these frames was set on warm cardboard, which puts a blue cast on every patch).
One exposure scale per frame (least squares on the Y of the four mid greys) maps to the
reference's absolute level. Then CIELAB D50 (Bradford from D65) and CIEDE2000 against the
ColorChecker Classic reference (X-Rite, post-November-2014 formulation, Lab D50), patch by patch.
This rests on the factory CCM: an error there shows up as chart-vs-ColorChecker difference.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from nereus_camera_test_rig.color import card_truth as T
from nereus_camera_test_rig.color.calibrate import dng_to_linear
from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.chart import load_chart
from nereus_camera_test_rig.color.metrics import SRGB_TO_XYZ, delta_e2000
from nereus_camera_test_rig.color.raw_io import read_dng

# ColorChecker Classic, X-Rite post-Nov-2014 formulation, CIELAB D50 / 2 deg (row-major,
# chart upright: dark skin ... cyan, then white ... black).
CC_2014 = [
    ("dark_skin", 37.54, 14.37, 14.92), ("light_skin", 64.66, 19.27, 17.50),
    ("blue_sky", 49.32, -3.82, -22.54), ("foliage", 43.46, -12.74, 22.72),
    ("blue_flower", 54.94, 9.61, -24.79), ("bluish_green", 70.48, -32.26, -0.37),
    ("orange", 62.73, 35.83, 56.50), ("purplish_blue", 39.43, 10.75, -45.17),
    ("moderate_red", 50.57, 48.64, 16.67), ("purple", 30.10, 22.54, -20.87),
    ("yellow_green", 71.77, -24.13, 58.19), ("orange_yellow", 71.51, 18.24, 67.37),
    ("blue", 28.37, 15.42, -49.80), ("green", 54.38, -39.72, 32.27),
    ("red", 42.43, 51.05, 28.62), ("yellow", 81.80, 2.67, 80.41),
    ("magenta", 50.63, 51.28, -14.12), ("cyan", 49.57, -29.71, -28.32),
    ("white", 95.19, -1.03, 2.93), ("neutral_8", 81.29, -0.57, 0.44),
    ("neutral_65", 66.89, -0.75, -0.06), ("neutral_5", 50.76, -0.13, 0.14),
    ("neutral_35", 35.63, -0.46, -0.48), ("black", 20.64, 0.07, -0.46),
]
SCALE_GREYS = (19, 20, 21, 22)          # neutral 8 / 6.5 / 5 / 3.5 (white may clip, black flares
D65 = np.array([0.95047, 1.0, 1.08883])

REPO = Path(__file__).resolve().parents[1]
FRAMES = [  # (label, dng, rpicam json, card yaml, chart search region in card canonical px)
    *[(f"s4_cool_r{i}", f"data/s4_20260930/cool_imx708/stop_+0_r{i}.dng",
       f"data/s4_20260930/cool_imx708/stop_+0_r{i}.json", "configs/cards/nereus_v1.yaml",
       [600, 760, 1420, 1360]) for i in range(3)],
    *[(f"s4_warm_r{i}", f"data/s4_20260930/warm_imx708/stop_+0_r{i}.dng",
       f"data/s4_20260930/warm_imx708/stop_+0_r{i}.json", "configs/cards/nereus_v1.yaml",
       [600, 760, 1420, 1360]) for i in range(3)],
]


def lab50(lin_srgb: np.ndarray) -> np.ndarray:
    xyz65 = lin_srgb @ SRGB_TO_XYZ.T
    return T.xyz_to_lab(xyz65 @ T.bradford(D65, T.D50).T)


def scale_to_ref(xyz_y: np.ndarray, ref_lab) -> float:
    ref_y = np.array([T.lab_to_xyz(np.array(ref_lab[i]))[1] for i in SCALE_GREYS])
    y = np.array([xyz_y[i] for i in SCALE_GREYS])
    return float((y @ ref_y) / (y @ y))


def analyse(cam_rgb: np.ndarray, meta: dict, frame) -> dict:
    ref = np.array([r[1:] for r in CC_2014])
    out = {}
    g = meta["ColourGains"]
    ccm = np.asarray(meta["ColourCorrectionMatrix"], float).reshape(3, 3)
    isp = (cam_rgb * [g[0], 1, g[1]]) @ ccm.T
    # the same CCM, white-balanced on the chart's own mid greys (neutral 6.5 / 5 / 3.5) instead
    # of the scene AWB: separates the chart's colours from the frame's white point
    grey = cam_rgb[[20, 21, 22]].mean(axis=0)
    isp_greywb = (cam_rgb * (grey[1] / grey)) @ ccm.T
    paths = {"isp": isp, "isp_greywb": isp_greywb}
    if frame.color_matrix is not None and frame.as_shot_wb:
        neutral = 1 / np.asarray(frame.as_shot_wb, float)
        paths["dng"] = dng_to_linear(cam_rgb, np.asarray(frame.color_matrix).reshape(3, 3), neutral)
    for name, lin in paths.items():
        k = scale_to_ref((lin @ SRGB_TO_XYZ.T)[:, 1], ref)
        lab = lab50(lin * k)
        de = delta_e2000(lab, ref)
        out[name] = {"lab": np.round(lab, 2).tolist(), "de2000": np.round(de, 2).tolist(),
                     "scale": round(k, 4)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--data-root", type=Path, default=REPO,
                    help="root the data/ paths are relative to (the primary checkout)")
    ap.add_argument("--extra", nargs="*", default=[],
                    help="label=dng=rpicam_json=card_yaml=x0,y0,x1,y1 (more frames)")
    a = ap.parse_args()
    chart = load_chart(REPO / "configs/charts/pixel_perfect_24.yaml")
    frames = [(lb, a.data_root / d, a.data_root / j, REPO / c, r) for lb, d, j, c, r in FRAMES]
    for e in a.extra:
        lb, d, j, c, r = e.split("=")
        frames.append((lb, Path(d), Path(j), REPO / c, [float(v) for v in r.split(",")]))
    res = {"reference": "ColorChecker Classic, X-Rite post-Nov-2014, Lab D50",
           "patches": [r[0] for r in CC_2014], "frames": {}}
    for lb, dng, js, card_yaml, region in frames:
        s = T.sample_frame(dng, load_card(card_yaml), chart, region)
        if "error" in s or "error" in s.get("chart", {}):
            res["frames"][lb] = {"error": s.get("error") or s["chart"]["error"]}
            print(lb, res["frames"][lb])
            continue
        cam = np.array([s["chart_patches"][p.id]["mean"] for p in chart.patches])
        clip = [p.id for p in chart.patches
                if max(s["chart_patches"][p.id]["clip_frac"] or [0]) > 0]
        meta = json.loads(Path(js).read_text())
        r = analyse(cam, meta, read_dng(dng))
        r["clipped"] = clip
        r["meta"] = {k: meta.get(k) for k in ("ColourTemperature", "ColourGains", "ExposureTime")}
        res["frames"][lb] = r
        for name in ("isp", "dng", "isp_greywb"):
            if name in r:
                de = np.array(r[name]["de2000"])
                print(f"{lb:14s} {name}: dE00 median {np.median(de):.1f}  "
                      f"colours {np.median(de[:18]):.1f}  greys {np.median(de[18:]):.1f}  "
                      f"max {de.max():.1f}  clipped {clip}")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
