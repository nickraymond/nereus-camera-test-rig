"""One-off colour calibration for the sunrise sweep's per-frame card ΔE (2026-10-08), from the
2026-10-06 IMX708 truth shoot (ColorChecker Classic beside the V3 c1 card, LEDs 5300 K).

    python scripts/s28_card_calib.py <truth_20261006 root> --out card_calib.json

Pipeline it calibrates (the same one the Pi applies per sweep frame):
  binned linear RAW -> / lens shading (rpi.alsc, tuning file, full strength, 5300 K)
  -> white balance on the V3 card greys (gains making the mean of gray_light, gray_light2,
     gray_mid neutral, G = 1) -> camera RGB -> XYZ D50 (3x3 and root-poly 2, fitted on the 24
     ColorChecker patches, leave-one-patch-out ΔE00 reported).
Also stores the provisional V3 truth (Lab D50, IMX708 2026-10-06, no flat-field, PR #93) and
the V3 design values (Lab D50 of the print-file sRGB), so the Pi needs no other input.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from host_tools import v3_truth_shoot as V

from nereus_camera_test_rig.color import card_truth as T
from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.chart import find_chart, load_chart
from nereus_camera_test_rig.color.patches import mosaic_to_binned, sample
from nereus_camera_test_rig.color.raw_io import bin2x2, normalize

REPO = Path(__file__).resolve().parents[1]
TUNING = REPO / "docs/card_truth/truth_20261006/imx708_wide_vc4_tuning.json"
TRUTH = REPO / "docs/card_truth/truth_20261006/truth_alsc.json"
ANCHORS = ("gray_light", "gray_light2", "gray_mid")


def wb_gains(rgb_greys: np.ndarray) -> np.ndarray:
    m = rgb_greys.mean(axis=0)
    return m[1] / m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    card, chart = load_card(V.CARD), load_chart(V.CHART)
    ref = T.load_reference(V.REF)
    files = sorted((a.root / "imx708/cards").glob("stop_+0_r*.dng"))
    f0 = V.read(files[0])
    Hm, _ = V.locate(f0, card)
    Hb = mosaic_to_binned(f0.valid_crop) @ Hm
    lin, sat, cfa = normalize(f0)
    b0, _ = bin2x2(lin, cfa, sat)
    flat = V.alsc_flat(TUNING, b0.shape)
    b0 = b0 / flat
    boxes = find_chart(b0, Hb, sample(b0, Hb, card.patch("gray_light").box)["mean"], chart,
                       (-1500, 2700, 5700, 7000))["boxes"]
    cc, v3 = {p.id: [] for p in chart.patches}, {p.id: [] for p in card.patches}
    for f in files:
        lin, sat, cfa = normalize(V.read(f))
        b, _ = bin2x2(lin, cfa, sat)
        b = b / flat
        for p in chart.patches:
            cc[p.id].append(sample(b, Hb, boxes[p.id])["mean"])
        for p in card.patches:
            v3[p.id].append(sample(b, Hb, p.box)["mean"])
    ccm = {k: np.median(v, axis=0) for k, v in cc.items()}
    v3m = {k: np.median(v, axis=0) for k, v in v3.items()}
    g = wb_gains(np.array([v3m[k] for k in ANCHORS]))
    rgb = np.array([ccm[p.id] * g for p in chart.patches])
    xyz = np.array([T.lab_to_xyz(np.array(ref[p.label]["lab"])) for p in chart.patches])
    out = {"source": "IMX708 2026-10-06 truth shoot, stop 0, 3 repeats, ALSC 5300 K, "
                     "WB on the V3 greys, ColorChecker Classic post-2014",
           "anchors": list(ANCHORS), "alsc_ct": 5300.0, "models": {}}
    for model in ("linear3x3", "rootpoly2"):
        out["models"][model] = {"matrix": T.fit(rgb, xyz, model).tolist(),
                                "loo_de00": T.stats(T.loo(rgb, xyz, model))}
    truth = json.loads(TRUTH.read_text())["cams"]["imx708"]["stops"]["stop_+0"]["v3_lab"]
    out["truth_provisional_lab_d50"] = truth
    out["design_lab_d50"] = {
        p.id: V.srgb8_to_lab50(np.array(p.design or p.truth, float)).round(2).tolist()
        for p in card.patches
    }
    a.out.write_text(json.dumps(out, indent=1))
    print({m: v["loo_de00"] for m, v in out["models"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
