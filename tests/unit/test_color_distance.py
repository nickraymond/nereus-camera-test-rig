"""``distance`` stage (SPEC §4 Phase 8 S1.4): PnP on the tag-centre quad recovers a known
pose, the medium sets the focal length, and the stage runs end to end on a synthetic
located dataset without dropping frames."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.distance import (
    card_object_points,
    distance,
    focal_px,
    medium_for,
    solve_pose,
)
from nereus_camera_test_rig.color.stages import StaleInputError, verify_fresh, write_stage

REPO = Path(__file__).resolve().parents[2]
CARD = REPO / "configs" / "cards" / "nereus_v2.yaml"
INTRINSICS = {"pixel_pitch_mm": 0.001554, "principal_point_raw": [2007.5, 1507.5],
              "water_focal_factor": 1.33}
PP = (2007.5, 1507.5)


def project(obj, f_px, rvec, tvec):
    K = np.array([[f_px, 0, PP[0]], [0, f_px, PP[1]], [0, 0, 1]])
    img, _ = cv2.projectPoints(obj.reshape(4, 1, 3), np.asarray(rvec, float),
                               np.asarray(tvec, float), K, None)
    return img.reshape(4, 2)


def test_object_points_are_the_physical_tag_centre_rectangle():
    obj = card_object_points(load_card(CARD))
    np.testing.assert_allclose(obj[2], [364.9, 91.566, 0])  # BR, TL at the origin


@pytest.mark.parametrize("tilt_deg", [0.0, 25.0, 50.0])
def test_pnp_recovers_distance_and_tilt(tilt_deg):
    obj = card_object_points(load_card(CARD))
    f = focal_px(4.5, INTRINSICS, "water")
    rvec, tvec = [0.0, np.radians(tilt_deg), 0.0], [-150.0, 80.0, 1200.0]
    quad = project(obj, f, rvec, tvec)
    pose = solve_pose(quad, obj, f, PP)
    R, _ = cv2.Rodrigues(np.asarray(rvec))
    centre = R @ obj.mean(axis=0) + np.asarray(tvec)
    assert pose["z_m"] == pytest.approx(centre[2] / 1000, abs=1e-4)
    assert pose["range_m"] == pytest.approx(np.linalg.norm(centre) / 1000, abs=1e-4)
    assert pose["tilt_deg"] == pytest.approx(tilt_deg, abs=0.05)
    assert pose["reproj_rms_px"] < 1e-3


def test_fronto_parallel_distance_matches_the_pinhole_formula():
    """z = f_px * W_mm / w_px (brief §7 P1.1 step 3) for a card square to the camera."""
    obj = card_object_points(load_card(CARD))
    f = focal_px(4.5, INTRINSICS, "air")
    quad = project(obj - obj.mean(axis=0), f, [0, 0, 0], [0, 0, 800.0])
    width_px = quad[1, 0] - quad[0, 0]
    assert solve_pose(quad, obj, f, PP)["z_m"] == pytest.approx(f * 364.9 / width_px / 1000,
                                                                rel=1e-6)


def test_water_multiplies_the_focal_length():
    assert focal_px(4.5, INTRINSICS, "air") == pytest.approx(2895.75, abs=0.01)
    assert focal_px(4.5, INTRINSICS, "water") == pytest.approx(1.33 * 2895.75, abs=0.01)


def test_medium_comes_from_the_dataset_config():
    cfg = {"medium": {"default": "water", "air": ["A1"], "split": ["S1"]}}
    assert [medium_for(s, cfg) for s in ("A1", "S1", "X")] == ["air", "split", "water"]
    assert medium_for("X", {}) == "water"


def make_located(tmp_path: Path, quad) -> tuple[Path, Path, Path]:
    ingest_dir = tmp_path / "ds1" / "ingest"
    ingest_dir.mkdir(parents=True)
    with (ingest_dir / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["stem", "focal_length_mm"])
        w.writeheader()
        w.writerows([{"stem": s, "focal_length_mm": 4.5} for s in ("W", "A", "S", "U")])
    write_stage(ingest_dir, "ingest")
    locate_dir = tmp_path / "ds1" / "locate"
    locate_dir.mkdir()
    located = {"located": True, "locate_method": "apriltag4", "quad_raw": quad.tolist()}
    corners = {"W": located, "A": located, "S": located,
               "U": {"located": False, "locate_method": None, "reason": "0 of 4"}}
    (locate_dir / "corners.json").write_text(json.dumps(corners))
    write_stage(locate_dir, "locate", upstream=[ingest_dir])
    dataset = tmp_path / "dataset.yaml"
    dataset.write_text(yaml.safe_dump({"camera": "cam", "medium": {"air": ["A"],
                                                                   "split": ["S"]}}))
    calib = tmp_path / "cam.yaml"
    calib.write_text(yaml.safe_dump({"camera_id": "cam", "provisional": True,
                                     "intrinsics": INTRINSICS}))
    return locate_dir, dataset, calib


def test_distance_stage_end_to_end(tmp_path):
    obj = card_object_points(load_card(CARD))
    quad = project(obj - obj.mean(axis=0), focal_px(4.5, INTRINSICS, "water"),
                   [0.1, 0.2, 0.0], [0, 0, 1500.0])
    locate_dir, dataset, calib = make_located(tmp_path, quad)
    summary = distance(locate_dir, calib, dataset, CARD)
    out = json.loads((Path(summary["out_dir"]) / "distances.json").read_text())
    assert set(out) == {"W", "A", "S", "U"}  # nothing dropped
    assert out["W"]["z_m"] == pytest.approx(1.5, abs=1e-3) and out["W"]["z_provisional"]
    # The same pixels read in air: focal length / 1.33 → distance / 1.33.
    assert out["A"]["z_m"] == pytest.approx(1.5 / 1.33, abs=1e-3)
    assert out["S"]["z_m"] is None and "waterline" in out["S"]["reason"]
    assert out["U"]["z_m"] is None and out["U"]["reason"] == "card not located"
    assert summary["with_z"] == 2 and summary["by_medium"] == {"water": 2, "air": 1, "split": 1}

    record = verify_fresh(Path(summary["out_dir"]))
    assert record["params"]["provisional"] is True
    calib.write_text(calib.read_text() + "# refined\n")
    with pytest.raises(StaleInputError, match="changed"):
        verify_fresh(Path(summary["out_dir"]))
