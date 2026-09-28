"""RAW ↔ camera-JPEG map (OQ-42): the radial model inverts, fits back a known map, drives
``locate``'s JPEG coordinates and the JPEG patch sampling, and the ``jpeg-map`` stage fits and
validates it end to end on synthetic frames."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.jpeg_geometry import JpegMap, fit_robust, read_jpeg
from nereus_camera_test_rig.color.jpeg_map import jpeg_map
from nereus_camera_test_rig.color.locate import locate_frame
from nereus_camera_test_rig.color.patches import canonical_tag_quad, homography, sample
from nereus_camera_test_rig.color.stages import verify_fresh, write_stage

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tests" / "fixtures" / "reference_card"
RENDER = FIX / "Nereus_Reef_Reference_Card_V2.png"
TEMPLATE = FIX / "reference_card_template_3000x1000.png"
CARD = load_card(REPO / "configs" / "cards" / "nereus_v2.yaml")
CORNERS = {"tl": 0, "tr": 1, "bl": 2, "br": 3}
TRUE = {0: (232.5, 652.5), 1: (2767.5, 652.5), 2: (232.5, 1288.5), 3: (2767.5, 1288.5)}
# The TG-7 fit (jpeg-map stage, 2026-09-27), in RAW-mosaic px.
TG7 = JpegMap((2006.6, 1511.4), (1997.97, 1502.85), (0.999396, 0.018916, 0.001665))


def remap_to_jpeg(raw_img: np.ndarray, m: JpegMap, size) -> np.ndarray:
    """The image a camera would store: JPEG pixel u shows RAW position m.to_raw(u)."""
    uu, vv = np.meshgrid(np.arange(size[0]), np.arange(size[1]))
    src = m.to_raw(np.stack([uu, vv], axis=-1).astype(np.float64)).astype(np.float32)
    return cv2.remap(raw_img, src[..., 0], src[..., 1], cv2.INTER_LINEAR,
                     borderValue=(255, 255, 255))


def test_to_raw_inverts_to_jpeg():
    rng = np.random.default_rng(0)
    pts = rng.uniform([0, 0], [4040, 3016], (500, 2))
    np.testing.assert_allclose(TG7.to_raw(TG7.to_jpeg(pts)), pts, atol=1e-4)
    # the measured size of the TG-7 remap (OQ-42): ~16 px at 950 px, ~130 px at 1800 px
    for r, shift in ((950, 16), (1800, 130)):
        p = np.array([TG7.centre_raw[0] + r, TG7.centre_raw[1]])
        moved = np.linalg.norm(TG7.to_jpeg(p) - TG7.centre_jpeg) - r
        assert moved == pytest.approx(shift, abs=0.25 * shift)


def test_config_block_and_plain_offset_fallback():
    assert np.allclose(JpegMap.from_config({"jpeg_offset_in_raw": [8, 8]}).to_jpeg([100, 50]),
                       [92, 42])
    m = JpegMap.from_config({"jpeg_from_raw": {**TG7.as_dict(),
                                               "exclude_frames": {"P1": "shifted"}}})
    assert m.k == TG7.k and m.exclude_frames == {"P1": "shifted"}
    assert JpegMap.from_config(m.as_dict() | {"jpeg_from_raw": m.as_dict()}).k == TG7.k


def test_fit_recovers_a_known_map_and_trims_outliers():
    rng = np.random.default_rng(1)
    raw = rng.uniform([200, 200], [3800, 2800], (300, 2))
    jpg = TG7.to_jpeg(raw) + rng.normal(0, 0.5, raw.shape)
    jpg[:5] += 60  # mismatched detections
    fitted, keep, _ = fit_robust(raw, jpg, start=raw.mean(axis=0))
    assert not keep[:5].any() and keep[5:].mean() > 0.97
    grid = np.stack(np.meshgrid(np.linspace(0, 4040, 9), np.linspace(0, 3016, 7)), -1)
    assert np.abs(fitted.to_jpeg(grid) - TG7.to_jpeg(grid)).max() < 1.5


def test_read_jpeg_keeps_the_stored_orientation(tmp_path):
    img = np.zeros((40, 60, 3), np.uint8)
    img[:, :30] = 255
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 CW on display
    Image.fromarray(img).save(tmp_path / "r.jpg", exif=exif, quality=95)
    assert cv2.imread(str(tmp_path / "r.jpg")).shape[:2] == (60, 40)  # OpenCV applies it
    stored = read_jpeg(tmp_path / "r.jpg")
    assert stored.shape[:2] == (40, 60) and stored[20, 5].mean() > 200


STRONG = JpegMap((1300.0, 1000.0), (1290.0, 995.0), (1.0, 0.06))


def test_jpeg_fallback_tags_are_mapped_back_to_raw(tmp_path):
    render = cv2.imread(str(RENDER))
    path = tmp_path / "j.png"
    mild = JpegMap((1500.0, 970.0), (1490.0, 964.0), (1.0, 0.02))  # tags stay in frame
    cv2.imwrite(str(path), remap_to_jpeg(render, mild, (3000, 1941)))
    rec = locate_frame(None, path, CORNERS, jpeg_map=mild)
    assert rec["locate_method"] == "apriltag4"
    raw_true = np.array([TRUE[0], TRUE[1], TRUE[3], TRUE[2]])
    np.testing.assert_allclose(rec["quad_raw"], raw_true, atol=1.5)
    np.testing.assert_allclose(rec["quad_jpeg"], mild.to_jpeg(raw_true), atol=1.5)


def test_jpeg_patches_sampled_through_the_map_hit_the_raw_card_area():
    view = cv2.getPerspectiveTransform(np.float32([[0, 0], [2999, 0], [2999, 999], [0, 999]]),
                                       np.float32([[300, 420], [2150, 300], [2080, 1050],
                                                   [340, 1010]]))
    raw_img = cv2.warpPerspective(cv2.cvtColor(cv2.imread(str(TEMPLATE)), cv2.COLOR_BGR2RGB),
                                  view, (2400, 1600))
    jpg = remap_to_jpeg(raw_img, STRONG, (2600, 1750))
    quad_raw = cv2.perspectiveTransform(canonical_tag_quad(CARD).reshape(4, 1, 2),
                                        view).reshape(4, 2)
    H = homography(CARD, quad_raw)
    naive = homography(CARD, STRONG.to_jpeg(quad_raw))  # the JPEG quad, projectively
    for p in CARD.patches:
        stats = sample(jpg, H, p.box, warp=STRONG)
        np.testing.assert_allclose(stats["mean"], p.design or p.truth, atol=4, err_msg=p.id)
        assert max(stats["std"]) < 4, p.id
    # the card is not projective in the JPEG: a JPEG-quad homography puts the patch centres
    # several pixels away from where the map puts them
    centres = np.array([[[p.box.x + p.box.w / 2, p.box.y + p.box.h / 2]] for p in CARD.patches])
    mapped = STRONG.to_jpeg(cv2.perspectiveTransform(centres, H).reshape(-1, 2))
    assert np.linalg.norm(cv2.perspectiveTransform(centres, naive).reshape(-1, 2) - mapped,
                          axis=1).max() > 5


def _synthetic_dataset(tmp_path, true_map: JpegMap, shifted: str):
    """Nine frames with the card on a 3 x 3 grid of a 2000 x 1500 RAW."""
    render = cv2.imread(str(RENDER))
    ds, out = tmp_path / "ds", tmp_path / "out"
    (ds / "cat").mkdir(parents=True)
    ingest_dir, locate_dir = out / "ingest", out / "locate"
    ingest_dir.mkdir(parents=True)
    locate_dir.mkdir()
    rows, corners = [], {}
    for n, (x, y) in enumerate([(x, y) for y in (40, 530, 1030) for x in (40, 650, 1260)]):
        stem = f"F{n}"
        scale = 700 / 3000
        A = np.array([[scale, 0, x], [0, scale, y]])
        raw_img = cv2.warpAffine(render, A, (2000, 1500), borderValue=(255, 255, 255))
        jpg = remap_to_jpeg(raw_img, true_map, (2000, 1500))
        if stem == shifted:
            jpg = cv2.warpAffine(jpg, np.float32([[1, 0, 15], [0, 1, 0]]), (2000, 1500),
                                 borderValue=(255, 255, 255))
        cv2.imwrite(str(ds / "cat" / f"{stem}.jpg"), jpg, [cv2.IMWRITE_JPEG_QUALITY, 97])
        centres = {str(i): (A @ np.array([*c, 1.0])).tolist() for i, c in TRUE.items()}
        corners[stem] = {"located": True, "tag_centers_raw": centres,
                         "tag_source": {i: "raw" for i in centres}}
        rows.append({"stem": stem, "jpeg": f"cat/{stem}.jpg", "category": "cat",
                     "dive_id": str(1 + n % 2)})
    with (ingest_dir / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    write_stage(ingest_dir, "ingest", params={"dataset_dir": str(ds)})
    (locate_dir / "corners.json").write_text(json.dumps(corners))
    write_stage(locate_dir, "locate", upstream=[ingest_dir])
    return locate_dir


def test_jpeg_map_stage_fits_validates_and_flags_a_shifted_frame(tmp_path):
    true_map = JpegMap((1000.0, 750.0), (992.0, 744.0), (1.0, 0.03))
    locate_dir = _synthetic_dataset(tmp_path, true_map, shifted="F2")
    cfg = tmp_path / "dataset.yaml"
    cfg.write_text("jpeg_offset_in_raw: [8, 6]\n")
    summary = jpeg_map(locate_dir, cfg, workers=1)
    assert summary["pairs"] == 36 and summary["inliers"] == 32  # F2's four tags trimmed
    fitted = JpegMap.from_config({"jpeg_from_raw": summary["fitted"]})
    pts = np.stack(np.meshgrid(np.linspace(80, 1920, 9), np.linspace(180, 1300, 7)), -1)
    assert np.abs(fitted.to_jpeg(pts) - true_map.to_jpeg(pts)).max() < 1.5
    assert summary["fit_error"]["max_px"] < 1.5
    assert list(summary["frames_over_tolerance"]) == ["F2"]
    assert summary["frames_over_tolerance"]["F2"]["mean_shift_px"][0] == pytest.approx(15, abs=1)
    assert not summary["config_matches"]  # the plain offset is ~40 px off at the corners
    assert "jpeg_from_raw:" in summary["config_block"]
    verify_fresh(Path(summary["out_dir"]))
