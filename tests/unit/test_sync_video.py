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
