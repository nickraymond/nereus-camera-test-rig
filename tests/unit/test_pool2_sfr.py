"""edge_ml/pool2/sfr.py: slanted-edge MTF50 and 10-90 % width against the analytic Gaussian edge."""

from __future__ import annotations

import math

import numpy as np
import pytest
from edge_ml.pool2 import sfr

_erf = np.frompyfunc(math.erf, 1, 1)


def _edge(sigma, angle_deg=5.0, size=64, lo=40.0, hi=200.0, noise=0.0, light_left=False,
          seed=0):
    """A straight edge through the ROI centre, Gaussian-blurred (sigma px), point-sampled."""
    yy, xx = np.mgrid[0:size, 0:size] + 0.5
    t = math.tan(math.radians(angle_deg))
    d = (xx - (size / 2 + t * (yy - size / 2))) * math.cos(math.radians(angle_deg))
    phi = 0.5 * (1 + _erf(d / (sigma * math.sqrt(2))).astype(float))
    if light_left:
        phi = 1 - phi
    img = lo + (hi - lo) * phi
    if noise:
        img = img + np.random.default_rng(seed).normal(0, noise * (hi - lo), img.shape)
    return img


@pytest.mark.parametrize("sigma", [1.0, 1.5, 2.0, 3.0])
def test_mtf50_matches_the_gaussian_edge(sigma):
    expect = math.sqrt(math.log(2) / 2) / (math.pi * sigma)   # exp(-2 pi^2 s^2 f^2) = 0.5
    assert sfr.mtf50(_edge(sigma)) == pytest.approx(expect, rel=0.05)


@pytest.mark.parametrize("sigma", [1.0, 2.0, 3.0])
def test_edge_width_10_90_matches_the_gaussian_edge(sigma):
    assert sfr.edge_width_10_90(_edge(sigma)) == pytest.approx(2 * 1.28155 * sigma, rel=0.06)


def test_direction_angle_and_noise():
    base = sfr.mtf50(_edge(2.0))
    assert sfr.mtf50(_edge(2.0, light_left=True)) == pytest.approx(base, rel=0.02)
    assert sfr.mtf50(_edge(2.0, angle_deg=-8)) == pytest.approx(base, rel=0.05)
    assert sfr.mtf50(_edge(2.0, noise=0.01)) == pytest.approx(base, rel=0.1)
    assert sfr.esf(_edge(2.0, angle_deg=5))[2] == pytest.approx(5.0, abs=0.3)
    assert sfr.lp_per_mm(0.1, 4.0) == pytest.approx(0.4)


def test_sharper_edge_scores_higher_and_flat_roi_is_refused():
    assert sfr.mtf50(_edge(1.0)) > sfr.mtf50(_edge(3.0))
    with pytest.raises(ValueError):
        sfr.mtf50(np.full((32, 32), 100.0))
