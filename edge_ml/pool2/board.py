"""The W1 board in board millimetres -> image pixels, through the tag homography (spec rev 13).

Rev 13: "pixels across each 80 mm and 50 mm sample, taken from the tag homography and the samples'
measured positions on the board ... never drawn by hand"; the detector's reference box is "each
sample's outline projected through the same tag homography". Board coordinates are mm, x right and
y down, as the board faces the camera upright (the card's canonical layout / 10, configs/cards/).
Also the rev 13 fit check: is the whole board inside a camera's frame at a given range.
"""

from __future__ import annotations

import math

import cv2
import numpy as np


def tag_corners_mm(cx: float, cy: float, edge: float) -> np.ndarray:
    """A tag's black-square corners in board mm, in OpenCV's order (TL, TR, BR, BL)."""
    h = edge / 2.0
    return np.array([[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]],
                    dtype=float)


def homography(tags_img: dict[int, np.ndarray],
               tags_mm: dict[int, np.ndarray]) -> np.ndarray | None:
    """Board mm -> image px from every corner of every tag found in both (>= 1 tag = 4 points)."""
    ids = [i for i in tags_img if i in tags_mm]
    if not ids:
        return None
    src = np.concatenate([tags_mm[i] for i in ids]).astype(np.float32)
    dst = np.concatenate([tags_img[i] for i in ids]).astype(np.float32)
    H, _ = cv2.findHomography(src, dst, 0)
    return H


def project(H: np.ndarray, pts_mm: np.ndarray) -> np.ndarray:
    return cv2.perspectiveTransform(np.asarray(pts_mm, dtype=np.float64).reshape(-1, 1, 2),
                                    H).reshape(-1, 2)


def sample_box(H: np.ndarray, centre_mm: tuple[float, float], diameter_mm: float,
               n: int = 72) -> dict:
    """A round sample's outline projected: its bounding box [x1, y1, x2, y2] (the detector's
    reference box) and px across = the
    SHORTEST projected diameter (spec rev 18 item 10: strict, the 30 px gate), px_across_max the
    longest (reported beside it)."""
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    r = diameter_mm / 2.0
    outline = np.c_[centre_mm[0] + r * np.cos(a), centre_mm[1] + r * np.sin(a)]
    uv = project(H, outline)
    half = n // 2
    diam = np.linalg.norm(uv[:half] - uv[half:], axis=1)
    across, across_max = float(np.min(diam)), float(np.max(diam))
    x1, y1 = uv.min(axis=0)
    x2, y2 = uv.max(axis=0)
    return {"box": [float(x1), float(y1), float(x2), float(y2)], "px_across": across,
            "px_across_max": across_max}


def iou(a: list[float], b: list[float]) -> float:
    """Intersection over union of two [x1, y1, x2, y2] boxes."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def board_fits(K: np.ndarray, width: int, height: int, outline_mm: tuple[float, float],
               depth_m: float, offset_mm: tuple[float, float] = (0.0, 0.0)) -> bool:
    """Rev 13 fit check: a face-on board of outline_mm (w, h), its centre offset_mm (x, y) from this
    camera's optical axis at depth_m, lies wholly inside the width x height frame (no
    distortion)."""
    w, h = outline_mm
    z = depth_m * 1000.0
    corners = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]])
    corners = corners + np.asarray(offset_mm, dtype=float)
    u = K[0, 0] * corners[:, 0] / z + K[0, 2]
    v = K[1, 1] * corners[:, 1] / z + K[1, 2]
    return bool((u >= 0).all() and (u <= width).all() and (v >= 0).all() and (v <= height).all())


def first_fit_bin(K: np.ndarray, width: int, height: int, outline_mm: tuple[float, float],
                  offset_mm: tuple[float, float], bins: tuple[float, ...]) -> float | None:
    """The first bin centre where the whole board fits (the camera's W1 chain start, rev 13)."""
    return next((d for d in bins if board_fits(K, width, height, outline_mm, d, offset_mm)), None)
