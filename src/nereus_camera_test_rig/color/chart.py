"""Tag-less colour charts beside the card (Phase 8 S4) — config + finder.

A chart (e.g. the 24-patch chart on the ``nereus002`` bench) has no AprilTags. It is taped flat
next to the card, so it lies in the card's plane: its patches are found in the card's canonical
frame (the homography from ``locate``) inside a search ``region`` given by the session config.

Finder, per frame: warp the binned RAW (luminance, relative to the card's white patch) onto the
canonical ``region``; the chart's surround is the darkest large area, so everything brighter
than 3x its 5th percentile is a patch candidate. Candidates of the median patch size and square
shape are assigned to grid cells by their pitch, a projective grid (chart cell -> canonical) is
fitted to them, and every patch gets a box at its own candidate centroid when there is one
(keeps lens distortion out of the grid — the N6's barrel bends the chart rows by several px)
and at the fitted grid position otherwise (dark patches the threshold misses). The grid residual
is reported; the finder fails loudly when the found cells do not span the configured rows x
cols.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import load_yaml
from .card import Box

MIN_REL_BG = 0.02  # floor for the candidate threshold, fraction of the card white


@dataclass(frozen=True)
class ChartPatch:
    id: str
    row: int  # 0-based
    col: int
    group: str  # "grey" | "color"
    label: str


@dataclass(frozen=True)
class Chart:
    chart_id: str
    rows: int
    cols: int
    patches: tuple[ChartPatch, ...]
    truth_source: str


def load_chart(path: str | Path) -> Chart:
    data = load_yaml(path)
    rows, cols = int(data["grid"]["rows"]), int(data["grid"]["cols"])
    patches = []
    for r, row in enumerate(data["patches"]):
        if len(row) != cols:
            raise ValueError(f"{path}: row {r} has {len(row)} patches, grid has {cols} cols")
        for c, spec in enumerate(row):
            patches.append(ChartPatch(id=f"chart_r{r + 1}c{c + 1}", row=r, col=c,
                                      group=spec["group"], label=spec["label"]))
    if len(data["patches"]) != rows:
        raise ValueError(f"{path}: {len(data['patches'])} rows, grid has {rows}")
    return Chart(chart_id=data["chart_id"], rows=rows, cols=cols, patches=tuple(patches),
                 truth_source=str(data["truth"]["source"]))


def _canvas(binned: np.ndarray, H: np.ndarray, white, region) -> tuple[np.ndarray, np.ndarray]:
    x0, y0, x1, y1 = region
    T = np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], dtype=np.float64)
    img = (binned / np.asarray(white, dtype=np.float64)).mean(axis=2).astype(np.float32)
    size = (int(x1 - x0), int(y1 - y0))
    flags = cv2.WARP_INVERSE_MAP
    can = cv2.warpPerspective(img, H @ T, size, flags=flags | cv2.INTER_LINEAR)
    valid = cv2.warpPerspective(np.ones_like(img), H @ T, size, flags=flags | cv2.INTER_NEAREST)
    return can, valid > 0


def _candidates(can: np.ndarray, valid: np.ndarray) -> tuple[list, float]:
    bg = float(np.percentile(can[valid], 5))
    mask = ((can > max(3 * bg, MIN_REL_BG)) & valid).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, _, stats, cent = cv2.connectedComponentsWithStats(mask)
    blobs = [(float(cent[i][0]), float(cent[i][1]), int(stats[i][2]), int(stats[i][3]))
             for i in range(1, n)
             if stats[i][4] > 0.75 * stats[i][2] * stats[i][3]
             and 0.7 < stats[i][2] / stats[i][3] < 1.4 and min(stats[i][2:4]) >= 20]
    if not blobs:
        return [], bg
    w, h = np.median([b[2] for b in blobs]), np.median([b[3] for b in blobs])
    return [b for b in blobs if abs(b[2] / w - 1) < 0.2 and abs(b[3] / h - 1) < 0.2], bg


def find_chart(binned: np.ndarray, H: np.ndarray, white, chart: Chart,
               region: tuple[float, float, float, float]) -> dict[str, Any]:
    """Patch boxes of ``chart`` in canonical px. ``H`` maps canonical → ``binned`` px;
    ``white`` is the card's white patch mean (binned linear RGB). Raises ``ValueError``."""
    can, valid = _canvas(binned, H, white, region)
    blobs, bg = _candidates(can, valid)
    if len(blobs) < max(4, chart.rows * chart.cols // 2):
        raise ValueError(f"chart: {len(blobs)} clean patch candidates in region {region} "
                         f"(background {bg:.3f} of card white)")
    xy = np.array([b[:2] for b in blobs])
    # pitch = median distance to the nearest other candidate; cells relative to the top-left
    d = np.linalg.norm(xy[:, None] - xy[None], axis=2) + np.eye(len(xy)) * 1e9
    pitch = float(np.median(d.min(axis=1)))
    col = np.round((xy[:, 0] - xy[:, 0].min()) / pitch).astype(int)
    row = np.round((xy[:, 1] - xy[:, 1].min()) / pitch).astype(int)
    if col.max() != chart.cols - 1 or row.max() != chart.rows - 1:
        raise ValueError(f"chart: candidates span {row.max() + 1} x {col.max() + 1} cells, "
                         f"expected {chart.rows} x {chart.cols}")
    G, _ = cv2.findHomography(np.stack([col, row], 1).astype(np.float64), xy)
    grid = cv2.perspectiveTransform(
        np.array([[[p.col, p.row]] for p in chart.patches], dtype=np.float64), G).reshape(-1, 2)
    at = {(int(r), int(c)): tuple(p) for r, c, p in zip(row, col, xy)}
    fit = cv2.perspectiveTransform(np.stack([col, row], 1)[:, None].astype(np.float64),
                                   G).reshape(-1, 2)
    w = float(np.median([b[2] for b in blobs]))
    h = float(np.median([b[3] for b in blobs]))
    x0, y0 = region[:2]
    boxes, source = {}, {}
    for p, g in zip(chart.patches, grid):
        cx, cy = at.get((p.row, p.col), g)
        boxes[p.id] = Box(x=cx + x0 - w / 2, y=cy + y0 - h / 2, w=w, h=h)
        source[p.id] = "blob" if (p.row, p.col) in at else "grid"
    return {"boxes": boxes, "source": source, "n_found": len(blobs), "pitch_px": round(pitch, 1),
            "patch_px": [round(w, 1), round(h, 1)], "background_rel": round(bg, 4),
            "grid_rms_px": round(float(np.sqrt(np.mean(np.sum((fit - xy) ** 2, 1)))), 2)}
