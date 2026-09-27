"""TG-7 ORF reader (SPEC §4 Phase 8 S0): exiftool parsing and the LibRaw/exiftool cross-checks
run on canned data; a real ORF is read only when NEREUS_TG7_DATASET points at the dataset."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from host_tools.tg7 import exif as tg7_exif
from host_tools.tg7.exif import ExifError, parse
from host_tools.tg7.orf_io import black_per_position, check_black

# exiftool -j -n output for P9150344.orf (2026-09-26), trimmed to the parsed tags.
RECORD = {
    "SourceFile": "raw/1_reference_A_iso100/P9150344.orf",
    "WaterDepth": 15.5,
    "DateTimeOriginal": "2026:09:15 17:45:03",
    "OffsetTimeOriginal": "-08:00",
    "DateTimeUTC": "2026:09:16 01:45:03",
    "ExposureTime": 0.04,
    "ISO": 100,
    "FNumber": 2,
    "FocalLength": 4.5,
    "Flash": 16,
    "BlackLevel2": "257 256 256 257",
    "ColorMatrix": "420 -164 0 -64 392 -72 8 -112 360",
    "WB_RBLevels": "484 430 256 256",
    "Make": "OM Digital Solutions",
    "Model": "TG-7",
}


def test_parse_real_record():
    e = parse(RECORD)
    assert e["time_utc"] == "2026-09-16T01:45:03+00:00"  # camera clock was UTC-8 (PST)
    assert e["depth_m"] == 15.5 and e["exposure_s"] == 0.04 and e["fnumber"] == 2
    assert e["flash_fired"] is False
    assert e["black_level2"] == [257, 256, 256, 257]
    assert e["color_matrix"] == [[420, -164, 0], [-64, 392, -72], [8, -112, 360]]
    assert all(sum(row) == 256 for row in e["color_matrix"])  # neutral stays neutral
    assert e["wb_rb"] == pytest.approx((484 / 256, 430 / 256))


def test_flash_fired_is_bit_zero():
    assert parse({**RECORD, "Flash": 25})["flash_fired"] is True  # 0x19: on, fired


def test_inconsistent_clocks_fail_loudly():
    with pytest.raises(ExifError, match="DateTimeUTC"):
        parse({**RECORD, "DateTimeUTC": "2026:09:16 02:45:03"})


def test_missing_exiftool_is_a_clear_error(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("exiftool")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(ExifError, match="brew install exiftool"):
        tg7_exif.run_exiftool(["x.orf"])


def test_black_level_maps_libraw_channels_to_cfa_positions():
    # TG-7 LibRaw layout: raw_pattern [[1, 0], [2, 3]] with colours R, G, B, G2 → GRBG.
    assert black_per_position([257, 256, 258, 259], [[1, 0], [2, 3]]) == (256, 257, 258, 259)


def test_black_level_cross_check():
    # P9150349: BlackLevel2 (R, G1, G2, B) = 257 256 256 257, LibRaw (R, G, B, G2) = 257 256 257 256
    check_black([257, 256, 257, 256], [257, 256, 256, 257], "P9150349.orf")
    with pytest.raises(ExifError, match="disagrees"):
        check_black([257, 256, 256, 257], [257, 256, 256, 257], "x.orf")


DATASET = os.environ.get("NEREUS_TG7_DATASET")


@pytest.mark.skipif(not DATASET, reason="set NEREUS_TG7_DATASET to the TG-7 dataset folder")
def test_real_orf_decodes():
    pytest.importorskip("rawpy")
    from host_tools.tg7.orf_io import read_orf

    from nereus_camera_test_rig.color.raw_io import bin2x2, normalize

    frame = read_orf(Path(DATASET) / "raw" / "1_reference_A_iso100" / "P9150344.orf")
    assert frame.cfa == "GRBG" and frame.white_level == 4095
    assert frame.mosaic.shape == (3016, 4040) and frame.valid_crop == (0, 0, 4014, 3016)
    assert frame.source["jpeg_crop"] == (8, 8, 4000, 3000)
    assert frame.exposure_factor() == pytest.approx(0.04 * 100 / 4)
    linear, saturated, cfa = normalize(frame)
    binned, _ = bin2x2(linear, cfa, saturated)
    assert binned.shape == (1508, 2007, 3)
    red, green, _ = binned.reshape(-1, 3).mean(axis=0)
    assert 0 < red < 0.3 * green  # 15.5 m down: red is mostly gone
