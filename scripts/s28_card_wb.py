"""Card-grey white balance for the still -> video hand-off (Sprint28, 2026-10-09), run ON THE PI.

    python scripts/s28_card_wb.py <still.dng> [--geometry geometry.json] [--patch gray_mid]

Finds the V3 c1 card on the still's B3a crop (native x=1504, y=846, 1600x900): bilinear
demosaic of the black-subtracted, white-normalised RAW; the green channel as an 8-bit view;
ONE native-scale ArUco tag25h9 pass (the T0.3 detector) and the rig's quad checks. Samples
the grey patch (central 60 %) in the linear RGB crop and prints one JSON line:
  {"red_gain": G/R, "blue_gain": G/B, "rgb": [...], "source": "detected" | "fixed_geometry",
   "tags": n, "detect_s": ..., "vmhwm_kib": ..., ...}
``--awbgains red_gain,blue_gain`` makes that grey neutral in the ISP. When the card is not
located (dark, occluded), ``--geometry`` (the fixed sunrise-run homography, canonical ->
binned full frame) is the fallback; without it the script exits 2 and the caller records
the clip with auto WB or skips it.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.jpeg_geometry import JpegMap
from nereus_camera_test_rig.color.locate import _detect, _record, tag_geometry, tag_spec
from nereus_camera_test_rig.color.patches import homography, sample
from nereus_camera_test_rig.color.raw_io import bin2x2, demosaic_bilinear, normalize, read_dng

CROP = (1504, 846, 1600, 900)
CARD = Path(__file__).resolve().parents[1] / "configs/cards/nereus_v3_c1.yaml"


def vmhwm() -> int | None:
    try:
        return next(int(x.split()[1]) for x in open("/proc/self/status") if x.startswith("VmHWM:"))
    except OSError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dng", type=Path)
    ap.add_argument("--geometry", type=Path, default=None)
    ap.add_argument("--patch", default="gray_mid")
    ap.add_argument(
        "--min-level",
        type=float,
        default=0.005,
        help="grey G (fraction of white) below which the gains are noise: exit 3",
    )
    a = ap.parse_args()
    t0 = time.perf_counter()
    card = load_card(CARD)
    spec = tag_spec(card)
    fr = read_dng(a.dng)
    lin, sat, cfa = normalize(fr)
    x, y, w, h = CROP
    crop = demosaic_bilinear(lin[y : y + h, x : x + w], cfa)  # x, y even: CFA phase unchanged
    t1 = time.perf_counter()
    g = crop[..., 1]
    view = np.clip(g / max(float(np.percentile(g, 99.5)), 1e-6), 0, 1) ** (1 / 2.2) * 255 + 0.5
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "view.png"
        cv2.imwrite(str(p), view.astype(np.uint8))
        gray = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    found = _detect(gray, set(card.corner_map.values()), (1.0,), family=spec.family)
    rec = _record(
        {i: (d, s, "img") for i, (d, s) in found.items()},
        card.corner_map,
        JpegMap.offset(0, 0),
        "apriltag",
        tag_geometry(card),
        spec.ratio_range,
    )
    t2 = time.perf_counter()
    out = {
        "still": a.dng.name,
        "tags": len(rec.get("tags_found", [])),
        "located": bool(rec.get("located")),
        "prep_s": round(t1 - t0, 3),
        "detect_s": round(t2 - t1, 3),
        "patch": a.patch,
    }
    if rec.get("located"):
        H = homography(card, np.asarray(rec["quad_raw"], float))
        s = sample(crop, H, card.patch(a.patch).box, clip=None)
        out["source"] = "detected"
    elif a.geometry is not None:
        # fixed sunrise-run geometry: canonical -> binned full frame
        Hb = np.asarray(json.loads(a.geometry.read_text())["Hb"], float)
        b, _ = bin2x2(lin, cfa, sat)
        s = sample(b, Hb, card.patch(a.patch).box, clip=None)
        out["source"] = "fixed_geometry"
    else:
        out.update(source="none", reason=rec.get("reason"), vmhwm_kib=vmhwm())
        print(json.dumps(out))
        return 2
    rgb = np.asarray(s["mean"], float)
    if rgb[1] < a.min_level or min(rgb) <= 0:
        out.update(
            source=out["source"] + "_too_dark",
            grey_level_g=round(float(rgb[1]), 5),
            vmhwm_kib=vmhwm(),
        )
        print(json.dumps(out))
        return 3
    out.update(
        rgb=[round(float(v), 5) for v in rgb],
        red_gain=round(float(rgb[1] / max(rgb[0], 1e-6)), 4),
        blue_gain=round(float(rgb[1] / max(rgb[2], 1e-6)), 4),
        grey_level_g=round(float(rgb[1]), 4),
        vmhwm_kib=vmhwm(),
        total_s=round(time.perf_counter() - t0, 3),
    )
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
