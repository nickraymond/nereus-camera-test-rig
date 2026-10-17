"""sync_video.py helpers: per-frame metadata survives an rpicam-vid that died at teardown."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("sync_video", ROOT / "scripts" / "sync_video.py")
sv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sv)


def _frames(n):
    return [{"ExposureTime": 33000, "ScalerCrop": "(0, 0)/4608x2592",
             "SensorTimestamp": 1_000_000_000 * (i + 1)} for i in range(n)]


def test_complete_metadata_reads_as_is(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(_frames(3), indent=4))
    assert len(sv.read_frame_metadata(p)) == 3


def test_metadata_cut_mid_record_keeps_every_complete_frame(tmp_path):
    # gate006 c1 (2026-10-14): rpicam-vid segfaulted at exit; the file ended inside a record
    # ('"SensorTimestamp": 77849768000' with no closing brace or bracket).
    text = json.dumps(_frames(4), indent=4)
    cut = text.rfind('"SensorTimestamp"')
    p = tmp_path / "m.json"
    p.write_text(text[:cut + 30])
    got = sv.read_frame_metadata(p)
    assert [f["SensorTimestamp"] for f in got] == [1e9, 2e9, 3e9]


def test_missing_or_garbage_metadata_is_empty(tmp_path):
    assert sv.read_frame_metadata(tmp_path / "none.json") == []
    (tmp_path / "g.json").write_text("not json")
    assert sv.read_frame_metadata(tmp_path / "g.json") == []


def _args(**kw):
    import argparse
    base = dict(seconds=30.0, board_framesize="VGA", board_quality=70, board_fps=10.0,
                board_subsampling="auto", timing=False,
                n6_framesize=None, n6_quality=None, n6_fps=None, n6_subsampling=None,
                ae3_framesize=None, ae3_quality=None, ae3_fps=None, ae3_subsampling=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_board_settings_default_is_fridays_shared_setting():
    # No per-board flag: both boards get the shared --board-* values, no subsampling key
    # (the firmware's own choice), no timing: the wire request is unchanged from Friday.
    for name in ("openmv_n6", "openmv_ae3"):
        assert sv.board_settings(_args(), name) == {
            "framesize": "VGA", "jpeg_quality": 70, "fps": 10.0, "max_seconds": 90,
            "warmup_ms": 2000}


def test_board_settings_per_board_overrides():
    # pool 2 (2026-10-17): best per camera, so the N6 and AE3 can differ
    a = _args(n6_framesize="HD", ae3_subsampling="420", timing=True)
    n6, ae3 = sv.board_settings(a, "openmv_n6"), sv.board_settings(a, "openmv_ae3")
    assert (n6["framesize"], n6.get("subsampling"), n6["timing"]) == ("HD", None, True)
    assert (ae3["framesize"], ae3["subsampling"], ae3["jpeg_quality"]) == ("VGA", "420", 70)
