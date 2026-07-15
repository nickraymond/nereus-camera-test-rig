"""Unit tests for the JPEG dimension probe — Spec §19."""

from __future__ import annotations

import pytest
from _capture_helpers import make_jpeg

from nereus_camera_test_rig.capture.image_probe import ImageProbeError, jpeg_dimensions


def test_reads_dimensions(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_bytes(make_jpeg(4608, 2592))
    assert jpeg_dimensions(p) == (4608, 2592)


def test_reads_dimensions_small(tmp_path):
    p = tmp_path / "s.jpg"
    p.write_bytes(make_jpeg(640, 480))
    assert jpeg_dimensions(p) == (640, 480)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ImageProbeError):
        jpeg_dimensions(tmp_path / "nope.jpg")


def test_not_a_jpeg_raises(tmp_path):
    p = tmp_path / "bad.jpg"
    p.write_bytes(b"not a jpeg at all")
    with pytest.raises(ImageProbeError, match="bad SOI"):
        jpeg_dimensions(p)


def test_no_sof_raises(tmp_path):
    p = tmp_path / "nosof.jpg"
    p.write_bytes(b"\xff\xd8" + b"\xff\xe0\x00\x04AB" + b"\xff\xd9")
    with pytest.raises(ImageProbeError, match="no SOF"):
        jpeg_dimensions(p)
