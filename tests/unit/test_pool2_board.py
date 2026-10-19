"""edge_ml/pool2/board.py: sample boxes through the tag homography, and the rev 13 fit check."""

from __future__ import annotations

import numpy as np
import pytest
from edge_ml.pool2 import board, pose, wscore

TAGS_MM = {0: (44.45, 44.45, 63.0), 1: (375.45, 44.45, 63.0), 2: (44.45, 225.45, 63.0),
           3: (375.45, 225.45, 63.0)}       # V3 corner tags, configs/cards/nereus_v3_c1.yaml / 10


def _image_tags(K, depth_mm, origin_mm=(-210.0, -135.0)):
    """Face-on board, its (0, 0) corner at origin_mm from the optical axis, at depth_mm."""
    out = {}
    for i, (cx, cy, e) in TAGS_MM.items():
        c = board.tag_corners_mm(cx, cy, e) + origin_mm
        out[i] = np.c_[K[0, 0] * c[:, 0] / depth_mm + K[0, 2],
                       K[1, 1] * c[:, 1] / depth_mm + K[1, 2]]
    return out


def test_sample_box_and_px_across_follow_the_homography():
    K = pose.camera_matrix("openmv_n6", 1280, 800)
    tags_mm = {i: board.tag_corners_mm(*v) for i, v in TAGS_MM.items()}
    for depth in (1000.0, 2500.0):
        H = board.homography(_image_tags(K, depth), tags_mm)
        s = board.sample_box(H, (210.0, 135.0), 80.0)
        assert s["px_across"] == pytest.approx(K[0, 0] * 80.0 / depth, rel=0.01)
        x1, y1, x2, y2 = s["box"]
        assert (x2 - x1) == pytest.approx(K[0, 0] * 80.0 / depth, rel=0.02)
    # three tags are enough
    H3 = board.homography({i: v for i, v in _image_tags(K, 1000.0).items() if i != 3}, tags_mm)
    assert board.sample_box(H3, (210.0, 135.0), 50.0)["px_across"] == pytest.approx(
        K[0, 0] * 50.0 / 1000.0, rel=0.01)
    assert board.homography({}, tags_mm) is None


def test_px_across_is_the_shortest_projected_diameter():
    """Spec rev 18 item 10: on a foreshortened (tilted) board px is the shortest diameter; the
    longest is reported beside it."""
    H = np.array([[2.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])   # x stretched 2x
    s = board.sample_box(H, (100.0, 100.0), 80.0)
    assert s["px_across"] == pytest.approx(80.0, rel=0.01)
    assert s["px_across_max"] == pytest.approx(160.0, rel=0.01)


def test_iou():
    assert board.iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert board.iou([0, 0, 10, 10], [5, 0, 15, 10]) == pytest.approx(1 / 3)
    assert board.iou([0, 0, 1, 1], [2, 2, 3, 3]) == 0.0


def test_rev13_fit_check():
    # spec rev 13: at 1280x800 the N6 covers 0.79 d x 0.49 d, so the 420 x 270 mm card is whole
    # only from d ~ 0.55 m; centred on the axis it fits at 1.0 m but not at 0.5 m
    K = pose.camera_matrix("openmv_n6", 1280, 800)
    card = (420.0, 270.0)
    assert board.board_fits(K, 1280, 800, card, 1.0)
    assert not board.board_fits(K, 1280, 800, card, 0.5)
    assert board.first_fit_bin(K, 1280, 800, card, (0.0, 0.0), wscore.BIN_CENTRES) == 1.0
    # a board offset 300 mm sideways (centred between two camera axes) first fits farther out
    late = board.first_fit_bin(K, 1280, 800, card, (300.0, 0.0), wscore.BIN_CENTRES)
    assert late is not None and late > 1.0
