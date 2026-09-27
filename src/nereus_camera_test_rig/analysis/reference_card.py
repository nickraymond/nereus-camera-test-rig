"""Reference-card localization + rectify — Spec §13.

Adapts the pure geometry functions from bm_cam_legacy's
``bm_reference_card_quality_v2.py`` (``infer_card_corners_from_tags``,
``expand_quad``, ``rectify_quad``), re-parameterized for the **V2** card whose
canonical geometry is recorded in
``tests/fixtures/reference_card/template_layout.json``:

- corner map ``tl:0, tr:1, bl:2, br:3``
- expand factors ``card_expand_x = 1.25``, ``card_expand_y = 2.0``
- canonical rectified size ``3000 × 1000``
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .apriltag_detector import DetectionOutcome

# V2 canonical geometry (see fixture template_layout.json).
DEFAULT_CORNER_MAP = {"tl": 0, "tr": 1, "bl": 2, "br": 3}
DEFAULT_EXPAND_X = 1.25
DEFAULT_EXPAND_Y = 2.0
DEFAULT_RECTIFIED_W = 3000
DEFAULT_RECTIFIED_H = 1000


class CardLocalizationError(ValueError):
    """Raised when the card boundary cannot be computed from the detected tags."""


@dataclass
class CardLocation:
    """A localized card: the ordered tag-center quad and the expanded card quad."""

    tag_quad: np.ndarray  # (4,2) TL,TR,BR,BL from tag centers
    card_quad: np.ndarray  # (4,2) TL,TR,BR,BL after expansion
    corner_ids: dict[str, int]
    inferred: tuple[str, ...] = ()  # corners inferred from the other three (min_tags=3)


# Parallelogram completion: the missing corner = its two neighbours minus the opposite one.
_OPPOSITE = {"tl": ("tr", "bl", "br"), "tr": ("tl", "br", "bl"),
             "bl": ("tl", "br", "tr"), "br": ("tr", "bl", "tl")}


def infer_card_corners_from_tags(
    outcome: DetectionOutcome,
    corner_map: dict[str, int] | None = None,
    min_tags: int = 4,
) -> tuple[np.ndarray, tuple[str, ...]]:
    """Build the TL,TR,BR,BL quad from the mapped tags' *centers*; return (quad, inferred).

    By default all four corner tags are required (Phase 2–6 behaviour). With ``min_tags=3``
    a single missing corner is inferred as a parallelogram — ported from bm_cam_legacy's
    ``infer_card_corners_from_tags`` (exact under an affine view, approximate under strong
    perspective). Raises ``CardLocalizationError`` when too few tags are present.
    """
    if min_tags not in (3, 4):
        raise ValueError(f"min_tags must be 3 or 4, got {min_tags}")
    corner_map = corner_map or DEFAULT_CORNER_MAP
    tags = outcome.tags
    centers = {name: np.array(tags[tid].center, dtype=np.float64)
               for name, tid in corner_map.items() if tid in tags}
    missing = [name for name in corner_map if name not in centers]
    if len(centers) < min_tags or len(missing) > 1:
        raise CardLocalizationError(
            f"missing corner tags {missing} (need ids {sorted(corner_map.values())}, "
            f"found {outcome.tag_ids}, min_tags={min_tags})"
        )
    for name in missing:
        a, b, opposite = _OPPOSITE[name]
        centers[name] = centers[a] + centers[b] - centers[opposite]
    quad = np.array([centers[k] for k in ("tl", "tr", "br", "bl")], dtype=np.float32)
    return quad, tuple(missing)


def expand_quad(quad: np.ndarray, scale_x: float, scale_y: float) -> np.ndarray:
    """Expand a TL,TR,BR,BL quad outward from its center along its own axes."""
    quad = quad.astype(np.float32)
    center = quad.mean(axis=0)
    tl, tr, br, bl = quad
    x_axis = ((tr - tl) + (br - bl)) / 2.0
    y_axis = ((bl - tl) + (br - tr)) / 2.0
    hx, hy = x_axis / 2.0, y_axis / 2.0
    out = np.array(
        [
            center - hx * scale_x - hy * scale_y,  # TL
            center + hx * scale_x - hy * scale_y,  # TR
            center + hx * scale_x + hy * scale_y,  # BR
            center - hx * scale_x + hy * scale_y,  # BL
        ],
        dtype=np.float32,
    )
    return out


def rectify(image: np.ndarray, quad: np.ndarray, out_w: int, out_h: int) -> np.ndarray:
    """Perspective-warp the card quad to an ``out_w × out_h`` upright rectangle."""
    dst = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], dtype=np.float32
    )
    matrix = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    return cv2.warpPerspective(image, matrix, (out_w, out_h), flags=cv2.INTER_CUBIC)


def localize_card(
    outcome: DetectionOutcome,
    *,
    corner_map: dict[str, int] | None = None,
    expand_x: float = DEFAULT_EXPAND_X,
    expand_y: float = DEFAULT_EXPAND_Y,
    min_tags: int = 4,
) -> CardLocation:
    """Compute the card boundary quad from detected tags (tag-center quad → expanded)."""
    corner_map = corner_map or DEFAULT_CORNER_MAP
    tag_quad, inferred = infer_card_corners_from_tags(outcome, corner_map, min_tags)
    card_quad = expand_quad(tag_quad, expand_x, expand_y)
    return CardLocation(tag_quad=tag_quad, card_quad=card_quad, corner_ids=dict(corner_map),
                        inferred=inferred)
