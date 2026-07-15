"""Card crop generation — Spec §13.

Produces the rectified card crop from a localized card and writes it to disk.
Kept separate from localization so the geometry stays pure and testable.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .reference_card import CardLocation, rectify


def make_card_crop(
    image: np.ndarray,
    location: CardLocation,
    out_w: int,
    out_h: int,
) -> np.ndarray:
    """Return the rectified (upright) card image at ``out_w × out_h``."""
    return rectify(image, location.card_quad, out_w, out_h)


def save_image(image: np.ndarray, path: str | Path) -> tuple[int, int, int]:
    """Write ``image`` to ``path``; return (width, height, size_bytes).

    Raises ``IOError`` if the crop is empty or the write produced no bytes — a crop
    that "succeeds" but is empty is a failure (CLAUDE.md §19).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if image is None or image.size == 0:
        raise IOError(f"refusing to save empty image to {p}")
    if not cv2.imwrite(str(p), image):
        raise IOError(f"cv2.imwrite failed for {p}")
    size = p.stat().st_size
    if size == 0:
        raise IOError(f"wrote zero bytes to {p}")
    h, w = image.shape[:2]
    return w, h, size
