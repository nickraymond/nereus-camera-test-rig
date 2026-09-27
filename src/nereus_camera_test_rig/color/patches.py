"""Stage ``patches`` — card-patch statistics per located frame (SPEC §4 Phase 8 S1.5).

Patch boxes live in the card's canonical frame (card YAML). The homography from the
canonical tag-centre quad to the tag-centre quad ``locate`` stored maps each box onto the
image — projectively exact for a flat card, unlike the image-space quad expansion used to
rectify crops. Every image pixel whose centre maps back inside the **central 60 %** of a box
is sampled; the same pixels are split into a 3 × 3 grid of cells for the damage test (qc).

**RAW first** (SPEC §20): the measurement image is the 2 × 2-binned RAW (R, mean G, B — no
demosaic), linear, black-subtracted, 0 = black and 1 = white level; clip = any sensor pixel
of the cell at the white level. ``mean_norm`` divides by ``t · ISO / N²`` so frames compare.
The camera JPEG is sampled as well, with the same boxes, as the "before" the scoring
protocol needs (as-shot baseline) — 8-bit sRGB values, with ceiling (255) and floor (0)
fractions. Each result states its source. The TG-7 JPEG is radially remapped from the RAW
(OQ-42), so a JPEG pixel is mapped back to RAW (``locate``'s ``jpeg_from_raw``) before the box
test: both sources sample the same area of the card. Frames the map lists in
``exclude_frames`` get no JPEG sample (``jpeg_excluded`` + reason).

Output: ``patches/{patches.json, summary.json, stage.json}``.
"""

from __future__ import annotations

import csv
import json
import os
from functools import partial
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np

from .card import Box, Card, load_card
from .jpeg_geometry import JpegMap, read_jpeg
from .raw_io import RawFrame, bin2x2, normalize
from .stages import run_parallel, verify_fresh, write_stage

INNER = 0.6
CELLS = 3


def canonical_tag_quad(card: Card) -> np.ndarray:
    """Tag-centre quad (TL, TR, BR, BL) in the canonical frame — the inverse of the Phase 2
    expansion: the expanded quad maps to the canonical corners (0, 0) … (W-1, H-1)."""
    cx, cy = (card.canonical_w - 1) / 2, (card.canonical_h - 1) / 2
    hx, hy = cx / card.expand_x, cy / card.expand_y
    return np.array([[cx - hx, cy - hy], [cx + hx, cy - hy], [cx + hx, cy + hy],
                     [cx - hx, cy + hy]], dtype=np.float64)


def homography(card: Card, quad: np.ndarray) -> np.ndarray:
    """Canonical-frame → image homography from a tag-centre quad in image pixels."""
    return cv2.getPerspectiveTransform(canonical_tag_quad(card).astype(np.float32),
                                       np.asarray(quad, dtype=np.float32))


def mosaic_to_binned(valid_crop: Optional[tuple]) -> np.ndarray:
    """Mosaic px → ``bin2x2`` index: binned (i, j) is centred at mosaic (x0 + 2j + 0.5, …)."""
    x0, y0 = (valid_crop or (0, 0))[:2]
    return np.array([[0.5, 0, -(x0 + 0.5) / 2], [0, 0.5, -(y0 + 0.5) / 2], [0, 0, 1]])


def _r(values, digits: int) -> list:
    return [None if v is None or not np.isfinite(v) else round(float(v), digits)
            for v in values]


def sample(img: np.ndarray, H: np.ndarray, box: Box, clip: Optional[np.ndarray] = None,
           floor: Optional[np.ndarray] = None, digits: int = 6,
           warp: Optional[JpegMap] = None) -> dict[str, Any]:
    """Statistics of the pixels in the central ``INNER`` of ``box`` (canonical px).

    ``img`` is (h, w, 3); ``clip`` / ``floor`` are optional per-channel boolean masks. With
    ``warp``, ``H`` maps the card into RAW-mosaic px and ``img`` is the camera JPEG: box
    outlines go RAW → JPEG and each JPEG pixel goes back to RAW before the inside test.
    """
    x0 = box.x + box.w * (1 - INNER) / 2
    y0 = box.y + box.h * (1 - INNER) / 2
    w1, h1 = box.w * INNER, box.h * INNER
    corners = np.array([[[x0, y0]], [[x0 + w1, y0]], [[x0 + w1, y0 + h1]], [[x0, y0 + h1]]],
                       dtype=np.float64)
    pts = cv2.perspectiveTransform(corners, H).reshape(4, 2)
    outline = pts
    if warp is not None:
        t = np.linspace(0, 1, 9)[:, None]
        outline = warp.to_jpeg(np.concatenate([pts[i] + (pts[(i + 1) % 4] - pts[i]) * t
                                               for i in range(4)]))  # edges bow under the map
        pts = warp.to_jpeg(pts)
    size = [float(np.linalg.norm(pts[1] - pts[0]) + np.linalg.norm(pts[2] - pts[3])) / 2,
            float(np.linalg.norm(pts[3] - pts[0]) + np.linalg.norm(pts[2] - pts[1])) / 2]
    ih, iw = img.shape[:2]
    in_frame = bool(np.all((outline >= -0.5) & (outline <= [iw - 0.5, ih - 0.5])))
    u0, v0 = np.maximum(np.floor(outline.min(axis=0)).astype(int), 0)
    u1, v1 = np.minimum(np.ceil(outline.max(axis=0)).astype(int), [iw - 1, ih - 1])
    out: dict[str, Any] = {"n_px": 0, "size_px": _r(size, 1), "in_frame": in_frame}
    if u1 < u0 or v1 < v0:
        return out
    uu, vv = np.meshgrid(np.arange(u0, u1 + 1), np.arange(v0, v1 + 1))
    img_px = np.stack([uu, vv], axis=-1).reshape(-1, 2).astype(np.float64)
    if warp is not None:
        img_px = warp.to_raw(img_px)
    canon = cv2.perspectiveTransform(img_px.reshape(-1, 1, 2), np.linalg.inv(H)).reshape(-1, 2)
    fx, fy = (canon[:, 0] - x0) / w1, (canon[:, 1] - y0) / h1
    inside = (fx >= 0) & (fx < 1) & (fy >= 0) & (fy < 1)
    if not inside.any():
        return out
    rows, cols = vv.ravel()[inside], uu.ravel()[inside]
    px = img[rows, cols].astype(np.float64)
    cell = (np.minimum((fy[inside] * CELLS).astype(int), CELLS - 1) * CELLS
            + np.minimum((fx[inside] * CELLS).astype(int), CELLS - 1))
    counts = np.bincount(cell, minlength=CELLS * CELLS)
    sums = np.stack([np.bincount(cell, px[:, c], CELLS * CELLS) for c in range(3)], axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        cell_means = sums / counts[:, None]
    out.update(n_px=int(inside.sum()), mean=_r(px.mean(axis=0), digits),
               std=_r(px.std(axis=0), digits),
               cell_n=counts.reshape(CELLS, CELLS).tolist(),
               cells=[[_r(cell_means[r * CELLS + c], digits) for c in range(CELLS)]
                      for r in range(CELLS)])
    if clip is not None:
        out["clip_frac"] = _r(clip[rows, cols].mean(axis=0), 4)
    if floor is not None:
        out["floor_frac"] = _r(floor[rows, cols].mean(axis=0), 4)
    return out


def _boxes(card: Card) -> dict[str, Box]:
    return {**{p.id: p.box for p in card.patches}, **{s.id: s.box for s in card.sub_patches}}


def sample_raw(frame: RawFrame, quad_raw, card: Card) -> dict[str, Any]:
    linear, saturated, cfa = normalize(frame)
    binned, clip = bin2x2(linear, cfa, saturated)
    H = mosaic_to_binned(frame.valid_crop) @ homography(card, np.asarray(quad_raw))
    k = frame.exposure_factor()
    result = {}
    for pid, box in _boxes(card).items():
        stats = sample(binned, H, box, clip=clip)
        if stats.get("mean"):
            stats["mean_norm"] = _r(np.asarray(stats["mean"]) / k, 6)
        result[pid] = stats
    return {"source": "raw_binned_linear", "exposure_factor": round(k, 8),
            "black_level": list(frame.black_level), "white_level": frame.white_level,
            "binned_shape": list(binned.shape[:2]), "patches": result}


def sample_jpeg(path: Path, quad_raw, card: Card, jpeg_map: JpegMap) -> dict[str, Any]:
    """Camera JPEG patches on the card area of the RAW tag-centre quad (see ``sample``)."""
    bgr = read_jpeg(path)
    if bgr is None:
        raise IOError(f"cannot read {path}")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    H = homography(card, np.asarray(quad_raw))
    return {"source": "camera_jpeg_srgb8", "geometry": "raw quad through jpeg_from_raw",
            "patches": {pid: sample(rgb, H, box, clip=rgb >= 255, floor=rgb <= 0, digits=3,
                                    warp=jpeg_map)
                        for pid, box in _boxes(card).items()}}


def sample_frame(raw: Optional[Path], jpeg: Optional[Path], quad_raw, stem: str, card: Card,
                 raw_reader: Optional[Callable[[Path], RawFrame]],
                 jpeg_map: JpegMap) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        if raw is not None and raw_reader is not None:
            out["raw"] = sample_raw(raw_reader(raw), quad_raw, card)
        if jpeg is not None and stem in jpeg_map.exclude_frames:
            out["jpeg_excluded"] = jpeg_map.exclude_frames[stem]
        elif jpeg is not None:
            out["jpeg"] = sample_jpeg(jpeg, quad_raw, card, jpeg_map)
    except (OSError, ValueError, RuntimeError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def patches(locate_dir: Path, card_path: Path,
            raw_reader: Optional[Callable[[Path], RawFrame]] = None,
            workers: int | None = None) -> dict[str, Any]:
    """Sample every patch + sub-patch of every located frame (RAW and camera JPEG)."""
    jmap = JpegMap.from_config(verify_fresh(locate_dir)["params"])
    ingest_dir = locate_dir.parent / "ingest"
    dataset_dir = Path(verify_fresh(ingest_dir)["params"]["dataset_dir"])
    rows = {r["stem"]: r for r in csv.DictReader((ingest_dir / "manifest.csv").open())}
    corners = json.loads((locate_dir / "corners.json").read_text())
    card = load_card(card_path)

    stems = [s for s, rec in corners.items() if rec.get("located")]
    jobs = []
    for s in stems:
        r = rows[s]
        raw = dataset_dir / r["file"] if r["has_raw"] == "True" else None
        jpeg = dataset_dir / r["jpeg"] if r["jpeg"] else None
        jobs.append((raw, jpeg, corners[s]["quad_raw"], s))
    fn = partial(sample_frame, card=card, raw_reader=raw_reader, jpeg_map=jmap)
    results = dict(zip(stems, run_parallel(fn, jobs, workers or os.cpu_count() or 1)))

    out_dir = locate_dir.parent / "patches"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "patches.json").write_text(json.dumps(results, separators=(",", ":")) + "\n")
    summary = {"located_frames": len(stems),
               "with_raw": sum("raw" in r for r in results.values()),
               "with_jpeg": sum("jpeg" in r for r in results.values()),
               "errors": {s: r["error"] for s, r in results.items() if "error" in r},
               "jpeg_excluded": {s: r["jpeg_excluded"] for s, r in results.items()
                                 if "jpeg_excluded" in r},
               "patches_per_frame": len(_boxes(card)), "inner_fraction": INNER,
               "cells": CELLS}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_stage(out_dir, "patches", configs=[card_path], upstream=[locate_dir],
                params={"inner_fraction": INNER, "cells": CELLS,
                        "raw": "bin2x2 linear" if raw_reader else None,
                        "jpeg_from_raw": jmap.as_dict()})
    summary["out_dir"] = str(out_dir)
    return summary
