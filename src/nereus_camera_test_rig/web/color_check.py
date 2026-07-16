"""Reference-card color-patch check — view-time analysis for the web UI (Phase 6).

Samples the grey ramp + color patches from a rectified card crop and reports how
far each patch is from the card's design values. Runs read-only at view time so
it needs no change to the shared capture/analysis pipeline (the Phase 7 session
depends on that code); if it proves valuable it can graduate into
``analysis/image_metrics.py`` later.

Patch geometry + expected RGB values were measured from the V2 design file
(``tests/fixtures/reference_card/Nereus_Reef_Reference_Card_V2.png``) run through
the real rectification pipeline into the canonical 3000x1000 crop — every sampled
box came back perfectly flat (std 0.0), so these are the printed design values.
Crops from real cameras are always rectified to the same canonical frame
(``analysis/reference_card.py`` DEFAULT_RECTIFIED_W/H), so fixed coordinates
scaled by the actual crop size line up patch-for-patch.

Assumption (documented per CLAUDE.md §16): the physical printed card reproduces
these sRGB values faithfully. Absolute print/lighting error is common to all
three cameras in an experiment, so *relative* deltas remain a fair comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Canonical rectified crop size the coordinates below are expressed in.
CANONICAL_W = 3000
CANONICAL_H = 1000

# Half-size of the sampled box around each patch center, in canonical pixels.
SAMPLE_HALF = 45

# name, center_x, center_y, expected sRGB (measured from the V2 design, see above)
GREY_PATCHES = [
    ("white", 300, 518, (255, 255, 255)),
    ("grey 200", 516, 483, (200, 200, 200)),
    ("grey 128", 726, 483, (128, 128, 128)),
    ("grey 74", 936, 483, (74, 74, 74)),
    ("black", 1146, 483, (0, 0, 0)),
]
COLOR_PATCHES = [
    ("cream", 1703, 372, (255, 249, 230)),
    ("sand", 1857, 372, (239, 212, 167)),
    ("tan", 2010, 372, (218, 169, 110)),
    ("amber", 2164, 372, (215, 146, 53)),
    ("sienna", 2318, 372, (184, 109, 42)),
    ("brown", 2472, 372, (99, 55, 26)),
    ("red-orange", 1703, 585, (240, 74, 24)),
    ("yellow", 1857, 585, (248, 185, 30)),
    ("green", 2010, 585, (109, 176, 79)),
    ("cyan", 2164, 585, (92, 201, 208)),
    ("blue", 2318, 585, (12, 102, 199)),
    ("magenta", 2472, 585, (179, 70, 163)),
]


@dataclass
class PatchResult:
    name: str
    group: str  # "grey" | "color"
    expected_rgb: tuple[int, int, int]
    measured_rgb: tuple[int, int, int]
    delta_e: float  # CIE76 in Lab
    delta_luma: float  # measured - expected mean luminance (grey ramp drift)

    @property
    def expected_css(self) -> str:
        return "rgb({},{},{})".format(*self.expected_rgb)

    @property
    def measured_css(self) -> str:
        return "rgb({},{},{})".format(*self.measured_rgb)


@dataclass
class ColorCheck:
    patches: list[PatchResult]
    grey_mean_delta_e: float
    color_mean_delta_e: float
    # Mean (R - B) over the grey ramp: positive = warm cast, negative = cool cast.
    grey_cast_r_minus_b: float


def _srgb_to_lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """sRGB (0-255) -> CIELAB, D65. Small and dependency-free on purpose."""

    def lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    # sRGB -> XYZ (D65)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _delta_e76(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    la, lb = _srgb_to_lab(a), _srgb_to_lab(b)
    return sum((p - q) ** 2 for p, q in zip(la, lb)) ** 0.5


def _luma(rgb: tuple[float, ...]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def check_card_crop(crop_path: Path) -> Optional[ColorCheck]:
    """Sample all patches from a rectified crop; None if Pillow/file unavailable."""
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return None
    try:
        img = Image.open(crop_path).convert("RGB")
    except OSError:
        return None

    sx = img.width / CANONICAL_W
    sy = img.height / CANONICAL_H
    half_x = max(2, int(SAMPLE_HALF * sx))
    half_y = max(2, int(SAMPLE_HALF * sy))

    def measure(cx: int, cy: int) -> tuple[int, int, int]:
        x, y = int(cx * sx), int(cy * sy)
        box = img.crop((x - half_x, y - half_y, x + half_x, y + half_y))
        return tuple(round(v) for v in ImageStat.Stat(box).mean)

    patches: list[PatchResult] = []
    for group, spec in (("grey", GREY_PATCHES), ("color", COLOR_PATCHES)):
        for name, cx, cy, expected in spec:
            measured = measure(cx, cy)
            patches.append(
                PatchResult(
                    name=name,
                    group=group,
                    expected_rgb=expected,
                    measured_rgb=measured,
                    delta_e=round(_delta_e76(measured, expected), 1),
                    delta_luma=round(_luma(measured) - _luma(expected), 1),
                )
            )

    greys = [p for p in patches if p.group == "grey"]
    colors = [p for p in patches if p.group == "color"]
    return ColorCheck(
        patches=patches,
        grey_mean_delta_e=round(sum(p.delta_e for p in greys) / len(greys), 1),
        color_mean_delta_e=round(sum(p.delta_e for p in colors) / len(colors), 1),
        grey_cast_r_minus_b=round(
            sum(p.measured_rgb[0] - p.measured_rgb[2] for p in greys) / len(greys), 1
        ),
    )
