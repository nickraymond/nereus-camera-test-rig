"""S4 calibrate stage — pure logic: CCM fit, DNG matrix read, chart config + finder."""

from pathlib import Path

import numpy as np
import pytest

from nereus_camera_test_rig.color.calibrate import SERIES, dng_to_linear, fit_ccm
from nereus_camera_test_rig.color.chart import find_chart, load_chart
from nereus_camera_test_rig.color.metrics import SRGB_TO_XYZ, WHITE_XYZ

REPO = Path(__file__).resolve().parents[2]
CHART = REPO / "configs" / "charts" / "pixel_perfect_24.yaml"


def test_fit_ccm_recovers_a_row_sum_one_matrix():
    rng = np.random.default_rng(1)
    true = np.array([[1.6, -0.4, -0.2], [-0.3, 1.7, -0.4], [0.0, -0.6, 1.6]])
    cam = rng.uniform(0.02, 0.9, (60, 3))
    M = fit_ccm(cam, cam @ true.T)
    assert np.allclose(M, true, atol=1e-9)
    assert np.allclose(M.sum(axis=1), 1.0)


def test_fit_ccm_keeps_rows_summing_to_one_on_noisy_data():
    rng = np.random.default_rng(2)
    cam = rng.uniform(0.02, 0.9, (40, 3))
    M = fit_ccm(cam, cam @ np.eye(3).T + rng.normal(0, 0.01, (40, 3)))
    assert np.allclose(M.sum(axis=1), 1.0)


def test_dng_to_linear_maps_the_neutral_to_d65_white():
    # a DNG-style XYZ -> camera matrix that includes white-balance gains (as rpicam writes it)
    xyz_to_cam = np.diag([0.6, 1.0, 0.41]) @ np.linalg.inv(SRGB_TO_XYZ)
    neutral = xyz_to_cam @ WHITE_XYZ * 0.3
    out = dng_to_linear(neutral, xyz_to_cam, neutral)
    assert np.allclose(out, [1.0, 1.0, 1.0], atol=1e-9)
    # linear: twice the neutral → twice the white
    assert np.allclose(dng_to_linear(2 * neutral, xyz_to_cam, neutral), 2.0)


def test_series_names():
    assert SERIES.match("stop_+0_r2.dng").groups() == ("+0", "2", "dng")
    assert SERIES.match("stop_-1_r0.bayer").group(1) == "-1"
    assert SERIES.match("locked.bayer") is None


def test_load_chart_grid_and_groups():
    chart = load_chart(CHART)
    assert (chart.rows, chart.cols, len(chart.patches)) == (4, 6, 24)
    assert [p.group for p in chart.patches if p.row == 3] == ["grey"] * 6
    assert chart.patches[0].id == "chart_r1c1"


def _synthetic_chart(chart, pitch=100, size=80, origin=(150, 120), missing=((3, 5),)):
    """Binned image = canonical frame (identity H): black surround, bright patches."""
    img = np.full((700, 900, 3), 0.01)
    centres = {}
    for p in chart.patches:
        cx, cy = origin[0] + p.col * pitch, origin[1] + p.row * pitch
        centres[p.id] = (cx, cy)
        if (p.row, p.col) not in missing:  # e.g. the black patch, at surround level
            img[cy - size // 2:cy + size // 2, cx - size // 2:cx + size // 2] = 0.3
    return img, centres


def test_find_chart_places_every_patch_including_missed_ones():
    chart = load_chart(CHART)
    img, centres = _synthetic_chart(chart)
    found = find_chart(img, np.eye(3), [1.0, 1.0, 1.0], chart, (0, 0, 900, 700))
    assert found["n_found"] == 23 and found["source"]["chart_r4c6"] == "grid"
    for pid, (cx, cy) in centres.items():
        box = found["boxes"][pid]
        assert abs(box.x + box.w / 2 - cx) < 2 and abs(box.y + box.h / 2 - cy) < 2, pid


def test_find_chart_fails_loudly_without_a_chart():
    chart = load_chart(CHART)
    with pytest.raises(ValueError, match="chart"):
        find_chart(np.full((700, 900, 3), 0.2), np.eye(3), [1, 1, 1], chart, (0, 0, 900, 700))
