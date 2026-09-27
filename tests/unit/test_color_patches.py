"""``patches`` stage (SPEC §4 Phase 8 S1.5): patch boxes mapped through the tag-centre
homography land on the right colours of the real V2 template, the binned-RAW path samples
the same pixels in mosaic coordinates, and the stage runs end to end."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.patches import (
    canonical_tag_quad,
    homography,
    patches,
    sample,
    sample_raw,
)
from nereus_camera_test_rig.color.raw_io import RawFrame
from nereus_camera_test_rig.color.stages import verify_fresh, write_stage

REPO = Path(__file__).resolve().parents[2]
CARD_PATH = REPO / "configs" / "cards" / "nereus_v2.yaml"
TEMPLATE = REPO / "tests" / "fixtures" / "reference_card" / "reference_card_template_3000x1000.png"
CARD = load_card(CARD_PATH)
# A perspective view of the canonical card inside a 2400 x 1600 image.
VIEW = cv2.getPerspectiveTransform(
    np.float32([[0, 0], [2999, 0], [2999, 999], [0, 999]]),
    np.float32([[300, 420], [2150, 300], [2080, 1050], [340, 1010]]))


def view_image():
    rgb = cv2.cvtColor(cv2.imread(str(TEMPLATE)), cv2.COLOR_BGR2RGB)
    return cv2.warpPerspective(rgb, VIEW, (2400, 1600), flags=cv2.INTER_LINEAR)


def view_quad():
    return cv2.perspectiveTransform(canonical_tag_quad(CARD).reshape(4, 1, 2), VIEW).reshape(4, 2)


def test_canonical_tag_quad_matches_the_measured_tag_centres():
    quad = canonical_tag_quad(CARD)
    measured = [CARD.tags[CARD.corner_map[k]].center for k in ("tl", "tr", "br", "bl")]
    np.testing.assert_allclose(quad, measured, atol=1.0)


def test_patches_sampled_in_a_perspective_view_match_the_card_truth():
    img, H = view_image(), homography(CARD, view_quad())
    for p in CARD.patches:
        stats = sample(img, H, p.box, clip=img >= 255)
        assert stats["in_frame"] and stats["n_px"] > 500, p.id
        np.testing.assert_allclose(stats["mean"], p.truth, atol=3, err_msg=p.id)
        assert max(stats["std"]) < 3, p.id  # central 60 % stays off the patch edges
        assert np.asarray(stats["cell_n"]).min() > 0
        np.testing.assert_allclose(np.asarray(stats["cells"]).reshape(-1, 3),
                                   np.tile(stats["mean"], (9, 1)), atol=3)


def test_cells_follow_the_card_axes():
    """A left-right ramp inside a patch shows up in the cell columns, not rows."""
    img = np.zeros((1000, 3000, 3), np.float32)
    img[..., 0] = np.arange(3000)[None, :]
    box = CARD.patch("gray_mid").box
    cells = np.asarray(sample(img, np.eye(3), box)["cells"])[..., 0]
    assert (np.diff(cells, axis=1) > 0).all() and np.allclose(cells, cells[0][None, :])


def test_partly_outside_the_frame_is_flagged():
    img = view_image()[:, :900]  # cut through the grey ramp
    stats = sample(img, homography(CARD, view_quad()), CARD.patch("gray_black").box)
    assert not stats["in_frame"]


def mosaic_from_binned(rgb: np.ndarray, black=64, white=1023, offset=(4, 2)) -> RawFrame:
    """GRBG mosaic whose 2x2 binning is ``rgb`` (linear 0..1), inside a padded readout."""
    h, w = rgb.shape[:2]
    counts = np.round(black + rgb * (white - black)).astype(np.uint16)
    active = np.zeros((2 * h, 2 * w), np.uint16)
    active[0::2, 0::2] = counts[..., 1]
    active[0::2, 1::2] = counts[..., 0]
    active[1::2, 0::2] = counts[..., 2]
    active[1::2, 1::2] = counts[..., 1]
    x0, y0 = offset
    mosaic = np.full((2 * h + y0 + 2, 2 * w + x0 + 6), black, np.uint16)
    mosaic[y0:y0 + 2 * h, x0:x0 + 2 * w] = active
    return RawFrame(mosaic=mosaic, cfa="GRBG", black_level=(black,) * 4, white_level=white,
                    valid_crop=(x0, y0, 2 * w, 2 * h), exposure_s=0.01, iso=100, fnumber=2.0)


def test_raw_path_samples_the_binned_mosaic_in_mosaic_coordinates():
    lin = view_image()[::2, ::2].astype(np.float32) / 255  # binned-resolution "linear" card
    frame = mosaic_from_binned(lin)
    # Binned (i, j) = view pixel (2i, 2j) and sits at mosaic (x0 + 2j + 0.5, y0 + 2i + 0.5).
    out = sample_raw(frame, view_quad() + np.array(frame.valid_crop[:2]) + 0.5, CARD)
    assert out["source"] == "raw_binned_linear"
    assert out["exposure_factor"] == pytest.approx(0.01 * 100 / 4)
    for p in CARD.patches:
        stats = out["patches"][p.id]
        np.testing.assert_allclose(stats["mean"], np.asarray(p.truth) / 255, atol=4 / 255,
                                   err_msg=p.id)
        # a truth of 255 is exactly the white level → clipped (white; cream's red); else not
        assert stats["clip_frac"] == [float(v == 255) for v in p.truth], p.id
        np.testing.assert_allclose(stats["mean_norm"], np.asarray(stats["mean"]) / 0.25,
                                   rtol=1e-4)
    assert set(out["patches"]) >= {"gray_mid_left", "gray_mid_right"}


def fake_reader(path: Path) -> RawFrame:
    return mosaic_from_binned(view_image()[::2, ::2].astype(np.float32) / 255)


def test_patches_stage_end_to_end(tmp_path):
    ds = tmp_path / "ds"
    (ds / "raw" / "ref").mkdir(parents=True)
    cv2.imwrite(str(ds / "raw" / "ref" / "A1.JPG"), cv2.cvtColor(view_image(), cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, 98])
    (ds / "raw" / "ref" / "A1.orf").write_bytes(b"fake")
    ingest_dir = tmp_path / "out" / "ingest"
    ingest_dir.mkdir(parents=True)
    with (ingest_dir / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["stem", "file", "jpeg", "has_raw"])
        w.writeheader()
        w.writerow({"stem": "A1", "file": "raw/ref/A1.orf", "jpeg": "raw/ref/A1.JPG",
                    "has_raw": "True"})
        w.writerow({"stem": "U1", "file": "raw/ref/U1.orf", "jpeg": "", "has_raw": "True"})
    write_stage(ingest_dir, "ingest", params={"dataset_dir": str(ds)})
    locate_dir = tmp_path / "out" / "locate"
    locate_dir.mkdir()
    offset = np.array(fake_reader(Path()).valid_crop[:2]) + 0.5
    corners = {"A1": {"located": True, "quad_raw": (view_quad() + offset).tolist(),
                      "quad_jpeg": view_quad().tolist()},
               "U1": {"located": False}}
    (locate_dir / "corners.json").write_text(json.dumps(corners))
    write_stage(locate_dir, "locate", upstream=[ingest_dir])

    summary = patches(locate_dir, CARD_PATH, raw_reader=fake_reader, workers=1)
    assert summary["located_frames"] == 1 and summary["errors"] == {}
    data = json.loads((Path(summary["out_dir"]) / "patches.json").read_text())
    assert list(data) == ["A1"]
    raw, jpg = data["A1"]["raw"]["patches"], data["A1"]["jpeg"]["patches"]
    assert data["A1"]["jpeg"]["source"] == "camera_jpeg_srgb8"
    for p in CARD.patches:  # RAW (x255) and JPEG agree on the same card
        np.testing.assert_allclose(np.asarray(raw[p.id]["mean"]) * 255, jpg[p.id]["mean"],
                                   atol=3, err_msg=p.id)
    assert "floor_frac" in jpg["gray_black"]
    assert str(locate_dir) in verify_fresh(Path(summary["out_dir"]))["upstream"]
