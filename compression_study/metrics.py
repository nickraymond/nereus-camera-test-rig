"""Metrics of a reconstruction against M0, in the linear raw domain (spec §5, as amended).

Everything about the reference frame is computed once in ``Context``; ``evaluate`` then
scores one reconstruction. Masked out everywhere: **highlight clipping only** (M0 at white,
or any channel over 1 after spec-M1's white balance), dilated 2 px. Clipping at 0 is never
masked — a method that floors red is exactly what is being measured (``clip0_R``).

Gate metrics (owner's §8, verbatim):
- ``red_err_noise``: median over card + chart patches of RMSE(recon − M0) / σ_patch on R.
- ``stress_de_mean``: mean over patches of ΔE2000 between corrected M0 and corrected recon,
  correction = WB on grey 128 → R ×4, G ×1.5 → 3×3 CCM fitted on M0.

Supporting (review consensus):
- ``bm_err_R_med``: 8×8 block-mean error / measured block σ (median, unmasked blocks).
- ``block_de_*``: ΔE2000 of 16×16-Bayer-block colours after WB + CCM (median / p95 / p99),
  without stress and with stress A; ``patch_de_mean``: the same on patch means, no stress.
- ``red_err_noise_vs_mean3``: R error against the mean of the three repeats (a denoised
  reference: a codec that removes noise scores well here, one that adds error does not).
- Stress B (R ×7.7, B ×1.2: the TG-7 grey at 15.5 m) on patches.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from nereus_camera_test_rig.color.calibrate import anchor_y, fit_ccm
from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.metrics import delta_e2000, linear_to_lab, srgb8_to_linear
from nereus_camera_test_rig.color.raw_io import demosaic_bilinear

from .common import CHANNELS, Raw, plane_offsets, split, srgb_oetf
from .noise import NoiseModel, _block_means
from .rois import CARD, mask

STRESS_A = np.array([4.0, 1.5, 1.0])
STRESS_B = np.array([7.7, 1.0, 1.2])
ANCHOR = "gray_mid"
CCM_EXCLUDE = {"gray_black", "gray_white"}  # black is haze-like, white clips at stop 0


def _rgb(planes: dict[str, np.ndarray]) -> np.ndarray:
    return np.stack([planes["R"], (planes["G1"] + planes["G2"]) / 2, planes["B"]], axis=-1)


@dataclass
class Context:
    ref: Raw
    repeats: list[Raw]
    rois: dict
    noise: NoiseModel
    wb_isp: np.ndarray  # spec-M1 white balance (linear multipliers, R G B)
    hl_mask: np.ndarray = field(init=False)  # mosaic bool
    patch_masks: dict = field(init=False)
    ccm: np.ndarray = field(init=False)
    wb_color: np.ndarray = field(init=False)

    def __post_init__(self):
        r = self.ref
        scale = float(r.white - r.black)
        mos = r.mosaic
        hl = mos >= r.white
        # sites whose own channel clips after the ISP white balance
        planes = split(mos.astype(np.float64), r.cfa)
        ch_idx = {"R": 0, "G1": 1, "G2": 1, "B": 2}
        site = np.zeros_like(hl)
        for ch, (dy, dx) in plane_offsets(r.cfa).items():
            v = (planes[ch] - r.black) / scale * self.wb_isp[ch_idx[ch]]
            site[dy::2, dx::2] = v >= 1.0
        hl = cv2.dilate((hl | site).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        self.hl_mask = hl
        self.hl_planes = split(hl.astype(np.uint8), r.cfa)
        self.ref_planes = {k: v.astype(np.float64) for k, v in split(mos, r.cfa).items()}
        shape = self.ref_planes["R"].shape
        self.patch_masks = {}
        for pid, p in self.rois["patches"].items():
            m = mask(p["quad"], shape) & ~self.hl_planes["R"].astype(bool)
            for ch in CHANNELS:
                m &= ~self.hl_planes[ch].astype(bool)
            if m.sum() >= 20:
                self.patch_masks[pid] = m
        mean3 = np.mean([x.mosaic.astype(np.float64) for x in self.repeats], axis=0)
        self.mean3_planes = split(mean3, r.cfa)
        # colour: WB on the anchor of M0, CCM fitted on M0's card patches
        self.card = load_card(CARD)
        self.ref_pm = self._patch_means(self.ref_planes)
        y = anchor_y(self.card, ANCHOR)
        self.wb_color = y / self.ref_pm[ANCHOR]
        ids = [p.id for p in self.card.patches if p.id not in CCM_EXCLUDE
               and p.id in self.ref_pm]
        cam = np.array([self.ref_pm[i] * self.wb_color for i in ids])
        tgt = np.array([srgb8_to_linear(self.card.patch(i).truth) for i in ids])
        self.ccm = fit_ccm(cam, tgt)
        self.ref_lab = {k: self._lab(v) for k, v in self._corrected(self.ref_pm).items()}
        # blocks
        self.ref_bm = {ch: _block_means(self.ref_planes[ch]) for ch in CHANNELS}
        self.blk_ok = _block_means(sum(self.hl_planes[ch].astype(float)
                                       for ch in CHANNELS)) == 0
        self.ref_blk_lab = self._block_lab(self.ref_bm)
        # SSIM reference render (shared ISP, WB from M0 anchor, no matrix)
        self.ref_luma = self._luma(self.ref.mosaic.astype(np.float64))
        x0, y0, x1, y1 = self.rois["texture_box"]
        self.tex = (slice(2 * y0, 2 * y1), slice(2 * x0, 2 * x1))

    # ---- helpers
    def _patch_means(self, planes) -> dict[str, np.ndarray]:
        b, s = self.ref.black, float(self.ref.white - self.ref.black)
        return {pid: np.array([planes["R"][m].mean() - b,
                          (planes["G1"][m].mean() + planes["G2"][m].mean()) / 2 - b,
                          planes["B"][m].mean() - b]) / s
                for pid, m in self.patch_masks.items()}

    def _corrected(self, pm: dict, stress=None) -> dict[str, np.ndarray]:
        st = np.ones(3) if stress is None else stress
        return {k: (v * self.wb_color * st) @ self.ccm.T for k, v in pm.items()}

    @staticmethod
    def _lab(lin):
        return linear_to_lab(lin)

    def _block_lab(self, bm, stress=None):
        b, s = self.ref.black, float(self.ref.white - self.ref.black)
        rgb = (_rgb(bm) - b) / s
        st = np.ones(3) if stress is None else stress
        return linear_to_lab((rgb * self.wb_color * st) @ self.ccm.T)

    def _luma(self, mosaic: np.ndarray) -> np.ndarray:
        r = self.ref
        lin = ((mosaic - r.black) / (r.white - r.black)).astype(np.float32)
        rgb = demosaic_bilinear(lin, r.cfa) * self.wb_isp.astype(np.float32)
        s = srgb_oetf(rgb) * 255
        return (s @ np.array([0.2126, 0.7152, 0.0722])).astype(np.float32)


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Gaussian-window SSIM (Wang et al. 2004; σ = 1.5, L = 255) on two luma images."""
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    g = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)  # noqa: E731
    mu_a, mu_b = g(a), g(b)
    saa = g(a * a) - mu_a ** 2
    sbb = g(b * b) - mu_b ** 2
    sab = g(a * b) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + c1) * (2 * sab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1)
                                                     * (saa + sbb + c2))
    return float(m.mean())


def evaluate(ctx: Context, recon: np.ndarray, *, with_ssim: bool = True) -> dict[str, float]:
    r = ctx.ref
    rp = split(recon, r.cfa)
    out: dict[str, float] = {}
    for ch in CHANNELS:
        ok = ~ctx.hl_planes[ch].astype(bool)
        err = rp[ch][ok] - ctx.ref_planes[ch][ok]
        out[f"rmse_{ch}"] = float(np.sqrt(np.mean(err ** 2)))
        out[f"maxabs_{ch}"] = float(np.abs(err).max()) if err.size else 0.0
        out[f"bias_{ch}"] = float(err.mean())
        ratios, ratios3 = [], []
        for pid, m in ctx.patch_masks.items():
            sig = ctx.noise.patch.get(pid, {}).get(ch)
            if not sig or sig[1] <= 0:
                continue
            e = rp[ch][m] - ctx.ref_planes[ch][m]
            ratios.append(np.sqrt(np.mean(e ** 2)) / sig[1])
            e3 = rp[ch][m] - ctx.mean3_planes[ch][m]
            ratios3.append(np.sqrt(np.mean(e3 ** 2)) / sig[1])
        out[f"err_noise_{ch}"] = float(np.median(ratios)) if ratios else np.nan
        out[f"err_noise3_{ch}"] = float(np.median(ratios3)) if ratios3 else np.nan
        bm = _block_means(rp[ch])
        lvl = ctx.ref_bm[ch] - r.black
        z = np.abs(bm - ctx.ref_bm[ch]) / ctx.noise.block_sigma(ch, lvl)
        z = z[ctx.blk_ok]
        out[f"bm_err_{ch}_med"] = float(np.median(z))
        out[f"bm_err_{ch}_p95"] = float(np.percentile(z, 95))
    out["red_err_noise"] = out["err_noise_R"]
    out["red_err_noise_vs_mean3"] = out["err_noise3_R"]
    # clip-to-0 on red where the reference has signal
    ref_r = ctx.ref_planes["R"] - r.black
    sig = ref_r >= 2
    out["clip0_R"] = float(np.mean(rp["R"][sig] <= r.black + 0.5)) if sig.any() else 0.0
    # colour on patch means
    pm = ctx._patch_means(rp)
    ref_pm = ctx.ref_pm
    red_pct = [abs(pm[k][0] - ref_pm[k][0]) / max(ref_pm[k][0], 1e-9) * 100 for k in pm
               if ref_pm[k][0] * (r.white - r.black) >= 2]
    out["red_pct_err_med"] = float(np.median(red_pct)) if red_pct else np.nan
    out["red_pct_err_max"] = float(np.max(red_pct)) if red_pct else np.nan
    for name, st in (("patch_de", None), ("stress_de", STRESS_A), ("stressB_de", STRESS_B)):
        a = ctx._corrected(ref_pm, st)
        b = ctx._corrected(pm, st)
        de = np.array([delta_e2000(ctx._lab(a[k]), ctx._lab(b[k])) for k in a])
        out[f"{name}_mean"] = float(de.mean())
        out[f"{name}_max"] = float(de.max())
    # colour on blocks
    rb = {ch: _block_means(rp[ch]) for ch in CHANNELS}
    for name, st in (("block_de", None), ("block_de_stress", STRESS_A)):
        ref_lab = ctx.ref_blk_lab if st is None else ctx._block_lab(ctx.ref_bm, st)
        de = delta_e2000(ref_lab, ctx._block_lab(rb, st))[ctx.blk_ok]
        out[f"{name}_med"] = float(np.median(de))
        out[f"{name}_p95"] = float(np.percentile(de, 95))
        out[f"{name}_p99"] = float(np.percentile(de, 99))
    if with_ssim:
        luma = ctx._luma(recon)
        out["ssim_full"] = ssim(ctx.ref_luma, luma)
        out["ssim_tex"] = ssim(ctx.ref_luma[ctx.tex], luma[ctx.tex])
    return out
