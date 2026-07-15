"""Image-quality metrics — Spec §13 (secondary, non-blocking).

Sharpness / contrast / clipping on the rectified card. These are *optional*
comparison metrics (Spec §13 says they are not bring-up blockers), but they are the
raw material for the down-select (Spec §17), so we compute them when a crop exists.
"""

from __future__ import annotations

import cv2
import numpy as np


def variance_of_laplacian(gray: np.ndarray) -> float:
    """Focus/sharpness proxy: variance of the Laplacian (higher = sharper)."""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def contrast_p95_p05(gray: np.ndarray) -> float:
    """Global contrast as the 95th–5th percentile luminance spread."""
    p5, p95 = np.percentile(gray, [5, 95])
    return float(p95 - p5)


def clipping_fractions(gray: np.ndarray, dark: int = 3, bright: int = 252) -> tuple[float, float]:
    """Fraction of pixels clipped dark (≤dark) and bright (≥bright)."""
    total = gray.size
    if total == 0:
        return 0.0, 0.0
    return (
        float((gray <= dark).sum() / total),
        float((gray >= bright).sum() / total),
    )


def card_metrics(card_bgr: np.ndarray) -> dict[str, float]:
    """Compute the standard metric set for a rectified card crop."""
    gray = cv2.cvtColor(card_bgr, cv2.COLOR_BGR2GRAY) if card_bgr.ndim == 3 else card_bgr
    dark, bright = clipping_fractions(gray)
    return {
        "sharpness_laplacian_var": variance_of_laplacian(gray),
        "contrast_p95_p05": contrast_p95_p05(gray),
        "clipped_dark_frac": dark,
        "clipped_bright_frac": bright,
        "mean_luma": float(gray.mean()),
    }
