"""Slanted-edge sharpness for pool test 2's W1 / W5 (docs/pool2_20261021/SPEC.md rev 12).

MTF50 (ISO 12233 slanted-edge method, simplified as in sfrmat): find the edge per row, fit a
line, project every pixel's distance to it, bin at 1/4 px into an edge spread function (ESF),
differentiate into a line spread function (LSF), Hamming-window it, FFT. MTF50 = the first
frequency where the MTF falls to 0.5, in cycles per pixel; times pixels per mm at the board it is
line pairs per mm at the board (W1). Edge width = the 10-90 % rise of the ESF, in pixels (W5).

Pure numpy. The ROI must hold one straight dark/light edge, within ~45 deg of vertical (pass a
transposed ROI for a near-horizontal edge), a few degrees off the pixel grid (the board's square is
printed ~5 deg rotated), with flat sides. Checked against the analytic Gaussian edge (tests/unit/test_pool2_sfr.py): MTF50
within 2.5 % for blur sigma 0.7-3 px in a 64 px ROI; keep the ROI >= ~16 sigma wide (at sigma 4 px a
64 px ROI reads +4 %).
"""

from __future__ import annotations

import numpy as np

OVERSAMPLE = 4  # ESF bins per pixel


def _edge_line(roi: np.ndarray) -> tuple[float, float]:
    """x = slope * y + offset of the edge: per row, the centroid of |d/dx|, then a line fit."""
    d = np.abs(np.diff(roi, axis=1))
    x = np.arange(d.shape[1]) + 0.5
    w = d.sum(axis=1)
    rows = np.nonzero(w > 0)[0]
    if len(rows) < 3:
        raise ValueError("no edge in the ROI")
    cent = (d[rows] * x).sum(axis=1) / w[rows]
    slope, offset = np.polyfit(rows.astype(float), cent, 1)
    return float(slope), float(offset)


def esf(roi: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """(distance px, ESF, edge angle deg) at 1/OVERSAMPLE px bins, distance perpendicular to the
    edge. Empty bins are filled by interpolation."""
    roi = np.asarray(roi, dtype=float)
    if roi.ndim != 2 or min(roi.shape) < 8:
        raise ValueError("ROI must be 2-D and at least 8 x 8 px")
    slope, offset = _edge_line(roi)
    yy, xx = np.mgrid[0:roi.shape[0], 0:roi.shape[1]]
    dist = (xx + 0.5 - (slope * yy + offset)) / np.hypot(1.0, slope)
    half = (roi.shape[1] / 2.0) * 0.9 / np.hypot(1.0, slope)
    keep = np.abs(dist) <= half
    idx = np.floor((dist[keep] + half) * OVERSAMPLE).astype(int)
    nbin = int(np.ceil(2 * half * OVERSAMPLE))
    idx = np.clip(idx, 0, nbin - 1)
    sums = np.bincount(idx, weights=roi[keep], minlength=nbin)
    counts = np.bincount(idx, minlength=nbin)
    centres = (np.arange(nbin) + 0.5) / OVERSAMPLE - half
    good = counts > 0
    if good.sum() < 8:
        raise ValueError("too few ESF samples")
    values = np.interp(centres, centres[good], sums[good] / counts[good])
    return centres, values, float(np.degrees(np.arctan(slope)))


def mtf(roi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(frequency cycles/px, MTF normalized to 1 at 0) of the ROI's edge."""
    _, e, _ = esf(roi)
    lsf = np.gradient(e)
    if lsf.sum() < 0:  # light-to-dark edge: the same spread, flipped
        lsf = -lsf
    n = len(lsf)
    peak = int(np.argmax(lsf))
    # Hamming window centred on the LSF peak (sfrmat), over the whole sample
    k = np.arange(n) - peak
    win = 0.54 + 0.46 * np.cos(np.pi * k / max(peak, n - 1 - peak, 1))
    spec = np.abs(np.fft.rfft(lsf * win))
    freq = np.fft.rfftfreq(n, d=1.0 / OVERSAMPLE)  # cycles per pixel
    m = spec / spec[0]
    # Undo the discrete derivative's own response (a [-1, 1] / 2 central difference over
    # 1/OVERSAMPLE px samples): sinc correction, as sfrmat does.
    arg = np.pi * freq / OVERSAMPLE
    corr = np.where(arg > 0, np.sin(arg) / np.where(arg > 0, arg, 1), 1.0)
    return freq, m / np.clip(corr, 0.1, None)


def mtf50(roi: np.ndarray) -> float:
    """MTF50 in cycles per pixel (linear interpolation at the first crossing of 0.5)."""
    f, m = mtf(roi)
    below = np.nonzero(m < 0.5)[0]
    if len(below) == 0 or below[0] == 0:
        raise ValueError("MTF never crosses 0.5")
    i = below[0]
    return float(f[i - 1] + (m[i - 1] - 0.5) * (f[i] - f[i - 1]) / (m[i - 1] - m[i]))


def edge_width_10_90(roi: np.ndarray) -> float:
    """The ESF's 10-90 % rise distance in pixels, perpendicular to the edge (W5 blur width)."""
    d, e, _ = esf(roi)
    lo, hi = np.percentile(e, 2), np.percentile(e, 98)
    if hi - lo <= 0:
        raise ValueError("flat ROI")
    n = (e - lo) / (hi - lo)
    if n[0] > n[-1]:
        n = n[::-1]
        d = -d[::-1]

    def cross(level):
        i = int(np.nonzero(n >= level)[0][0])
        if i == 0:
            return float(d[0])
        return float(d[i - 1] + (level - n[i - 1]) * (d[i] - d[i - 1]) / (n[i] - n[i - 1]))
    return cross(0.9) - cross(0.1)


def lp_per_mm(mtf50_cpp: float, px_per_mm: float) -> float:
    """MTF50 at the board: cycles per pixel x pixels per mm at the board = line pairs per mm."""
    return mtf50_cpp * px_per_mm
