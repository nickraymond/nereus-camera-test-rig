"""RAW frame contract + L0 decode — SPEC §4 Phase 8 S0, brief §5.2.

``RawFrame`` is what every reader produces (DNG via ``tifffile``, OpenMV Bayer, and the
Mac-only ORF reader in ``host_tools/tg7/``), so everything downstream is reader-agnostic.
L0 turns it into linear camera RGB two ways:

- **measurement path** — ``bin2x2``: one RGB value per 2×2 CFA cell (R, mean of the two
  G, B). No demosaic interpolation, better SNR; all card-patch sampling uses this.
- **image path** — ``demosaic_bilinear``: full-resolution RGB for output images.

Values stay linear and **unclipped below zero** after black subtraction, so patch means on
dark patches are not biased upward; saturated pixels are reported in a separate mask.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import cv2
import numpy as np

PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")
_CHANNEL = {"R": 0, "G": 1, "B": 2}


def cfa_shift(cfa: str, dx: int, dy: int) -> str:
    """The CFA pattern seen at mosaic offset (dx, dy) — e.g. cropping RGGB by x=1 gives GRBG."""
    grid = [[cfa[0], cfa[1]], [cfa[2], cfa[3]]]
    return "".join(grid[(r + dy) % 2][(c + dx) % 2] for r in (0, 1) for c in (0, 1))


@dataclass(frozen=True)
class RawFrame:
    """One RAW capture: the Bayer mosaic plus what is needed to linearize it.

    ``black_level`` is per CFA *position* in 2×2 row-major order ([0,0], [0,1], [1,0],
    [1,1]) of the full mosaic — unambiguous, unlike per-"channel" orders that differ
    between formats. Readers convert to this order.
    """

    mosaic: np.ndarray  # (H, W) integer sensor counts, full readout
    cfa: str  # pattern at mosaic[0, 0], one of PATTERNS
    black_level: tuple[float, float, float, float]
    white_level: float
    valid_crop: Optional[tuple[int, int, int, int]] = None  # x, y, w, h of the active area
    exposure_s: Optional[float] = None
    iso: Optional[float] = None
    fnumber: Optional[float] = None
    as_shot_wb: Optional[tuple[float, float, float]] = None  # R, G, B multipliers (G = 1)
    color_matrix: Optional[np.ndarray] = None  # 3×3, meaning recorded in source["color_matrix"]
    source: dict[str, Any] = field(default_factory=dict)  # reader name, path, raw tags

    def __post_init__(self) -> None:
        if self.mosaic.ndim != 2 or not np.issubdtype(self.mosaic.dtype, np.integer):
            raise ValueError(f"mosaic must be a 2-D integer array, got {self.mosaic.dtype} "
                             f"{self.mosaic.shape}")
        if self.cfa not in PATTERNS:
            raise ValueError(f"cfa must be one of {PATTERNS}, got {self.cfa!r}")
        if len(self.black_level) != 4 or max(self.black_level) >= self.white_level:
            raise ValueError(f"need 4 black levels below white {self.white_level}, "
                             f"got {self.black_level}")
        if self.valid_crop is not None:
            x, y, w, h = self.valid_crop
            H, W = self.mosaic.shape
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > W or y + h > H:
                raise ValueError(f"valid_crop {self.valid_crop} outside mosaic {W}x{H}")

    def active(self) -> tuple[np.ndarray, str, np.ndarray]:
        """(mosaic, cfa, 2×2 black grid) of the valid area, with the CFA phase adjusted."""
        x, y, w, h = self.valid_crop or (0, 0, self.mosaic.shape[1], self.mosaic.shape[0])
        black = np.asarray(self.black_level, dtype=np.float32).reshape(2, 2)
        black = np.roll(black, shift=(-(y % 2), -(x % 2)), axis=(0, 1))
        return self.mosaic[y:y + h, x:x + w], cfa_shift(self.cfa, x, y), black

    def exposure_factor(self) -> float:
        """t · ISO / N² — divide linear values by this to compare frames (brief §7 P1.0)."""
        missing = [k for k in ("exposure_s", "iso", "fnumber") if getattr(self, k) is None]
        if missing:
            raise ValueError(f"cannot exposure-normalize {self.source.get('path', 'frame')}: "
                             f"missing {missing}")
        return self.exposure_s * self.iso / self.fnumber ** 2


def normalize(frame: RawFrame) -> tuple[np.ndarray, np.ndarray, str]:
    """Active area → (linear float32, 0 = black and 1 = white; saturated mask; its CFA)."""
    raw, cfa, black = frame.active()
    h, w = raw.shape
    black_img = np.tile(black, ((h + 1) // 2, (w + 1) // 2))[:h, :w]
    linear = (raw.astype(np.float32) - black_img) / (np.float32(frame.white_level) - black_img)
    return linear, raw >= frame.white_level, cfa


def _positions(cfa: str) -> dict[str, list[tuple[int, int]]]:
    out: dict[str, list[tuple[int, int]]] = {"R": [], "G": [], "B": []}
    for i, color in enumerate(cfa):
        out[color].append((i // 2, i % 2))
    return out


def bin2x2(linear: np.ndarray, cfa: str, saturated: Optional[np.ndarray] = None):
    """2×2 superpixel binning → (H/2, W/2, 3) RGB and, if given, a per-channel clip mask.

    Binned pixel (i, j) is centred at mosaic (x, y) = (2j + 0.5, 2i + 0.5). An odd last
    row/column is dropped.
    """
    h, w = (linear.shape[0] // 2) * 2, (linear.shape[1] // 2) * 2
    pos = _positions(cfa)
    rgb = np.empty((h // 2, w // 2, 3), dtype=np.float32)
    clip = np.zeros((h // 2, w // 2, 3), dtype=bool) if saturated is not None else None
    for color, cells in pos.items():
        c = _CHANNEL[color]
        planes = [linear[r:h:2, s:w:2] for r, s in cells]
        rgb[..., c] = sum(planes) / len(planes)
        if clip is not None:
            for r, s in cells:
                clip[..., c] |= saturated[r:h:2, s:w:2]
    return rgb, clip


# Bilinear kernels. R/B: a native sample keeps itself (other R/B are 2 px away); gaps take
# the mean of 2 or 4 neighbours. G uses the cross: on a Bayer grid a G's *diagonal*
# neighbours are also G, so the full kernel would blur native green samples.
_KERNEL_RB = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], dtype=np.float32)
_KERNEL_G = np.array([[0, 1, 0], [1, 4, 1], [0, 1, 0]], dtype=np.float32)


def demosaic_bilinear(linear: np.ndarray, cfa: str) -> np.ndarray:
    """Full-resolution (H, W, 3) linear RGB by bilinear interpolation.

    Normalized convolution: each channel's samples and its sample mask are filtered with the
    same kernel and divided, which is exact bilinear in the interior and stays correct at
    the borders. Float in, float out — negatives are kept (``cv2.cvtColor`` Bayer codes are
    integer-only and would clip them).
    """
    h, w = linear.shape
    out = np.empty((h, w, 3), dtype=np.float32)
    for color, cells in _positions(cfa).items():
        mask = np.zeros((h, w), dtype=np.float32)
        for r, s in cells:
            mask[r::2, s::2] = 1.0
        kernel = _KERNEL_G if color == "G" else _KERNEL_RB
        num = cv2.filter2D(linear * mask, -1, kernel, borderType=cv2.BORDER_CONSTANT)
        den = cv2.filter2D(mask, -1, kernel, borderType=cv2.BORDER_CONSTANT)
        out[..., _CHANNEL[color]] = num / den
    return out
