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
from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.jpeg_geometry import JpegMap
from nereus_camera_test_rig.color.locate import (
    MANUAL_FILE,
    locate,
    locate_frame,
    nearest_located,
    plausible,
    tag_geometry,
    window_for,
)
from nereus_camera_test_rig.color.raw_io import RawFrame
from nereus_camera_test_rig.color.stages import StaleInputError, verify_fresh, write_stage

REPO = Path(__file__).resolve().parents[2]
RENDER = REPO / "tests" / "fixtures" / "reference_card" / "Nereus_Reef_Reference_Card_V2.png"
CARD = REPO / "configs" / "cards" / "nereus_v2.yaml"
CORNERS = {"tl": 0, "tr": 1, "bl": 2, "br": 3}
CROP0, CROP8 = JpegMap.offset(0, 0), JpegMap.offset(8, 8)  # plain-crop RAW -> JPEG maps
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
    rec = locate_frame(None, RENDER, CORNERS, jpeg_map=CROP8)
    assert rec["located"] and rec["locate_method"] == "apriltag4"
    assert set(rec["found_at_scale"].values()) == {1.0}  # finest scale wins
    quad = np.array(rec["quad_jpeg"])
    expected = np.array([TRUE[0], TRUE[1], TRUE[3], TRUE[2]])  # TL, TR, BR, BL
    np.testing.assert_allclose(quad, expected, atol=1.0)
    np.testing.assert_allclose(np.array(rec["quad_raw"]), quad + 8, atol=1e-6)
    assert set(rec["tag_source"].values()) == {"jpeg"}  # no RAW given


def test_locate_frame_infers_a_painted_out_tag(tmp_path):
    img = cv2.imread(str(RENDER))
    x, y = TRUE[3]
    cv2.rectangle(img, (int(x) - 150, int(y) - 150), (int(x) + 150, int(y) + 150),
                  (255, 255, 255), -1)
    path = tmp_path / "masked.png"
    cv2.imwrite(str(path), img)
    rec = locate_frame(None, path, CORNERS, jpeg_map=CROP0)
    assert rec["locate_method"] == "apriltag3" and rec["inferred_corners"] == ["br"]
    assert rec["quad_jpeg"][2] == pytest.approx(list(TRUE[3]), abs=2.0)


@pytest.mark.parametrize("dropped", [0, 3])
def test_three_tags_under_strong_perspective_use_the_tag_corner_homography(tmp_path, dropped):
    """Close TG-7 cards are far from affine: the parallelogram guess missed by up to ~50 px.
    The 12-corner homography recovers the missing centre."""
    img = cv2.imread(str(RENDER))
    h, w = img.shape[:2]
    view = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]),
                                       np.float32([[200, 300], [2700, 50], [2800, 1900],
                                                   [150, 1500]]))
    warped = cv2.warpPerspective(img, view, (3000, 2000), borderValue=(255, 255, 255))
    x, y = cv2.perspectiveTransform(np.float32([[TRUE[dropped]]]), view)[0, 0]
    cv2.circle(warped, (int(x), int(y)), 190, (255, 255, 255), -1)
    path = tmp_path / "persp.png"
    cv2.imwrite(str(path), warped)
    truth = cv2.perspectiveTransform(
        np.float32([[TRUE[0], TRUE[1], TRUE[3], TRUE[2]]]), view)[0]
    idx = {0: 0, 3: 2}[dropped]
    geometry = tag_geometry(load_card(CARD))
    rec = locate_frame(None, path, CORNERS, jpeg_map=CROP0, geometry=geometry)
    old = locate_frame(None, path, CORNERS, jpeg_map=CROP0)
    assert rec["locate_method"] == "apriltag3" and rec["inference"] == "homography_tag_corners"
    assert old["inference"] == "parallelogram"
    err = np.linalg.norm(np.array(rec["quad_jpeg"][idx]) - truth[idx])
    old_err = np.linalg.norm(np.array(old["quad_jpeg"][idx]) - truth[idx])
    assert err < 2.0 and old_err > 20 * err, (err, old_err)
    print(f"homography {err:.2f} px vs parallelogram {old_err:.1f} px")


def test_locate_frame_without_tags_is_recorded_not_dropped(tmp_path):
    path = tmp_path / "blank.png"
    cv2.imwrite(str(path), np.full((400, 600, 3), 128, np.uint8))
    rec = locate_frame(None, path, CORNERS, jpeg_map=CROP0)
    assert rec == {"tags_found": [], "tag_source": {}, "found_at_scale": {},
                   "tag_side_px_min": {}, "tag_centers_raw": {}, "located": False,
                   "locate_method": None, "reason": "0 of 4 card tags found (need 3)"}


def test_locate_stage_end_to_end(tmp_path):
    ds = tmp_path / "ds"
    for cat, stem in [("1_reference_A_iso100", "A1"), ("4_no_card", "N1")]:
        (ds / "raw" / cat).mkdir(parents=True)
        (ds / "raw" / cat / f"{stem}.JPG").write_bytes(RENDER.read_bytes())
    ingest_dir = tmp_path / "out" / "synthetic-00000000" / "ingest"
    ingest_dir.mkdir(parents=True)
    with (ingest_dir / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["stem", "file", "jpeg", "has_raw", "category",
                                          "dive_id", "sweep_id", "time_utc"])
        w.writeheader()
        w.writerow({"stem": "A1", "file": "raw/1_reference_A_iso100/A1.JPG",
                    "jpeg": "raw/1_reference_A_iso100/A1.JPG", "has_raw": "False",
                    "category": "1_reference_A_iso100", "dive_id": 1, "sweep_id": 1,
                    "time_utc": "2026-09-16T01:00:00+00:00"})
        w.writerow({"stem": "N1", "file": "raw/4_no_card/N1.JPG", "jpeg": "raw/4_no_card/N1.JPG",
                    "has_raw": "False", "category": "4_no_card", "dive_id": 1, "sweep_id": "",
                    "time_utc": "2026-09-16T01:00:10+00:00"})
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
    rec = locate_frame(None, path, CORNERS, jpeg_map=CROP0)
    assert rec["located"], rec
    assert min(rec["found_at_scale"].values()) < 1.0
    expected = np.array([TRUE[0], TRUE[1], TRUE[3], TRUE[2]]) * 3
    np.testing.assert_allclose(np.array(rec["quad_jpeg"]), expected, atol=6.0)


def test_implausible_quads_are_rejected():
    good = np.array([[0, 0], [400, 0], [400, 100], [0, 100]], np.float32)
    assert plausible(good)
    assert not plausible(np.array([[0, 0], [100, 0], [100, 100], [0, 100]], np.float32))  # 1:1
    assert not plausible(np.array([[0, 0], [400, 100], [400, 0], [0, 100]], np.float32))  # bow-tie


# --- RAW first, window fallback, manual corners (S1.3a) ------------------------------------

def raw_from_image(img_bgr: np.ndarray) -> RawFrame:
    """A 12-bit RGGB RawFrame whose every photosite samples the image's grey level."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.uint16) * 16
    return RawFrame(mosaic=gray, cfa="RGGB", black_level=(0, 0, 0, 0), white_level=4095.0)


def test_raw_first_detection_records_the_source(tmp_path):
    reader = lambda p: raw_from_image(cv2.imread(str(RENDER)))  # noqa: E731
    rec = locate_frame(Path("x.orf"), None, CORNERS, jpeg_map=CROP8, raw_reader=reader)
    assert rec["locate_method"] == "apriltag4"
    assert set(rec["tag_source"].values()) == {"raw"}
    expected = np.array([TRUE[0], TRUE[1], TRUE[3], TRUE[2]])
    np.testing.assert_allclose(np.array(rec["quad_raw"]), expected, atol=1.0)
    np.testing.assert_allclose(np.array(rec["quad_jpeg"]), expected - 8, atol=1.0)


def test_jpeg_fills_a_tag_the_raw_missed(tmp_path):
    img = cv2.imread(str(RENDER))
    masked = img.copy()
    x, y = TRUE[1]
    cv2.rectangle(masked, (int(x) - 150, int(y) - 150), (int(x) + 150, int(y) + 150),
                  (255, 255, 255), -1)
    reader = lambda p: raw_from_image(masked)  # noqa: E731
    rec = locate_frame(Path("x.orf"), RENDER, CORNERS, jpeg_map=CROP0, raw_reader=reader)
    assert rec["locate_method"] == "apriltag4" and rec["inferred_corners"] == []
    assert rec["tag_source"] == {"0": "raw", "1": "jpeg", "2": "raw", "3": "raw"}


def test_window_search_only_looks_inside_the_window(tmp_path):
    reader = lambda p: raw_from_image(cv2.imread(str(RENDER)))  # noqa: E731
    around_card = (100.0, 500.0, 2900.0, 1450.0)
    inside = locate_frame(Path("x.orf"), None, CORNERS, CROP0, reader, window=around_card)
    assert inside["locate_method"] == "window4"
    empty = locate_frame(Path("x.orf"), None, CORNERS, CROP0, reader,
                         window=(0.0, 0.0, 200.0, 200.0))
    assert not empty["located"]


def test_window_and_neighbour_selection():
    box = window_for({"quad_raw": [[100, 100], [500, 100], [500, 200], [100, 200]]})
    assert box == (-300.0, -100.0, 900.0, 400.0)
    rows = {s: {"dive_id": d, "sweep_id": w, "time_utc": f"2026-09-16T01:00:{t:02d}+00:00"}
            for s, d, w, t in [("me", "1", "3", 30), ("near_other_sweep", "1", "2", 29),
                               ("same_sweep", "1", "3", 50), ("other_dive", "2", "3", 30),
                               ("unlocated", "1", "3", 31)]}
    corners = {s: {"located": s != "unlocated"} for s in rows}
    assert nearest_located("me", rows, corners, max_s=120) == "same_sweep"
    assert nearest_located("me", rows, corners, max_s=10) == "near_other_sweep"
    assert nearest_located("me", rows, corners, max_s=0.5) is None


def test_manual_corners_are_read_never_overwritten(tmp_path):
    ds = tmp_path / "ds"
    (ds / "raw" / "1_reference_A_iso100").mkdir(parents=True)
    blank = ds / "raw" / "1_reference_A_iso100" / "M1.JPG"
    cv2.imwrite(str(blank), np.full((300, 400, 3), 128, np.uint8))
    ingest_dir = tmp_path / "out" / "synthetic-00000000" / "ingest"
    ingest_dir.mkdir(parents=True)
    with (ingest_dir / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["stem", "file", "jpeg", "has_raw", "category",
                                          "dive_id", "sweep_id", "time_utc"])
        w.writeheader()
        w.writerow({"stem": "M1", "file": "raw/1_reference_A_iso100/M1.JPG",
                    "jpeg": "raw/1_reference_A_iso100/M1.JPG", "has_raw": "False",
                    "category": "1_reference_A_iso100", "dive_id": 1, "sweep_id": 1,
                    "time_utc": "2026-09-16T01:00:00+00:00"})
    write_stage(ingest_dir, "ingest", params={"dataset_dir": str(ds)})
    cfg = tmp_path / "dataset.yaml"
    cfg.write_text("jpeg_offset_in_raw: [8, 8]\n")
    locate_dir = ingest_dir.parent / "locate"
    locate_dir.mkdir()
    manual = {"M1": {"quad_raw": [[10, 10], [410, 10], [410, 110], [10, 110]],
                     "clicked_utc": "2026-09-27T00:00:00+00:00"}}
    (locate_dir / MANUAL_FILE).write_text(json.dumps(manual))
    before = (locate_dir / MANUAL_FILE).read_bytes()

    summary = locate(ingest_dir, CARD, cfg, workers=1)
    corners = json.loads((locate_dir / "corners.json").read_text())
    assert corners["M1"]["locate_method"] == "manual"
    assert corners["M1"]["quad_jpeg"][0] == [2.0, 2.0]
    assert (locate_dir / MANUAL_FILE).read_bytes() == before
    assert summary["manual_entries"] == 1
    assert verify_fresh(locate_dir)["params"]["manual_corners_sha256"]
    # a new click makes locate stale (SPEC §20)
    (locate_dir / MANUAL_FILE).write_text(json.dumps({**manual, "M2": {"skip": True}}))
    with pytest.raises(StaleInputError, match="changed"):
        verify_fresh(locate_dir)


def test_operator_skip_is_recorded_as_unlocated_with_reason(tmp_path):
    ds = tmp_path / "ds"
    (ds / "raw" / "1_reference_A_iso100").mkdir(parents=True)
    cv2.imwrite(str(ds / "raw" / "1_reference_A_iso100" / "K1.JPG"),
                np.full((300, 400, 3), 128, np.uint8))
    ingest_dir = tmp_path / "out" / "synthetic-00000000" / "ingest"
    ingest_dir.mkdir(parents=True)
    with (ingest_dir / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["stem", "file", "jpeg", "has_raw", "category",
                                          "dive_id", "sweep_id", "time_utc"])
        w.writeheader()
        w.writerow({"stem": "K1", "file": "raw/1_reference_A_iso100/K1.JPG",
                    "jpeg": "raw/1_reference_A_iso100/K1.JPG", "has_raw": "False",
                    "category": "1_reference_A_iso100", "dive_id": 1, "sweep_id": 1,
                    "time_utc": "2026-09-16T01:00:00+00:00"})
    write_stage(ingest_dir, "ingest", params={"dataset_dir": str(ds)})
    cfg = tmp_path / "dataset.yaml"
    cfg.write_text("jpeg_offset_in_raw: [0, 0]\n")
    (ingest_dir.parent / "locate").mkdir()
    (ingest_dir.parent / "locate" / MANUAL_FILE).write_text(
        json.dumps({"K1": {"skip": True, "reason": "card not usable in this frame"}}))
    locate(ingest_dir, CARD, cfg, workers=1)
    rec = json.loads((ingest_dir.parent / "locate" / "corners.json").read_text())["K1"]
    assert not rec["located"] and rec["manual_skip"]
    assert rec["reason"] == "operator: card not usable in this frame"


def test_manual_corners_path_comes_from_the_dataset_config(tmp_path):
    from nereus_camera_test_rig.color.locate import manual_corners_path

    cfg = tmp_path / "configs" / "ds.yaml"
    assert manual_corners_path(cfg, {"manual_corners": "ds_manual.json"}, tmp_path / "loc") == \
        tmp_path / "configs" / "ds_manual.json"
    assert manual_corners_path(cfg, {}, tmp_path / "loc") == tmp_path / "loc" / MANUAL_FILE
