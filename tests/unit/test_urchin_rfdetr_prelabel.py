"""E6: RF-DETR pre-labels, tiled (PREREG amendment 2026-10-18), with a fake model."""

from types import SimpleNamespace

import numpy as np
import pytest
from edge_ml.urchin import review
from edge_ml.urchin import rfdetr_prelabel as rp

Image = pytest.importorskip("PIL.Image")


class FakeModel:
    """Finds the white square in each crop and reports its bbox (crop coordinates)."""

    def predict(self, crop, threshold):
        a = np.asarray(crop.convert("L"))
        ys, xs = np.nonzero(a > 200)
        if not len(xs):
            return SimpleNamespace(xyxy=np.zeros((0, 4)), confidence=np.zeros(0))
        box = [[xs.min(), ys.min(), xs.max() + 1, ys.max() + 1]]
        return SimpleNamespace(xyxy=np.array(box, float), confidence=np.array([0.8]))


def test_tiled_boxes_are_in_frame_coordinates_and_merged():
    im = Image.new("RGB", (3840, 2160))
    im.paste((255, 255, 255), (1000, 1200, 1040, 1240))  # sits in two overlapping tiles
    dets = rp.detect_tiled(FakeModel(), im, 0.05)
    assert len(dets) == 1  # the overlap duplicate is merged by NMS
    assert dets[0]["box"] == [1000.0, 1200.0, 1040.0, 1240.0] and dets[0]["score"] == 0.8


def test_output_works_with_review_candidate_sheets_ids():
    im = Image.new("RGB", (1280, 960))
    im.paste((255, 255, 255), (10, 10, 30, 30))
    owl_format = {"detections": {"v/a.jpg": rp.detect_tiled(FakeModel(), im, 0.05), "v/b.jpg": []}}
    ids = review.candidate_ids(owl_format)
    assert [(k, s) for k, s, _, _ in ids] == [(0, "v/a.jpg")]
