"""``locate`` stage + 3-of-4-tag inference (SPEC §4 Phase 8 S1): on the real V2 card render,
with one tag painted out, and end-to-end over a synthetic ingested dataset."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from nereus_camera_test_rig.analysis.apriltag_detector import DetectionOutcome, TagDetection
from nereus_camera_test_rig.analysis.reference_card import (
    CardLocalizationError,
    infer_card_corners_from_tags,
    localize_card,
)
from nereus_camera_test_rig.color.locate import locate, locate_frame, plausible
from nereus_camera_test_rig.color.stages import verify_fresh, write_stage

REPO = Path(__file__).resolve().parents[2]
RENDER = REPO / "tests" / "fixtures" / "reference_card" / "Nereus_Reef_Reference_Card_V2.png"
CARD = REPO / "configs" / "cards" / "nereus_v2.yaml"
CORNERS = {"tl": 0, "tr": 1, "bl": 2, "br": 3}
# Tag centres detected in the 3000x1941 render (fixture README).
TRUE = {0: (232.5, 652.5), 1: (2767.5, 652.5), 2: (232.5, 1288.5), 3: (2767.5, 1288.5)}


def outcome(ids, affine=np.array([[1.0, 0.2, 5.0], [-0.1, 0.9, 7.0]])):
    tags = {}
    for i in ids:
        x, y = affine @ np.array([*TRUE[i], 1.0])
        tags[i] = TagDetection(i, np.zeros((4, 2)), (float(x), float(y)), 50.0)
    return DetectionOutcome(tags=tags)


@pytest.mark.parametrize("dropped", [0, 1, 2, 3])
def test_three_tags_infer_the_fourth_exactly_under_an_affine_view(dropped):
    full, _ = infer_card_corners_from_tags(outcome([0, 1, 2, 3]), CORNERS)
    quad, inferred = infer_card_corners_from_tags(
        outcome([i for i in range(4) if i != dropped]), CORNERS, min_tags=3)
    assert inferred == ({v: k for k, v in CORNERS.items()}[dropped],)
    np.testing.assert_allclose(quad, full, atol=1e-3)


def test_default_still_requires_all_four_tags():
    with pytest.raises(CardLocalizationError):
        localize_card(outcome([0, 1, 2]))  # Phase 2-6 behaviour unchanged
    assert localize_card(outcome([0, 1, 2]), min_tags=3).inferred == ("br",)
    with pytest.raises(CardLocalizationError):
        infer_card_corners_from_tags(outcome([0, 1]), CORNERS, min_tags=3)


def test_locate_frame_on_the_card_render(tmp_path):
    rec = locate_frame(RENDER, CORNERS, offset=(8, 8))
    assert rec["located"] and rec["locate_method"] == "apriltag4"
    assert set(rec["found_at_scale"].values()) == {1.0}  # finest scale wins
    quad = np.array(rec["quad_jpeg"])
    expected = np.array([TRUE[0], TRUE[1], TRUE[3], TRUE[2]])  # TL, TR, BR, BL
    np.testing.assert_allclose(quad, expected, atol=1.0)
    np.testing.assert_allclose(np.array(rec["quad_raw"]), quad + 8, atol=1e-6)


def test_locate_frame_infers_a_painted_out_tag(tmp_path):
    img = cv2.imread(str(RENDER))
    x, y = TRUE[3]
    cv2.rectangle(img, (int(x) - 150, int(y) - 150), (int(x) + 150, int(y) + 150),
                  (255, 255, 255), -1)
    path = tmp_path / "masked.png"
    cv2.imwrite(str(path), img)
    rec = locate_frame(path, CORNERS, offset=(0, 0), retry_scale=1)
    assert rec["locate_method"] == "apriltag3" and rec["inferred_corners"] == ["br"]
    assert rec["quad_jpeg"][2] == pytest.approx(list(TRUE[3]), abs=2.0)


def test_locate_frame_without_tags_is_recorded_not_dropped(tmp_path):
    path = tmp_path / "blank.png"
    cv2.imwrite(str(path), np.full((400, 600, 3), 128, np.uint8))
    rec = locate_frame(path, CORNERS, offset=(0, 0), retry_scale=1)
    assert rec == {"tags_found": [], "found_at_scale": {}, "tag_side_px_min": {},
                   "located": False, "locate_method": None,
                   "reason": "0 of 4 card tags found (need 3)"}


def test_locate_stage_end_to_end(tmp_path):
    ds = tmp_path / "ds"
    for cat, stem in [("1_reference_A_iso100", "A1"), ("4_no_card", "N1")]:
        (ds / "raw" / cat).mkdir(parents=True)
        (ds / "raw" / cat / f"{stem}.JPG").write_bytes(RENDER.read_bytes())
    ingest_dir = tmp_path / "out" / "synthetic-00000000" / "ingest"
    ingest_dir.mkdir(parents=True)
    with (ingest_dir / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["stem", "jpeg", "category", "dive_id", "sweep_id"])
        w.writeheader()
        w.writerow({"stem": "A1", "jpeg": "raw/1_reference_A_iso100/A1.JPG",
                    "category": "1_reference_A_iso100", "dive_id": 1, "sweep_id": 1})
        w.writerow({"stem": "N1", "jpeg": "raw/4_no_card/N1.JPG", "category": "4_no_card",
                    "dive_id": 1, "sweep_id": ""})
    write_stage(ingest_dir, "ingest", params={"dataset_dir": str(ds)})
    cfg = tmp_path / "dataset.yaml"
    cfg.write_text("jpeg_offset_in_raw: [8, 8]\nno_card_categories: [4_no_card]\n")

    summary = locate(ingest_dir, CARD, cfg, workers=1)
    out = Path(summary["out_dir"])
    corners = json.loads((out / "corners.json").read_text())
    assert list(corners) == ["A1"] and corners["A1"]["locate_method"] == "apriltag4"
    assert summary["by_category"] == {"1_reference_A_iso100": {"apriltag4": 1}}
    record = verify_fresh(out)
    assert str(ingest_dir) in record["upstream"] and record["stage"] == "locate"


def test_downscaled_pass_rescues_a_card_too_large_for_native_detection(tmp_path):
    big = cv2.resize(cv2.imread(str(RENDER)), None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (0, 0), 6)  # large, soft tags: the TG-7 near-frame case
    path = tmp_path / "big.png"
    cv2.imwrite(str(path), big)
    rec = locate_frame(path, CORNERS, offset=(0, 0), retry_scale=0)
    assert rec["located"], rec
    assert min(rec["found_at_scale"].values()) < 1.0
    expected = np.array([TRUE[0], TRUE[1], TRUE[3], TRUE[2]]) * 3
    np.testing.assert_allclose(np.array(rec["quad_jpeg"]), expected, atol=6.0)


def test_implausible_quads_are_rejected():
    good = np.array([[0, 0], [400, 0], [400, 100], [0, 100]], np.float32)
    assert plausible(good)
    assert not plausible(np.array([[0, 0], [100, 0], [100, 100], [0, 100]], np.float32))  # 1:1
    assert not plausible(np.array([[0, 0], [400, 100], [400, 0], [0, 100]], np.float32))  # bow-tie
