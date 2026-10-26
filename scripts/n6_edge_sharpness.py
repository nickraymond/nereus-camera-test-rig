#!/usr/bin/env python3
"""n6_edge_sharpness.py — lens sharpness from the V3 card's tag edges (SPEC_n6_eval must do 14, first pass).

Measures the 10-90 % edge rise across the outer edge of each card tag: light-grey quiet zone (9 mm) outside,
the tag's black border (9 mm) inside (docs/reference_card_v3.md: 63 mm tags of 9 mm cells on 81 mm grey squares).
For each side of each tag, intensity profiles are sampled along the outward normal at points 20-80 % along the
side (corners skipped), averaged into one edge-spread function, and the 10 % / 90 % crossings are interpolated.
The rise in pixels is converted to mm on the card with the tag's own edge length (63 mm per the card YAML).

Limits, stated in every summary: the input is the camera JPEG (the N6 ISP may sharpen; JPEG blurs), so this is the
system's edge rise, not the bare lens. Only where the card sits in the frame is measured (the centre in today's
framing); corners need the target moved.

Usage: python scripts/n6_edge_sharpness.py IMG [IMG ...] --out summary.json [--card configs/cards/nereus_v3_c1.yaml]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import numpy as np

CELLS = 7  # tag25h9: 5x5 data + a black border cell each side


def _bilinear(gray: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    x0 = np.clip(np.floor(xs).astype(int), 0, w - 2)
    y0 = np.clip(np.floor(ys).astype(int), 0, h - 2)
    fx, fy = xs - x0, ys - y0
    a, b = gray[y0, x0], gray[y0, x0 + 1]
    c, d = gray[y0 + 1, x0], gray[y0 + 1, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def side_rise(gray: np.ndarray, p0, p1, centre, half_px: float, step: float = 0.25,
              along=(0.2, 0.8), n_lines: int = 25) -> dict:
    """10-90 % rise (px) across the edge p0-p1; inside = towards `centre` (dark), outside = light."""
    p0, p1, centre = (np.asarray(v, float) for v in (p0, p1, centre))
    t = (p1 - p0) / np.linalg.norm(p1 - p0)
    n = np.array([t[1], -t[0]])
    mid = (p0 + p1) / 2
    if np.dot(centre - mid, n) > 0:  # make n point outwards (away from the tag centre)
        n = -n
    offs = np.arange(-half_px, half_px + step / 2, step)
    profs = []
    for f in np.linspace(along[0], along[1], n_lines):
        base = p0 + f * (p1 - p0)
        pts = base[None, :] + offs[:, None] * n[None, :]
        profs.append(_bilinear(gray, pts[:, 0], pts[:, 1]))
    esf = np.mean(profs, axis=0)
    k = max(2, len(esf) // 6)
    lo, hi = float(esf[:k].mean()), float(esf[-k:].mean())  # inside (dark) ... outside (light)
    if hi - lo < 10:  # no usable contrast
        return {"ok": False, "contrast": hi - lo}
    norm = (esf - lo) / (hi - lo)

    def cross(level):
        idx = np.nonzero(norm >= level)[0]
        if not len(idx) or idx[0] == 0:
            return None
        i = idx[0]
        return offs[i - 1] + (level - norm[i - 1]) / (norm[i] - norm[i - 1]) * step

    x10, x90 = cross(0.1), cross(0.9)
    if x10 is None or x90 is None:
        return {"ok": False, "contrast": hi - lo}
    return {"ok": True, "rise_px": float(x90 - x10), "contrast": hi - lo, "dark": lo, "light": hi}


def tag_rises(gray: np.ndarray, quad, edge_mm: float) -> list[dict]:
    """All four outer sides of one tag; mm on the card from the tag's own mean edge length."""
    q = np.asarray(quad, float).reshape(4, 2)
    edge_px = float(np.mean([np.linalg.norm(q[k] - q[(k + 1) % 4]) for k in range(4)]))
    half = 0.6 * edge_px / CELLS  # stays inside the 1-cell black border and the 1-cell quiet zone
    out = []
    for k in range(4):
        r = side_rise(gray, q[k], q[(k + 1) % 4], q.mean(0), half)
        r.update(side=k, edge_px=edge_px)
        if r["ok"]:
            r["rise_mm"] = r["rise_px"] * edge_mm / edge_px
        out.append(r)
    return out


def _summary(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    q = statistics.quantiles(vals, n=4) if len(vals) >= 4 else [min(vals), statistics.median(vals), max(vals)]
    return {"n": len(vals), "median": round(statistics.median(vals), 3), "p25": round(q[0], 3),
            "p75": round(q[-1], 3), "min": round(min(vals), 3), "max": round(max(vals), 3)}


def main(argv: list[str] | None = None) -> int:
    import cv2
    import yaml

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("images", nargs="+")
    ap.add_argument("--card", default=str(Path(__file__).resolve().parents[1] / "configs/cards/nereus_v3_c1.yaml"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    card = yaml.safe_load(open(a.card))
    ids = {int(k) for k in card["tags"]}
    edge_mm = float(card["physical_mm"]["tag_edge"])
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_25h9),
                                  cv2.aruco.DetectorParameters())
    frames, px, mm = [], [], []
    for f in a.images:
        img = cv2.imread(f, cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
        if img is None:
            print(f"n6_edge_sharpness: cannot open {f}", file=sys.stderr)
            return 1
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        corners, found, _ = det.detectMarkers(gray.astype(np.uint8))
        tags = {}
        for q, i in zip(corners if found is not None else [], [] if found is None else found.ravel()):
            if int(i) in ids:
                sides = tag_rises(gray, q, edge_mm)
                tags[int(i)] = sides
                px += [s["rise_px"] for s in sides if s["ok"]]
                mm += [s["rise_mm"] for s in sides if s["ok"]]
        frames.append({"file": Path(f).name, "w": img.shape[1], "h": img.shape[0], "tags": tags})
    out = {"edge_mm": edge_mm, "frames": len(frames), "edges_measured": len(px),
           "rise_px": _summary(px), "rise_mm_on_card": _summary(mm),
           "limits": "camera JPEG (ISP + JPEG in the loop); centre of the frame only (card position)",
           "per_frame": frames}
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("frames", "edges_measured", "rise_px", "rise_mm_on_card")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
