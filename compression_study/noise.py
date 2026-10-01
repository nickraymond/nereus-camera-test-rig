"""Sensor noise from three locked repeats of the same scene (spec 5.2, as amended by review).

- **Row banding removed first**: each repeat's plane is divided by its row-mean ratio to the
  three-repeat mean (the warm LED lamp flickers row-wise on the IMX708 rolling shutter; the
  scene is static, so whole-row means are a clean reference).
- **Temporal variance per pixel** across the repeats (ddof = 1) on eroded, unclipped patch
  interiors. With n = 3 the per-pixel variance is σ²·χ²₂/2 (exponential). A median is not
  usable on 8-bit OpenMV data: with σ < 1 DN most pixels give 0 or 1/3 exactly and the median
  locks at 1/3. So the estimate is the mean of the lower 95 % divided by its exact
  expectation for an exponential, 1 − 0.05·(1 + ln 20)/0.95 ≈ 0.842 — robust to edge pixels
  and the AE3 r2 sub-pixel shift, unbiased for clean data.
- **Fit** σ²(v) = a·v + c per channel (v = signal above black, DN), weighted least squares on
  the patches above ``vmin``. Metrics use each patch's **measured** σ; the fit is for the
  underwater simulation (needs the gain ``a``) and the rate-distortion guide line.
- **Block-mean σ** (8×8 per plane): the same estimator on block means, binned by level —
  OpenMV's on-chip denoise correlates neighbouring pixels, so it is not σ/8.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .common import CHANNELS, Raw, split
from .rois import mask

LN2 = np.log(2.0)
BLOCK = 8
TRIM = 0.95
TRIM_E = (1 - (1 - TRIM) * (1 + np.log(1 / (1 - TRIM)))) / TRIM  # E[X | X < q95], X ~ Exp(1)


def robust_var(v: np.ndarray) -> float:
    """σ² from per-pixel variances (each σ²·Exp(1)): trimmed mean / its expectation."""
    v = np.sort(np.ravel(v))
    return float(v[: max(1, int(len(v) * TRIM))].mean() / TRIM_E)


def deband(planes: list[np.ndarray]) -> list[np.ndarray]:
    rows = np.stack([p.mean(axis=1) for p in planes])
    ref = rows.mean(axis=0)
    ratio = rows / np.where(ref > 0, ref, 1)
    return [p / np.where(r > 0, r, 1)[:, None] for p, r in zip(planes, ratio)]


def _block_means(p: np.ndarray, n: int = BLOCK) -> np.ndarray:
    h, w = (p.shape[0] // n) * n, (p.shape[1] // n) * n
    return p[:h, :w].reshape(h // n, n, w // n, n).mean(axis=(1, 3))


@dataclass
class NoiseModel:
    camera: str
    a: dict[str, float]
    c: dict[str, float]
    fit_rel_rms: dict[str, float]
    patch: dict[str, dict[str, tuple[float, float]]]  # patch → channel → (v, sigma)
    block: dict[str, tuple[np.ndarray, np.ndarray]] = field(repr=False, default_factory=dict)

    def sigma(self, ch: str, v) -> np.ndarray:
        return np.sqrt(np.maximum(self.a[ch] * np.asarray(v, float) + self.c[ch], 1 / 12))

    def block_sigma(self, ch: str, v) -> np.ndarray:
        lv, ls = self.block[ch]
        return np.exp(np.interp(np.log(np.maximum(np.asarray(v, float), 0.05)), lv, ls))

    def summary(self) -> dict:
        return {"a": self.a, "c": self.c, "fit_rel_rms": self.fit_rel_rms}


def estimate(repeats: list[Raw], rois: dict, vmin: float, erode: int = 2) -> NoiseModel:
    raw0 = repeats[0]
    per = [split(r.mosaic.astype(np.float64), r.cfa) for r in repeats]
    clip = np.zeros(per[0]["R"].shape, bool)
    for p in per:
        for ch in CHANNELS:
            clip |= p[ch] >= raw0.white
    a, c, rel, patch, block = {}, {}, {}, {}, {}
    masks = {pid: mask(r["quad"], clip.shape, erode) & ~clip for pid, r in rois["patches"].items()}
    for ch in CHANNELS:
        planes = deband([p[ch] for p in per])
        stack = np.stack(planes)
        var = stack.var(axis=0, ddof=1)
        mean = stack.mean(axis=0) - raw0.black
        vs, ss = [], []
        for pid, m in masks.items():
            if m.sum() < 30:
                continue
            v = float(np.median(mean[m]))
            s2 = robust_var(var[m])
            patch.setdefault(pid, {})[ch] = (v, float(np.sqrt(s2)))
            if v >= vmin:
                vs.append(v)
                ss.append(s2)
        vs, ss = np.asarray(vs), np.asarray(ss)
        if len(vs) < 3:
            raise ValueError(f"{raw0.camera} {ch}: only {len(vs)} patches above {vmin} DN")
        w = 1 / np.maximum(ss, 1e-3)  # relative-error weighting
        A = np.stack([vs, np.ones_like(vs)], 1) * w[:, None]
        (ka, kc), *_ = np.linalg.lstsq(A, ss * w, rcond=None)
        ka, kc = max(float(ka), 1e-6), max(float(kc), 0.0)
        a[ch], c[ch] = ka, kc
        rel[ch] = float(np.sqrt(np.mean(((ka * vs + kc) / ss - 1) ** 2)))
        # block means: temporal variance of 8×8 means, binned by level (median / ln 2)
        bm = np.stack([_block_means(p) for p in planes])
        bclip = _block_means(clip.astype(float)) > 0
        bv = (bm.mean(axis=0) - raw0.black)[~bclip]
        bs2 = bm.var(axis=0, ddof=1)[~bclip]
        edges = np.quantile(bv, np.linspace(0, 1, 13))
        lv, ls = [], []
        for lo, hi in zip(edges[:-1], edges[1:]):
            sel = (bv >= lo) & (bv <= hi)
            if sel.sum() >= 20:
                lv.append(np.log(max(np.median(bv[sel]), 0.05)))
                ls.append(0.5 * np.log(max(robust_var(bs2[sel]), 1e-6)))
        block[ch] = (np.asarray(lv), np.asarray(ls))
    return NoiseModel(raw0.camera, a, c, rel, patch, block)
