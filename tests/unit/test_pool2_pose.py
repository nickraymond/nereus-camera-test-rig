"""edge_ml/pool2/pose.py: board range from a synthetic tag25h9 card at a known depth."""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from edge_ml.pool2 import pose


def test_camera_matrix_scales_to_the_recorded_mode():
    k = pose.camera_matrix("openmv_n6", 640, 400)              # VGA = the HD field, halved
    assert k[0, 0] == pytest.approx(1628.856 / 2) and k[0, 2] == pytest.approx(637.406 / 2)
    imx = pose.camera_matrix("imx708", 1920, 1080, pose.parse_scaler_crop("(0, 0)/4608x2592"))
    assert imx[0, 0] == pytest.approx(2628.283 * 1920 / 4608)
    crop = pose.camera_matrix("imx708", 1920, 1080, (768, 432, 3072, 1728))
    assert crop[0, 2] == pytest.approx((2313.855 - 768) * 1920 / 3072)
    assert pose.parse_scaler_crop([0, 0, 4608, 2592]) == (0, 0, 4608, 2592)
    assert pose.parse_scaler_crop("nonsense") is None


def _project(K, depth_mm, edge_mm, centre_mm=(0.0, 0.0)):
    h = edge_mm / 2
    pts = np.array([[-h, h], [h, h], [h, -h], [-h, -h]]) + centre_mm
    obj = np.c_[pts, np.full(4, depth_mm)]
    uv = (K @ (obj.T / obj[:, 2])).T[:, :2]
    return uv


@pytest.mark.parametrize("depth_m", [0.5, 1.0, 2.0, 4.0])
def test_tag_depth_from_its_square(depth_m):
    K = pose.camera_matrix("openmv_ae3", 1280, 800)
    uv = _project(K, depth_m * 1000, 63.0, centre_mm=(30.0, -20.0))
    assert pose.tag_depth_m(uv, 63.0, K) == pytest.approx(depth_m, rel=0.01)


def test_board_range_on_a_rendered_card():
    # four tag25h9 markers (ids 0-3) at the corners of a 1280x800 frame's centre, 1 m away
    K = pose.camera_matrix("openmv_n6", 1280, 800)
    img = np.full((800, 1280), 230, np.uint8)
    d = cv2.aruco.getPredefinedDictionary(pose.TAG_DICT)
    side = 81                                     # 63 mm x fx / 1000 mm at 1 m ~ 103 px; any px ok
    tags = {}
    for i, (x, y) in enumerate([(400, 200), (800, 200), (400, 500), (800, 500)]):
        m = cv2.aruco.generateImageMarker(d, i, side + 2 * side // 7)  # with its white border
        img[y:y + m.shape[0], x:x + m.shape[1]] = m
        tags[i] = (x, y)
    found = pose.detect_tags(img)
    assert sorted(found) == [0, 1, 2, 3]
    # the detected black square is 7/9 of the marker image; its edge in px -> depth at 63 mm
    edge_px = np.linalg.norm(found[0][1] - found[0][0])
    res = pose.board_range(found, (0, 1, 2, 3), 63.0, K)
    assert res["board_whole"] and res["tags"] == 4
    assert res["range_m"] == pytest.approx(K[0, 0] * 63.0 / edge_px / 1000, rel=0.03)
    assert res["px_per_mm"] == pytest.approx(K[0, 0] / (res["range_m"] * 1000))
    part = pose.board_range({k: v for k, v in found.items() if k != 3}, (0, 1, 2, 3), 63.0, K)
    assert not part["board_whole"] and part["tags"] == 3
