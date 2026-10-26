#!/usr/bin/env python3
"""n6_raw_compare.py — Mac analysis for docs/n6_eval/TEST_CARD_RAW_COMPARE.md (OpenMV 8-bit Bayer, card V3).

For each set of frames (one sensor, one framesize, one exposure; written by ``n6_raw_compare_capture.py``):
- card located from the four tag25h9 tags on the demosaiced green plane (``color/raw_io.demosaic_bilinear``, the same
  code for every sensor), canonical patch boxes mapped through the tag-centre homography, central 60 % of each box;
- per grey patch and CFA channel: mean DN, temporal noise (per-pixel std over the frames, RMS), SNR, and the
  correlation of the temporal noise between same-channel horizontal neighbours (on-chip denoise shows as > 0, OQ-54);
- edge 10-90 % rise on the four tags' outer sides (``n6_edge_sharpness.tag_rises``), in px and in mm on the card.
Across a sensor's exposures: mean vs exposure per grey patch (linearity) and its intercept (a black-level estimate
for when no dark frames exist).

Limits stated in every output: frame centre only; 8-bit data; black level from the sidecar unless given.

Usage: python scripts/n6_raw_compare.py --set NAME DIR GLOB [--set ...] --out results.json [--card YAML] [--black DN]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import n6_edge_sharpness as es  # noqa: E402
from nereus_camera_test_rig.color.raw_io import demosaic_bilinear  # noqa: E402

GREYS = ("gray_light", "gray_light2", "gray_mid", "gray_black")
INSET = 0.2  # central 60 % of each patch box


def cfa_offsets(cfa: str) -> dict[str, list[tuple[int, int]]]:
    """(dy, dx) of each channel's sites in the 2x2 tile; G has two."""
    out: dict[str, list[tuple[int, int]]] = {"R": [], "G": [], "B": []}
    for k, ch in enumerate(cfa.upper()):
        out[ch].append((k // 2, k % 2))
    return out


def load_frame(path: Path) -> tuple[np.ndarray, dict]:
    side = json.loads(path.with_suffix(".json").read_text())
    raw = np.frombuffer(path.read_bytes(), np.uint8).reshape(side["height"], side["width"])
    return raw, side


def green_plane(raw: np.ndarray, cfa: str, black: float) -> np.ndarray:
    lin = (raw.astype(np.float32) - black) / (255.0 - black)
    return demosaic_bilinear(lin, cfa)[..., 1]


def to8(g: np.ndarray) -> np.ndarray:
    """Green scaled to 0..255 by its 99.5th percentile, for tag detection and the edge measure."""
    return np.clip(g / max(float(np.percentile(g, 99.5)), 1e-6) * 255.0, 0, 255).astype(np.float32)


def detect_tags(gray8: np.ndarray, ids: set[int]) -> dict[int, np.ndarray]:
    import cv2

    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_25h9),
                                  cv2.aruco.DetectorParameters())
    corners, found, _ = det.detectMarkers(gray8.astype(np.uint8))
    out = {}
    for q, i in zip(corners if found is not None else [], [] if found is None else found.ravel()):
        if int(i) in ids:
            out[int(i)] = q.reshape(4, 2).astype(float)
    return out


def homography(card: dict, tags: dict[int, np.ndarray]) -> np.ndarray:
    """Canonical card px -> image px, from the tag centres (needs all four)."""
    import cv2

    ids = sorted(int(k) for k in card["tags"])
    src = np.array([card["tags"][i]["center"] for i in ids], np.float64)
    dst = np.array([tags[i].mean(0) for i in ids], np.float64)
    h, _ = cv2.findHomography(src, dst, 0)
    return h


def patch_mask(h: np.ndarray, box, shape: tuple[int, int], inset: float = INSET) -> np.ndarray:
    """Pixels whose centre maps back inside the inset canonical box [x, y, w, h]."""
    x, y, w, hh = (float(v) for v in box)
    x0, x1, y0, y1 = x + inset * w, x + (1 - inset) * w, y + inset * hh, y + (1 - inset) * hh
    corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float)
    img = (h @ np.c_[corners, np.ones(4)].T).T
    img = img[:, :2] / img[:, 2:]
    r0, r1 = max(int(np.floor(img[:, 1].min())), 0), min(int(np.ceil(img[:, 1].max())) + 1, shape[0])
    c0, c1 = max(int(np.floor(img[:, 0].min())), 0), min(int(np.ceil(img[:, 0].max())) + 1, shape[1])
    mask = np.zeros(shape, bool)
    if r1 <= r0 or c1 <= c0:
        return mask
    yy, xx = np.mgrid[r0:r1, c0:c1]
    pts = np.c_[xx.ravel(), yy.ravel(), np.ones(xx.size)].T.astype(float)
    back = np.linalg.inv(h) @ pts
    bx, by = back[0] / back[2], back[1] / back[2]
    inside = (bx >= x0) & (bx < x1) & (by >= y0) & (by < y1)
    mask[r0:r1, c0:c1] = inside.reshape(xx.shape)
    return mask


def channel_stats(stack: np.ndarray, mask: np.ndarray, cfa: str, black: float) -> dict[str, dict]:
    """Mean, temporal noise, SNR and same-channel neighbour noise correlation per CFA channel.

    ``stack`` is (frames, H, W) raw DN. Temporal noise = RMS over pixels of the per-pixel std over frames (ddof 1).
    Neighbour correlation = Pearson r of the per-frame residuals (frame - per-pixel mean) at (y, x) and (y, x + 2).
    """
    stack = stack.astype(np.float64)
    resid = stack - stack.mean(0, keepdims=True)
    out = {}
    for ch, offs in cfa_offsets(cfa).items():
        site = np.zeros(mask.shape, bool)
        for dy, dx in offs:
            site[dy::2, dx::2] = True
        sel = mask & site
        n = int(sel.sum())
        if n < 20:
            out[ch] = {"n_px": n}
            continue
        px = stack[:, sel]
        mean = float(px.mean()) - black
        noise = float(np.sqrt(np.mean(px.var(0, ddof=1))))
        right = np.zeros_like(sel)
        right[:, :-2] = sel[:, :-2] & sel[:, 2:]
        a = resid[:, right].ravel()
        b = resid[:, np.roll(right, 2, axis=1)].ravel()
        r = float(np.corrcoef(a, b)[0, 1]) if a.size > 20 and a.std() > 0 and b.std() > 0 else None
        out[ch] = {"n_px": n, "mean_dn": round(mean, 3), "noise_dn": round(noise, 4),
                   "snr": round(mean / noise, 2) if noise > 0 else None,
                   "neighbour_r": None if r is None else round(r, 3),
                   "clip255_frac": round(float((px >= 255).mean()), 5), "zero_frac": round(float((px <= 0).mean()), 5)}
    return out


def fit_line(t: list[float], y: list[float]) -> dict:
    """Least-squares y = a + b t, with the max residual as a fraction of the largest y."""
    t_, y_ = np.asarray(t, float), np.asarray(y, float)
    b, a = np.polyfit(t_, y_, 1)
    res = y_ - (a + b * t_)
    return {"intercept": round(float(a), 3), "slope_per_ms": round(float(b), 4),
            "max_resid_frac": round(float(np.abs(res).max() / max(abs(y_).max(), 1e-9)), 4)}


def analyse_set(name: str, files: list[Path], card: dict, black: float | None) -> dict:
    ids = {int(k) for k in card["tags"]}
    edge_mm = float(card["physical_mm"]["tag_edge"])
    boxes = {p["id"]: p["box"] for p in card["patches"]}
    raws, sides, rises_px, rises_mm, edge_px, tags_found = [], [], [], [], [], []
    h0 = None
    for f in files:
        raw, side = load_frame(f)
        blk = float(side.get("black_level", 0) if black is None else black)
        g8 = to8(green_plane(raw, side["cfa"], blk))
        tags = detect_tags(g8, ids)
        tags_found.append(sorted(tags))
        for q in tags.values():
            for s in es.tag_rises(g8, q, edge_mm):
                if s["ok"]:
                    rises_px.append(s["rise_px"])
                    rises_mm.append(s["rise_mm"])
                edge_px.append(s["edge_px"])
        if h0 is None and len(tags) == 4:
            h0 = homography(card, tags)
        raws.append(raw)
        sides.append(side)
    side = sides[0]
    blk = float(side.get("black_level", 0) if black is None else black)
    out = {"set": name, "frames": len(files), "width": side["width"], "height": side["height"], "cfa": side["cfa"],
           "exposure_us": sorted({s["exposure_us"] for s in sides}), "gain_db": sorted({s["gain_db"] for s in sides}),
           "black_used": blk, "tags_found": tags_found,
           "tag_edge_px_median": round(float(np.median(edge_px)), 2) if edge_px else None,
           "rise_px": es._summary(rises_px), "rise_mm_on_card": es._summary(rises_mm), "patches": {}}
    if h0 is None:
        out["error"] = "card not located (fewer than 4 tags on every frame)"
        return out
    stack = np.stack(raws)
    for pid in GREYS:
        if pid in boxes:
            out["patches"][pid] = channel_stats(stack, patch_mask(h0, boxes[pid], stack.shape[1:]), side["cfa"], blk)
    out["homography"] = h0.round(6).tolist()
    return out


def main(argv: list[str] | None = None) -> int:
    import yaml

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--set", nargs=3, action="append", metavar=("NAME", "DIR", "GLOB"), required=True)
    ap.add_argument("--card", default=str(ROOT / "configs/cards/nereus_v3_c1.yaml"))
    ap.add_argument("--black", type=float, default=None, help="black level DN (default: the sidecar's)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    card = yaml.safe_load(open(a.card))
    sets = []
    for name, d, glob in a.set:
        files = sorted(Path(d).glob(glob))
        if not files:
            print(f"n6_raw_compare: no files for {name}: {d}/{glob}", file=sys.stderr)
            return 1
        sets.append(analyse_set(name, files, card, a.black))
        s = sets[-1]
        print(json.dumps({k: s.get(k) for k in ("set", "frames", "exposure_us", "tag_edge_px_median",
                                                 "rise_mm_on_card", "error")}), flush=True)
    out = {"card": Path(a.card).name, "sets": sets,
           "limits": "frame centre only (card position); 8-bit Bayer; black level from the sidecar unless --black"}
    Path(a.out).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
