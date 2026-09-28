"""DNG reader (SPEC §4 Phase 8 S0): synthetic DNGs in both layouts round-trip into a
``RawFrame`` that decodes to known values; unsupported variants fail loudly. A real
rpicam DNG is checked only when one is supplied (OQ-24): set NEREUS_IMX708_DNG."""

from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from _color_helpers import (  # noqa: E402
    FLAT,
    RATIONAL,
    SHORT,
    dng_tags,
    flat_mosaic,
    write_dng,
)

from nereus_camera_test_rig.color.raw_io import (  # noqa: E402
    _capture_value,
    bin2x2,
    normalize,
    read_dng,
)


@pytest.mark.parametrize("subifd", [False, True])
def test_dng_round_trip_in_both_layouts(tmp_path, subifd):
    frame = read_dng(write_dng(tmp_path / "x.dng", flat_mosaic(), dng_tags(), subifd=subifd))
    assert frame.cfa == "GRBG" and frame.mosaic.shape == (12, 16)
    assert frame.black_level == (256, 257, 258, 259) and frame.white_level == 4095
    assert frame.exposure_s == pytest.approx(1 / 250) and frame.fnumber == pytest.approx(1.8)
    assert frame.iso == 200 and frame.exposure_factor() == pytest.approx(200 / 250 / 1.8**2)
    assert frame.as_shot_wb == pytest.approx((2.0, 1.0, 1.5))
    np.testing.assert_allclose(frame.color_matrix, np.eye(3))
    assert "CalibrationIlluminant1=21" in frame.source["color_matrix"]
    assert frame.source["model"] == "TestModel"
    linear, saturated, cfa = normalize(frame)
    binned, _ = bin2x2(linear, cfa, saturated)
    np.testing.assert_allclose(binned, np.broadcast_to(FLAT, binned.shape), atol=2e-4)


def test_single_black_level_and_even_active_area(tmp_path):
    tags = [t for t in dng_tags(active=(2, 4, 12, 16)) if t[0] not in (50713, 50714)]
    tags.append((50714, RATIONAL, 1, (257, 1), True))
    frame = read_dng(write_dng(tmp_path / "x.dng", flat_mosaic(black=(257,) * 4), tags))
    assert frame.black_level == (257.0,) * 4
    assert frame.valid_crop == (4, 2, 12, 10)


def test_capture_values_from_the_exif_ifd():
    exif = SimpleNamespace(value={"ExposureTime": (1, 60), "ISOSpeedRatings": 100})
    tf = SimpleNamespace(pages=[SimpleNamespace(tags={34665: exif})])
    raw_page = SimpleNamespace(tags={})
    assert _capture_value(tf, raw_page, 33434, "ExposureTime") == pytest.approx(1 / 60)
    assert _capture_value(tf, raw_page, 34855, "ISOSpeedRatings") == 100
    assert _capture_value(tf, raw_page, 33437, "FNumber") is None


@pytest.mark.parametrize(
    "tags, kwargs, message",
    [
        (dng_tags(), {"compression": "zlib"}, "compressed CFA"),
        (dng_tags(active=(1, 0, 12, 16)), {}, "odd ActiveArea"),
        (dng_tags(cfa_codes=(0, 1, 3, 2)), {}, "not an RGB Bayer"),
        (dng_tags(extra=[(50712, SHORT, 2, (0, 4095), True)]), {}, "LinearizationTable"),
        ([t for t in dng_tags() if t[0] != 50713] + [(50713, SHORT, 2, (1, 2), True)], {},
         "BlackLevel"),
    ],
)
def test_unsupported_dngs_fail_loudly(tmp_path, tags, kwargs, message):
    path = write_dng(tmp_path / "x.dng", flat_mosaic(), tags, **kwargs)
    with pytest.raises(ValueError, match=message):
        read_dng(path)


def test_file_without_a_cfa_image_fails(tmp_path):
    path = tmp_path / "rgb.tif"
    tifffile.imwrite(path, np.zeros((4, 4, 3), np.uint8), photometric="rgb")
    with pytest.raises(ValueError, match="no full-resolution CFA"):
        read_dng(path)


REAL_DNG = os.environ.get("NEREUS_IMX708_DNG")


@pytest.mark.skipif(not REAL_DNG, reason="set NEREUS_IMX708_DNG to a real rpicam DNG (OQ-24)")
def test_real_rpicam_dng_decodes():
    frame = read_dng(REAL_DNG)
    linear, saturated, cfa = normalize(frame)
    binned, _ = bin2x2(linear, cfa, saturated)
    assert frame.mosaic.ndim == 2 and frame.white_level > max(frame.black_level)
    assert frame.exposure_s and frame.iso
    assert np.isfinite(binned).all() and 0 < float(np.median(binned)) < 1
    # rpicam keeps the colour tags in IFD0 and the raw in a SubIFD: both must be read
    assert frame.as_shot_wb and frame.as_shot_wb[1] == 1.0 and frame.color_matrix is not None
