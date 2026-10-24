"""sync_video.py helpers: per-frame metadata survives an rpicam-vid that died at teardown."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

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
                board_subsampling="auto", timing=False, meta=False, lock_3a=False,
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


from openmv.common import command_protocol as cp  # noqa: E402

from nereus_camera_test_rig.cameras.openmv_usb import _SerialIO  # noqa: E402


def _jpeg(i, n=300):
    body = bytes((j * 7 + i) % 251 for j in range(n)).replace(b"\xff", b"\xfe")
    return b"\xff\xd8" + body + b"\n" + body[:50] + b"\xff\xd9"


def _frame(i, data):
    return cp.encode_message(cp.frame_response("cid", i, len(data), len(data), 640, 400,
                                               ticks_ms=1000 + 100 * i)) + data


class _Bytes:
    """read(n)/write() transport over a fixed byte string (then silence)."""

    def __init__(self, data):
        self.data = bytearray(data)

    def read(self, n):
        out = bytes(self.data[:n])
        del self.data[:n]
        return out

    def write(self, data):
        return len(data)


def test_decode_tail_finds_a_header_behind_jpeg_bytes():
    hdr = cp.encode_message(cp.frame_response("cid", 7, 10, 10, 640, 400, ticks_ms=5)).rstrip()
    assert sv.decode_tail(b"\x12\x34{garbage\xff" + hdr)["frame"]["seq"] == 7
    assert sv.decode_tail(b"\xff\xd8 no header here") is None


def test_split_short_jpeg():
    j = _jpeg(1)
    assert sv.split_short_jpeg(j) == (j, b"")
    assert sv.split_short_jpeg(j[:100] + j[-2:] + b"NEXT") == (j[:100] + j[-2:], b"NEXT")


def test_reader_recovers_after_bytes_lost_mid_frame(tmp_path):
    # gate008 c22 (2026-10-15): bytes lost inside an N6 frame; the old reader never found a
    # header again. Now one damaged frame is dropped and the stream continues.
    frames = [_jpeg(i) for i in range(6)]
    stream = b"".join(_frame(i, f) for i, f in enumerate(frames))
    cut = stream.index(frames[2]) + 120          # lose 40 bytes inside frame 2
    stream = stream[:cut] + stream[cut + 40:]
    stream += cp.encode_message(cp.completed_response("cid", {"frames": 6}))
    bv = sv.BoardVideo("openmv_n6", "SERIAL", tmp_path, {})
    bv.io = _SerialIO(_Bytes(stream), default_timeout=1)
    bv._read()
    assert [r["seq"] for r in bv.rows] == [0, 1, 3, 4, 5]
    assert bv.rec.get("short_frames") == 1 and "error" not in bv.rec


def test_combined_decode_byte_loss_costs_one_frame_and_keeps_timing_columns(tmp_path):
    # pool 2 (QE 005 R2): the resync fix (aa06bc7) and the B0 per-frame timing (f672126)
    # together. Each frame header carries snap/jpeg/prev-write ms; 40 bytes are lost inside
    # frame 3; the reader drops that one frame, resyncs, and keeps every other frame's timing.
    frames = [_jpeg(i) for i in range(7)]
    stream = b""
    for i, f in enumerate(frames):
        timing = {"snap_ms": 16, "jpeg_ms": 60 + i, "prev_write_ms": None if i == 0 else 23}
        stream += cp.encode_message(cp.frame_response("cid", i, len(f), len(f), 640, 400,
                                                      ticks_ms=1000 + 107 * i,
                                                      extra=timing)) + f
    cut = stream.index(frames[3]) + 150
    stream = stream[:cut] + stream[cut + 40:]
    stream += cp.encode_message(cp.completed_response("cid", {"frames": 7}))
    bv = sv.BoardVideo("openmv_ae3", "SERIAL", tmp_path, {"timing": True})
    bv.io = _SerialIO(_Bytes(stream), default_timeout=1)
    bv._read()
    assert [r["seq"] for r in bv.rows] == [0, 1, 2, 4, 5, 6]
    assert bv.rec.get("short_frames") == 1 and "error" not in bv.rec
    assert [r["jpeg_ms"] for r in bv.rows] == [60, 61, 62, 64, 65, 66]
    assert bv.rows[0]["prev_write_ms"] is None and bv.rows[-1]["snap_ms"] == 16
    import csv
    rows = list(csv.DictReader((tmp_path / "openmv_ae3_frames.csv").open()))
    # timing columns keep their order; the per-frame 3A columns (SPEC_n6_eval) follow, empty here
    assert list(rows[0])[-5:] == ["snap_ms", "jpeg_ms", "prev_write_ms", "exposure_us", "gain_db"]
    assert rows[3]["seq"] == "4" and rows[0]["exposure_us"] == ""
    assert (tmp_path / "openmv_ae3.mjpeg").stat().st_size == sum(len(frames[i])
                                                                 for i in (0, 1, 2, 4, 5, 6))


# -- QE 005 R2: the fps bar is frames over the clip's seconds ------------------------------------
def test_clip_rate_counts_frames_in_the_clip_window():
    fire = 100.0
    full = [fire + 0.016 + 0.107 * k for k in range(281)]          # Friday's AE3 pace, 30 s
    r = sv.clip_rate(full, fire, 30.0)
    assert r["fps_clip"] == 9.37 and r["last_frame_s"] == 29.976
    # QE 005's case: 201 clean frames in 20 s scored 10.0 fps on the first -> last span
    early = [fire + 0.1 * k for k in range(201)]
    assert sv.clip_rate(early, fire, 30.0) == {"fps_clip": 6.7, "last_frame_s": 20.0}
    assert sv.clip_rate([], fire, 30.0) == {"fps_clip": 0.0, "last_frame_s": None}
    assert sv.clip_rate(full, None, 30.0)["fps_clip"] is None


def test_extra_args_are_checked_against_the_boards_own_framesizes():
    # pool 2 dive program (pool_capture --program "video:6x30[...]"): refused at arm, not underwater
    sv.check_extra_args(["--n6-framesize", "960x600", "--ae3-subsampling", "420", "--timing"])
    for bad in (["--ae3-framesize", "960x600"],       # the AE3 has no ISP scaler
                ["--n6-framesize", "hd"], ["--board-framesize", "960x600"],
                ["--ae3-subsampling", "411"], ["--seconds", "5"], ["--out=/tmp/x"],
                ["--sec", "5"], ["--ou", "/tmp/x"], ["--n6-frame", "HD"]):   # no abbreviations
        with pytest.raises(ValueError):
            sv.check_extra_args(bad)
