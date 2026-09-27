"""The whole Phase 8 S1 chain on a tiny synthetic dataset (SPEC §4 S1 exit):
ingest → locate → distance → patches → qc → report, with a RAW reader that Bayer-samples a
rendered V2 card. ``report`` reads only saved stage outputs and fails loudly when stale."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from nereus_camera_test_rig.color.distance import distance
from nereus_camera_test_rig.color.ingest import ingest
from nereus_camera_test_rig.color.locate import locate
from nereus_camera_test_rig.color.metrics import srgb8_to_linear
from nereus_camera_test_rig.color.patches import patches
from nereus_camera_test_rig.color.qc import qc
from nereus_camera_test_rig.color.raw_io import RawFrame
from nereus_camera_test_rig.color.report import report
from nereus_camera_test_rig.color.stages import StaleInputError

REPO = Path(__file__).resolve().parents[2]
CARD = REPO / "configs" / "cards" / "nereus_v2.yaml"
RENDER = REPO / "tests" / "fixtures" / "reference_card" / "Nereus_Reef_Reference_Card_V2.png"
REF = "1_reference_A_iso100"
T0 = datetime(2026, 9, 16, 1, 40, tzinfo=timezone.utc)
SHOTS = {"S1": (REF, 0), "S2": (REF, 30), "S3": (REF, 60), "N1": ("4_no_card", 90)}
PAD, BLACK, WHITE = 8, 256, 4095


def scene() -> np.ndarray:
    card = cv2.imread(str(RENDER))
    h, w = card.shape[:2]
    view = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]),
                                       np.float32([[140, 260], [1480, 200], [1500, 1080],
                                                   [120, 1010]]))
    return cv2.warpPerspective(card, view, (1600, 1200), borderValue=(90, 110, 40))


def raw_reader(path: Path) -> RawFrame:
    """GRBG mosaic that Bayer-samples the shot's JPEG (linearized), padded by 8 px."""
    rgb = srgb8_to_linear(cv2.cvtColor(cv2.imread(str(path.with_suffix(".JPG"))),
                                       cv2.COLOR_BGR2RGB)) * 0.8
    counts = np.round(BLACK + rgb * (WHITE - BLACK)).astype(np.uint16)
    mosaic = np.full((counts.shape[0] + 2 * PAD, counts.shape[1] + 2 * PAD), BLACK, np.uint16)
    active = mosaic[PAD:-PAD, PAD:-PAD]
    active[0::2, 0::2] = counts[0::2, 0::2, 1]
    active[0::2, 1::2] = counts[0::2, 1::2, 0]
    active[1::2, 0::2] = counts[1::2, 0::2, 2]
    active[1::2, 1::2] = counts[1::2, 1::2, 1]
    return RawFrame(mosaic=mosaic, cfa="GRBG", black_level=(BLACK,) * 4, white_level=WHITE,
                    valid_crop=(PAD, PAD, counts.shape[1], counts.shape[0]),
                    exposure_s=0.01, iso=100, fnumber=2.0)


def metadata(paths):
    return [{"path": str(p), "time_utc": (T0 + timedelta(seconds=SHOTS[Path(p).stem][1]))
             .isoformat(), "depth_m": 10.0, "exposure_s": 0.01, "iso": 100, "fnumber": 2.0,
             "focal_length_mm": 4.5, "flash_fired": False, "black_level2": [BLACK] * 4}
            for p in paths]


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("chain")
    ds = tmp / "ds"
    img = scene()
    for stem, (cat, _) in SHOTS.items():
        (ds / "raw" / cat).mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(ds / "raw" / cat / f"{stem}.JPG"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        (ds / "raw" / cat / f"{stem}.orf").write_bytes(b"raw")
    cfg = tmp / "dataset.yaml"
    cfg.write_text(yaml.safe_dump({
        "dataset": "synthetic", "camera": "cam", "jpeg_offset_in_raw": [PAD, PAD],
        "dives": {1: {"site": "a", "start_utc": "2026-09-16T01:30:00Z",
                      "end_utc": "2026-09-16T02:00:00Z"}},
        "sites": {"a": {"lat": 34.0, "lon": -119.75}},
        "card_damage": {"from_stem": "S3", "patches": ["gray_white", "gray_mid_left"],
                        "clean_reference": ["S1", "S2"]}}))
    calib = tmp / "cam.yaml"
    calib.write_text(yaml.safe_dump({"camera_id": "cam", "provisional": True, "intrinsics": {
        "pixel_pitch_mm": 0.001554, "principal_point_raw": [808, 608]}}))
    root = Path(ingest(ds, cfg, tmp / "out", metadata)["out_dir"]).parent
    locate(root / "ingest", CARD, cfg, raw_reader=raw_reader, workers=1)
    distance(root / "locate", calib, cfg, CARD)
    patches(root / "locate", CARD, raw_reader=raw_reader, workers=1)
    qc(root / "patches", cfg, CARD)
    for raw in ds.rglob("*.orf"):  # report must not need the RAW (nor re-run locate)
        raw.unlink()
    summary = report(root / "qc", root / "distance", cfg, CARD, workers=1)
    return root, cfg, summary


def test_every_stage_ran_and_the_report_scores_both_card_conditions(chain):
    root, _, summary = chain
    corners = json.loads((root / "locate" / "corners.json").read_text())
    assert set(corners) == {"S1", "S2", "S3"} and all(c["located"] for c in corners.values())
    assert all(c["tag_source"]["0"] == "raw" for c in corners.values())
    z = json.loads((root / "distance" / "distances.json").read_text())["S1"]
    assert z["z_m"] is not None and z["z_provisional"]
    assert summary["frames_scored"] == 3
    assert summary["by_condition"]["clean"]["frames"] == 2
    assert summary["by_condition"]["damaged"]["frames"] == 1
    # the rendered card is neutral: as-shot grey error is small
    assert summary["by_condition"]["clean"]["psi_median_deg"] < 3
    qcd = json.loads((root / "qc" / "qc.json").read_text())
    assert not qcd["S3"]["patches"]["gray_mid"]["usable"]  # map: parent of gray_mid_left


def test_report_page_and_scores_file(chain):
    root, _, summary = chain
    page = (root / "report" / "index.html").read_text()
    for text in ("Before", "QC", "Dataset timeline", "Contact sheets", "S3", "Damaged card"):
        assert text in page
    assert page.count("data:image/jpeg;base64,") == 3
    rows = (root / "report" / "scores_before.csv").read_text().splitlines()
    assert rows[0].startswith("stem,dive_id") and len(rows) == 4


def test_report_fails_loudly_on_stale_input(chain):
    root, cfg, _ = chain
    (root / "qc" / "qc.json").touch()
    (root / "patches" / "stage.json").write_text(
        (root / "patches" / "stage.json").read_text().replace('"patches"', '"patches" ', 1))
    with pytest.raises(StaleInputError):
        report(root / "qc", root / "distance", cfg, CARD, workers=1)
