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


def infer_card_corners_from_tags(
    outcome: DetectionOutcome, corner_map: dict[str, int] | None = None
) -> np.ndarray:
    """Build the TL,TR,BR,BL quad from the mapped tags' *centers*.

    Requires all four mapped corner tags to be present (V2 has 4 corner tags — the
    legacy 3-tag inference does not apply). Raises ``CardLocalizationError`` otherwise.
    """
    corner_map = corner_map or DEFAULT_CORNER_MAP
    tags = outcome.tags
    missing = [name for name, tid in corner_map.items() if tid not in tags]
    if missing:
        raise CardLocalizationError(
            f"missing corner tags {missing} (need ids {sorted(corner_map.values())}, "
            f"found {outcome.tag_ids})"
        )
    tl = np.array(tags[corner_map["tl"]].center)
    tr = np.array(tags[corner_map["tr"]].center)
    bl = np.array(tags[corner_map["bl"]].center)
    br = np.array(tags[corner_map["br"]].center)
    return np.array([tl, tr, br, bl], dtype=np.float32)  # TL,TR,BR,BL


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
) -> CardLocation:
    """Compute the card boundary quad from detected tags (tag-center quad → expanded)."""
    corner_map = corner_map or DEFAULT_CORNER_MAP
    tag_quad = infer_card_corners_from_tags(outcome, corner_map)
    card_quad = expand_quad(tag_quad, expand_x, expand_y)
    return CardLocation(tag_quad=tag_quad, card_quad=card_quad, corner_ids=dict(corner_map))
