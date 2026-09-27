"""RAW ↔ camera-JPEG pixel map (OQ-42).

The TG-7 JPEG is not a plain crop of the RAW: the camera remaps it radially (an in-air lens
correction, applied even though EXIF says ``DistortionCorrection: Off``), so a point moves
outward by ~16 px at 950 px from the centre and ~130 px at 1800 px. Under water the flat port
cancels most of the lens barrel, so there the **RAW** is the near-projective image (card
homography RMS ≈ 1 px vs ≈ 13 px on the JPEG); in air it is the other way round. Either way
the same map links the two, so every card position is kept in RAW coordinates and mapped:

    jpeg = centre_jpeg + (raw − centre_raw) · (k0 + k1 ρ² + k2 ρ⁴),  ρ = |raw − centre_raw| / 1000

It is config, not code: ``jpeg_from_raw`` in the dataset config, fitted and validated by the
``jpeg-map`` stage (``color/jpeg_map.py``) from tag centres found separately on RAW and JPEG.
A config with only ``jpeg_offset_in_raw: [x, y]`` is the plain-crop special case.

JPEG pixel coordinates are in the stored (sensor) orientation: read JPEGs with
``cv2.IMREAD_IGNORE_ORIENTATION`` (``read_jpeg``) — OpenCV otherwise applies the EXIF
Orientation tag, and 2 TG-7 card frames are stored rotated 90°.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

RHO_UNIT = 1000.0  # px; keeps the coefficients readable


def read_jpeg(path: str | Path, flags: int = cv2.IMREAD_COLOR) -> Optional[np.ndarray]:
    """``cv2.imread`` in the stored orientation (EXIF Orientation ignored), same frame as RAW."""
    return cv2.imread(str(path), flags | cv2.IMREAD_IGNORE_ORIENTATION)


@dataclass(frozen=True)
class JpegMap:
    centre_raw: tuple[float, float]
    centre_jpeg: tuple[float, float]
    k: tuple[float, ...] = (1.0,)
    # {stem: reason} — frames whose JPEG does not follow the map; their JPEG is not scored
    exclude_frames: dict[str, str] = field(default_factory=dict)

    @classmethod
    def offset(cls, dx: float, dy: float) -> "JpegMap":
        """Plain crop: JPEG = RAW − (dx, dy)."""
        return cls((0.0, 0.0), (-float(dx), -float(dy)))

    @classmethod
    def from_config(cls, cfg: dict) -> "JpegMap":
        block = cfg.get("jpeg_from_raw")
        if not block:
            return cls.offset(*cfg.get("jpeg_offset_in_raw", (0, 0)))
        return cls(tuple(float(v) for v in block["centre_raw"]),
                   tuple(float(v) for v in block["centre_jpeg"]),
                   tuple(float(v) for v in block["k"]),
                   {str(s): str(r) for s, r in (block.get("exclude_frames") or {}).items()})

    def as_dict(self) -> dict[str, Any]:
        return {"centre_raw": list(self.centre_raw), "centre_jpeg": list(self.centre_jpeg),
                "k": list(self.k), "rho_unit_px": RHO_UNIT,
                "exclude_frames": dict(self.exclude_frames)}

    def _scale(self, r: np.ndarray) -> np.ndarray:
        rho2 = (r / RHO_UNIT) ** 2
        return sum(k * rho2 ** i for i, k in enumerate(self.k))

    def to_jpeg(self, pts) -> np.ndarray:
        """RAW-mosaic px (…, 2) → JPEG px."""
        p = np.asarray(pts, dtype=np.float64)
        d = p - self.centre_raw
        return self.centre_jpeg + d * self._scale(np.linalg.norm(d, axis=-1))[..., None]

    def to_raw(self, pts) -> np.ndarray:
        """JPEG px (…, 2) → RAW-mosaic px (Newton on the radius; the map is monotonic)."""
        v = np.asarray(pts, dtype=np.float64) - self.centre_jpeg
        target = np.linalg.norm(v, axis=-1)
        r = target / self.k[0]
        for _ in range(20):
            rho2 = (r / RHO_UNIT) ** 2
            g = r * self._scale(r) - target
            dg = sum((2 * i + 1) * k * rho2 ** i for i, k in enumerate(self.k))
            step = g / dg
            r = r - step
            if np.all(np.abs(step) < 1e-6):
                break
        with np.errstate(invalid="ignore", divide="ignore"):
            unit = np.where(target[..., None] > 0, v / target[..., None], 0.0)
        return np.asarray(self.centre_raw) + unit * r[..., None]


def _design(raw: np.ndarray, centre: np.ndarray, n_k: int) -> np.ndarray:
    """Least-squares design matrix for (centre_jpeg x, y, k0 … k_{n_k-1}) at a fixed centre."""
    d = raw - centre
    rho2 = (d ** 2).sum(axis=1) / RHO_UNIT ** 2
    a = np.zeros((2 * len(raw), 2 + n_k))
    a[0::2, 0] = 1
    a[1::2, 1] = 1
    for i in range(n_k):
        a[0::2, 2 + i] = d[:, 0] * rho2 ** i
        a[1::2, 2 + i] = d[:, 1] * rho2 ** i
    return a


def _solve(raw, jpeg, centre, n_k):
    a = _design(raw, centre, n_k)
    q = np.linalg.lstsq(a, jpeg.ravel(), rcond=None)[0]
    return q, float(((a @ q - jpeg.ravel()) ** 2).sum())


def fit_map(raw, jpeg, n_k: int = 3, start=None, span: float = 400.0) -> JpegMap:
    """Least-squares radial map from matched points. For a fixed distortion centre the model
    is linear, so the centre is found by a coarse-to-fine grid search around ``start``."""
    raw, jpeg = np.asarray(raw, np.float64), np.asarray(jpeg, np.float64)
    best_c = np.asarray(start if start is not None else raw.mean(axis=0), np.float64)
    step = span / 16
    for _ in range(5):
        grid = [best_c + (dx, dy) for dx in np.arange(-span, span + step / 2, step)
                for dy in np.arange(-span, span + step / 2, step)]
        best_c = min(grid, key=lambda c: _solve(raw, jpeg, c, n_k)[1])
        span, step = 2 * step, step / 4
    q, _ = _solve(raw, jpeg, best_c, n_k)
    return JpegMap(tuple(best_c.tolist()), (float(q[0]), float(q[1])),
                   tuple(float(v) for v in q[2:]))


def fit_robust(raw, jpeg, n_k: int = 3, start=None, rounds: int = 3,
               floor_px: float = 3.0) -> tuple[JpegMap, np.ndarray, np.ndarray]:
    """``fit_map`` with outlier trimming (error > max(5 × median, ``floor_px``)).
    Returns (map, inlier mask, per-point error in JPEG px)."""
    raw, jpeg = np.asarray(raw, np.float64), np.asarray(jpeg, np.float64)
    keep = np.ones(len(raw), bool)
    for _ in range(rounds):
        m = fit_map(raw[keep], jpeg[keep], n_k, start)
        err = np.linalg.norm(m.to_jpeg(raw) - jpeg, axis=1)
        keep = err < max(5 * float(np.median(err[keep])), floor_px)
    return m, keep, err
