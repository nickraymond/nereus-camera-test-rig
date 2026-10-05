"""Decode a production .nrjxl still (bm #120 container) and render it like the camera would.

    NRJXL_BM_DIR=<bm #120 export> python -m compression_study.preview_loss.nrjxl_view \
        <still.nrjxl> --out <dir>

Writes <dir>/<stem>_full.png (bilinear demosaic, 1600x900 for today's crop, WB = the capture's
ColourGains, colour = its CCM, both from the container params) and <stem>.json (header,
distance, effort, bytes, messages at 288 B). Nothing is re-encoded.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from compression_study import common
from compression_study.preview_loss import codec as C

PLANES = ("R", "G1", "G2", "B")


def decode_nrjxl(blob: bytes) -> dict:
    head, payloads = C.rc().unpack_container(blob)
    p = head["params"]
    planes = {}
    for n, b in zip(PLANES, payloads):
        out = C.decode(b)
        if out is None:
            raise ValueError(f"plane {n} does not decode")
        planes[n] = out.astype(np.float64)
    crop = {"black": head["black"], "white": head["white"]}
    lin = {n: C.to_linear(v, crop) for n, v in planes.items()}
    mosaic = common.merge(lin, head["cfa"])
    meta = {"crop_xywh": p[1:3] + [head["w"], head["h"]], "native_wh": p[3:5],
            "exposure_us": p[6], "again": p[7] / 1000, "gains": [p[8] / 1e4, p[9] / 1e4],
            "ccm": [v / 1e4 for v in p[10:19]], "distance": p[20] / 100, "effort": p[21],
            "bytes": len(blob), "messages_288": C.chunks(len(blob)),
            "plane_bytes": [len(b) for b in payloads], "cfa": head["cfa"]}
    return {"head": head, "meta": meta, "mosaic": mosaic, "planes": planes}


def render(mosaic: np.ndarray, cfa: str, gains, ccm, scale: float | None = None) -> np.ndarray:
    """Linear mosaic (0..1) -> 8-bit sRGB: bilinear demosaic, ColourGains (R, B), CCM, then an
    exposure scale (p99.5 of green -> 0.9 unless given), sRGB curve."""
    from nereus_camera_test_rig.color.raw_io import demosaic_bilinear
    rgb = demosaic_bilinear(np.clip(mosaic, 0, 1).astype(np.float32), cfa)
    # clip after WB at the green clip level, so saturated highlights stay white (not pink)
    rgb = np.minimum(rgb * np.array([gains[0], 1.0, gains[1]], np.float32), 1.0)
    rgb = rgb @ np.asarray(ccm, np.float32).reshape(3, 3).T
    if scale is None:
        scale = 0.9 / max(float(np.percentile(rgb[..., 1], 99.5)), 1e-6)
    return np.round(common.srgb_oetf(np.clip(rgb * scale, 0, 1)) * 255).astype(np.uint8)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("nrjxl", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    d = decode_nrjxl(a.nrjxl.read_bytes())
    m = d["meta"]
    img = render(d["mosaic"], m["cfa"], m["gains"], m["ccm"])
    a.out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(a.out / f"{a.nrjxl.stem}_full.png"), img[..., ::-1])
    (a.out / f"{a.nrjxl.stem}.json").write_text(json.dumps(m, indent=1))
    print(json.dumps(m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
