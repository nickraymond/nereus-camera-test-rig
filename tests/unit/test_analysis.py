"""Unit tests for the reference-card analysis pipeline — Spec §13.

Run against the real V2 card fixture plus synthetic cases. Skipped entirely if the
'analysis' extra (opencv-contrib) is not installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("cv2")
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from nereus_camera_test_rig.analysis import reference_card  # noqa: E402
from nereus_camera_test_rig.analysis.apriltag_detector import detect_tags  # noqa: E402
from nereus_camera_test_rig.analysis.result_writer import (  # noqa: E402
    AnalysisConfig,
    analyze_reference_card,
)

FIXTURE = Path("tests/fixtures/reference_card/Nereus_Reef_Reference_Card_V2.png")


def _synth_card(size=1600, tag_px=160):
    """White canvas with four 36h11 tags (0..3) at the corners — a minimal card."""
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    canvas = np.full((size, size, 3), 255, np.uint8)
    m = 80
    positions = {0: (m, m), 1: (size - m - tag_px, m),
                 2: (m, size - m - tag_px), 3: (size - m - tag_px, size - m - tag_px)}
    for tid, (x, y) in positions.items():
        tag = cv2.aruco.generateImageMarker(d, tid, tag_px)
        canvas[y:y + tag_px, x:x + tag_px] = cv2.cvtColor(tag, cv2.COLOR_GRAY2BGR)
    return canvas


# -- detector ---------------------------------------------------------------
@pytest.mark.skipif(not FIXTURE.exists(), reason="V2 fixture missing")
def test_detects_four_tags_on_real_card():
    outcome = detect_tags(FIXTURE)
    assert outcome.tag_ids == [0, 1, 2, 3]


def test_detects_synthetic_tags():
    outcome = detect_tags(_synth_card())
    assert set(outcome.tag_ids) == {0, 1, 2, 3}
    # Tag 0 is top-left; its center should be in the upper-left quadrant.
    c0 = outcome.tags[0].center
    assert c0[0] < 800 and c0[1] < 800


# -- geometry ---------------------------------------------------------------
def test_expand_quad_grows_symmetrically():
    quad = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], np.float32)
    out = reference_card.expand_quad(quad, 2.0, 2.0)
    # center preserved, extent doubled -> corners at (-50..150)
    assert np.allclose(out.mean(axis=0), [50, 50])
    assert out[:, 0].min() == pytest.approx(-50) and out[:, 0].max() == pytest.approx(150)


def test_localize_missing_tag_raises():
    outcome = detect_tags(_synth_card())
    del outcome.tags[3]  # drop bottom-right
    with pytest.raises(reference_card.CardLocalizationError):
        reference_card.localize_card(outcome)


# -- full pipeline ----------------------------------------------------------
@pytest.mark.skipif(not FIXTURE.exists(), reason="V2 fixture missing")
def test_analyze_real_card_passes(tmp_path):
    res = analyze_reference_card(FIXTURE, tmp_path)
    assert res.status == "pass"
    assert res.all_expected_tags_found
    assert res.tags_detected == [0, 1, 2, 3]
    assert res.card_crop_created
    assert res.crop_width == 3000 and res.crop_height == 1000
    # Artifacts written.
    assert (tmp_path / "detection.json").is_file()
    assert (tmp_path / "annotated.jpg").is_file()
    assert (tmp_path / "card_crop.jpg").is_file()
    assert Path(res.crop_path).stat().st_size > 0


def test_analyze_blank_image_fails(tmp_path):
    blank = tmp_path / "blank.jpg"
    cv2.imwrite(str(blank), np.full((600, 800, 3), 255, np.uint8))
    res = analyze_reference_card(blank, tmp_path)
    assert res.status == "fail"
    assert res.tags_detected == []
    assert not res.card_crop_created
    assert res.errors
    assert (tmp_path / "detection.json").is_file()  # still writes a result


def test_analyze_unreadable_image_fails(tmp_path):
    res = analyze_reference_card(tmp_path / "does_not_exist.jpg", tmp_path)
    assert res.status == "fail"
    assert res.errors


def test_analysis_config_from_dict_defaults():
    cfg = AnalysisConfig.from_dict({"apriltag": {"expected_tag_ids": [0, 1, 2, 3]}})
    assert cfg.expected_tag_ids == [0, 1, 2, 3]
    assert cfg.rectified_w == 3000 and cfg.rectified_h == 1000
