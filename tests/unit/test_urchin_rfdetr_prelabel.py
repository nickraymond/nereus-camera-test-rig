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


def _pin(monkeypatch, data: bytes):
    import hashlib

    monkeypatch.setattr(rp, "CHECKPOINT_BYTES", len(data))
    monkeypatch.setattr(rp, "CHECKPOINT_SHA256", hashlib.sha256(data).hexdigest())


def test_checkpoint_matching_the_pin_is_accepted(tmp_path, monkeypatch):
    ck = tmp_path / "ck.pth"
    ck.write_bytes(b"weights" * 100)
    _pin(monkeypatch, b"weights" * 100)
    assert rp.check_checkpoint(ck) == rp.CHECKPOINT_SHA256


@pytest.mark.parametrize(
    "data, match",
    [(b"weights" * 99, "pinned 700 B"), (b"Weights" * 100, "sha256 .* pinned"), (None, "None B")],
)
def test_checkpoint_off_the_pin_is_refused_before_any_load(tmp_path, monkeypatch, data, match):
    ck = tmp_path / "ck.pth"
    if data is not None:
        ck.write_bytes(data)
    _pin(monkeypatch, b"weights" * 100)
    with pytest.raises(SystemExit, match=match):
        rp.check_checkpoint(ck)
    stills = tmp_path / "stills.txt"
    stills.write_text("v/a.jpg\n")
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit, match="refusing"):  # main refuses before importing rfdetr
        rp.main([str(stills), str(tmp_path), str(out), "--checkpoint", str(ck)])
    assert not out.exists()


def test_the_pin_is_spec_rev3_must_do_1():
    assert rp.CHECKPOINT_BYTES == 127_700_271
    assert rp.CHECKPOINT_SHA256 == (
        "ae9aa853d9d73354f318e03e710f517d032408791721f206f55c01f3ca0c915f"
    )
