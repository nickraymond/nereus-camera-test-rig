"""Edge-rise measurement (scripts/n6_edge_sharpness.py) on synthetic blurred squares of known width."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import n6_edge_sharpness as es  # noqa: E402

cv2 = pytest.importorskip("cv2")


def _square(sigma: float, angle_deg: float = 0.0):
    """A 140 px black square on light grey (200), Gaussian-blurred: 10-90 % rise = 2.563 sigma."""
    big = 4  # draw at 4x and downsample, so the edge is anti-aliased like a real one
    img = np.full((400 * big, 400 * big), 200, np.float32)
    c, h = 200 * big, 70 * big
    a = np.deg2rad(angle_deg)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    quad = (np.array([[-h, -h], [h, -h], [h, h], [-h, h]]) @ rot.T) + c
    cv2.fillPoly(img, [quad.astype(np.int32)], 0)
    img = cv2.resize(img, (400, 400), interpolation=cv2.INTER_AREA)
    if sigma:
        img = cv2.GaussianBlur(img, (0, 0), sigma)
    return img, quad / big


@pytest.mark.parametrize("sigma,angle", [(1.5, 0), (2.5, 0), (2.0, 17)])
def test_rise_matches_the_gaussian_width(sigma, angle):
    img, quad = _square(sigma, angle)
    sides = es.tag_rises(img, quad, edge_mm=63.0)
    rises = [s["rise_px"] for s in sides if s["ok"]]
    assert len(rises) == 4
    expect = 2.563 * sigma
    assert abs(np.median(rises) - expect) < 0.35
    s = sides[0]
    assert s["rise_mm"] == pytest.approx(s["rise_px"] * 63.0 / s["edge_px"])


def test_a_sharper_edge_gives_a_smaller_rise():
    soft = np.median([s["rise_px"] for s in es.tag_rises(*_square(3.0), edge_mm=63.0)])
    sharp = np.median([s["rise_px"] for s in es.tag_rises(*_square(1.0), edge_mm=63.0)])
    assert sharp < soft


def test_flat_patch_is_reported_not_measured():
    img = np.full((200, 200), 120, np.float32)
    r = es.side_rise(img, (50, 50), (150, 50), (100, 100), half_px=8)
    assert r["ok"] is False
