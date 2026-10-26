"""scripts/n6_raw_compare.py on synthetic Bayer with known noise, correlation, geometry and linearity."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
pytest.importorskip("cv2")
import n6_raw_compare as rc  # noqa: E402


def test_cfa_offsets_bggr():
    assert rc.cfa_offsets("BGGR") == {"R": [(1, 1)], "G": [(0, 1), (1, 0)], "B": [(0, 0)]}


def test_temporal_noise_snr_and_zero_correlation_for_white_noise():
    rng = np.random.default_rng(0)
    stack = np.clip(100 + rng.normal(0, 3.0, (10, 64, 64)), 0, 255)
    mask = np.ones((64, 64), bool)
    st = rc.channel_stats(stack, mask, "BGGR", black=0.0)
    for ch in "RGB":
        assert st[ch]["noise_dn"] == pytest.approx(3.0, rel=0.05)
        assert st[ch]["mean_dn"] == pytest.approx(100, abs=0.2)
        assert st[ch]["snr"] == pytest.approx(100 / 3.0, rel=0.06)
        assert abs(st[ch]["neighbour_r"]) < 0.06


def test_neighbour_correlation_detects_smoothed_noise():
    rng = np.random.default_rng(1)
    n = rng.normal(0, 3.0, (10, 64, 66))
    smooth = (n[:, :, :-2] + n[:, :, 1:-1] + n[:, :, 2:]) / 3  # each pixel shares noise with x+-1, x+-2
    stack = 100 + smooth
    st = rc.channel_stats(stack, np.ones((64, 64), bool), "BGGR", black=0.0)
    assert st["G"]["neighbour_r"] == pytest.approx(1 / 3, abs=0.06)  # 1 of 3 terms shared at distance 2


def test_black_is_subtracted_from_the_mean():
    stack = np.full((3, 16, 16), 50.0) + np.arange(3)[:, None, None]
    st = rc.channel_stats(stack, np.ones((16, 16), bool), "BGGR", black=10.0)
    assert st["G"]["mean_dn"] == pytest.approx(41.0)


def test_patch_mask_identity_homography_is_the_inset_box():
    m = rc.patch_mask(np.eye(3), [10, 20, 50, 100], (200, 200))
    ys, xs = np.nonzero(m)
    assert (xs.min(), xs.max(), ys.min(), ys.max()) == (20, 49, 40, 99)  # x 20..50, y 40..100 (60 % inset)


def test_patch_mask_follows_a_scaled_homography():
    h = np.diag([0.5, 0.5, 1.0])
    m = rc.patch_mask(h, [100, 100, 100, 100], (200, 200))
    assert m.sum() == pytest.approx(30 * 30, rel=0.1)  # 60 x 60 canonical -> 30 x 30 px


def test_fit_line_recovers_intercept_and_slope():
    f = rc.fit_line([2, 4, 8], [3 + 5 * 2, 3 + 5 * 4, 3 + 5 * 8])
    assert f["intercept"] == pytest.approx(3) and f["slope_per_ms"] == pytest.approx(5)
    assert f["max_resid_frac"] < 1e-6
