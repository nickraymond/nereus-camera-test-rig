"""AprilTag detection — Spec §13.

Adapts the proven detector from bm_cam_legacy's ``bm_reference_card_quality_v2.py``:
OpenCV ArUco with the AprilTag dictionary (**not** pupil/dt_apriltags), plus
multi-scale detection (upscale the gray image, keep the scale that finds the most
tags). Requires the *contrib* OpenCV build (``opencv-contrib-python``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

DEFAULT_FAMILY = "DICT_APRILTAG_36h11"
DEFAULT_SCALES = (1, 2, 3, 4)


class DetectorUnavailable(RuntimeError):
    """Raised when cv2.aruco is missing (needs opencv-contrib-python)."""


@dataclass
class TagDetection:
    """One detected tag: id, its 4 corners (native px), center, and min edge length."""

    tag_id: int
    corners: np.ndarray  # shape (4, 2), native-image pixels
    center: tuple[float, float]
    side_px_min: float


@dataclass
class DetectionOutcome:
    """Result of running the detector: tags by id + the scale that won."""

    tags: dict[int, TagDetection] = field(default_factory=dict)
    scale_used: int = 1

    @property
    def tag_ids(self) -> list[int]:
        return sorted(self.tags)


def _require_aruco() -> None:
    if not hasattr(cv2, "aruco"):
        raise DetectorUnavailable(
            "cv2.aruco not available — install opencv-contrib-python (the 'analysis' extra)"
        )


def _load_gray(image: str | Path | np.ndarray) -> np.ndarray:
    if isinstance(image, np.ndarray):
        img = image
    else:
        img = cv2.imread(str(image))
        if img is None:
            raise FileNotFoundError(f"could not read image: {image}")
    if img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def _side_min(corners: np.ndarray) -> float:
    """Minimum edge length of a 4-corner tag (a size/legibility proxy)."""
    pts = corners.reshape(4, 2)
    edges = [float(np.linalg.norm(pts[i] - pts[(i + 1) % 4])) for i in range(4)]
    return min(edges)


def detect_tags(
    image: str | Path | np.ndarray,
    *,
    family: str = DEFAULT_FAMILY,
    scales: tuple[int, ...] = DEFAULT_SCALES,
) -> DetectionOutcome:
    """Detect AprilTags, trying each scale and keeping the best (most tags).

    Corner coordinates are always returned in native (scale-1) image pixels. Ties on
    tag count are broken toward the smallest scale (cheapest, least interpolation).
    """
    _require_aruco()
    gray = _load_gray(image)
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, family))
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_APRILTAG
    detector = cv2.aruco.ArucoDetector(dictionary, params)

    best: DetectionOutcome | None = None
    for scale in scales:
        scaled = (
            gray
            if scale == 1
            else cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        )
        corners, ids, _ = detector.detectMarkers(scaled)
        tags: dict[int, TagDetection] = {}
        if ids is not None:
            for c, i in zip(corners, ids.flatten()):
                native = c.reshape(4, 2) / scale
                center = (float(native[:, 0].mean()), float(native[:, 1].mean()))
                tags[int(i)] = TagDetection(
                    tag_id=int(i),
                    corners=native,
                    center=center,
                    side_px_min=_side_min(native),
                )
        if best is None or len(tags) > len(best.tags):
            best = DetectionOutcome(tags=tags, scale_used=scale)
    return best or DetectionOutcome()
