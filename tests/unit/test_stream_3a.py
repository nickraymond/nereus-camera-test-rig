"""start_stream per-frame 3A + 3A lock (SPEC_n6_eval must do 2 and test 1), board side and host side.

Runs the MicroPython stream loop under CPython with a fake sensor / clock / USB. The default
stream must stay exactly as before (no new header fields, no lock); ``meta`` adds exposure_us /
gain_db to every frame header and ``lock_3a`` reports which controls locked.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import sync_video as sv  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeSensor(types.SimpleNamespace):
    def __init__(self, awb_supported=True):
        super().__init__(QVGA=1, VGA=2, HD=3, RGB565=9)
        self.exposure, self.gain, self.awb_supported = 8000, 6.0, awb_supported
        self.calls = []

    def reset(self): self.calls.append("reset")
    def set_pixformat(self, pf): pass
    def set_framesize(self, fs): pass
    def skip_frames(self, n=None, time=None): self.calls.append(("skip", n, time))
    def get_exposure_us(self):
        self.exposure += 100   # autos still moving unless locked
        return self.exposure
    def get_gain_db(self): return self.gain
    def set_auto_exposure(self, on, exposure_us=None): self.calls.append(("ae", on, exposure_us))
    def set_auto_gain(self, on, gain_db=None): self.calls.append(("ag", on, gain_db))

    def set_auto_whitebal(self, on):
        if not self.awb_supported:
            raise RuntimeError("not supported")
        self.calls.append(("awb", on))

    def snapshot(self):
        img = types.SimpleNamespace()
        img.compress = lambda quality=None, subsampling=None: None
        img.bytearray = lambda: b"\xff\xd8jpeg\xff\xd9"
        img.width, img.height = (lambda: 1280), (lambda: 800)
        return img


class FakeTime:
    def __init__(self): self.t = 0
    def ticks_ms(self):
        self.t += 1
        return self.t
    def ticks_diff(self, a, b): return a - b
    def ticks_add(self, a, b): return a + b
    def sleep_ms(self, ms): self.t += ms


class FakeUsb:
    """Stops the stream (one inbound byte) after ``frames`` frames."""

    def __init__(self, frames): self.frames, self.out = frames, []
    def any(self): return 1 if sum(1 for m in self.msgs() if m["status"] == "frame") >= self.frames else 0
    def read(self, n): return b"x"
    def write(self, b): self.out.append(bytes(b))

    def msgs(self):
        return [json.loads(b) for b in self.out if b.startswith(b"{")]


@pytest.fixture
def run(monkeypatch):
    def _run(settings, awb_supported=True, frames=3):
        sensor = FakeSensor(awb_supported)
        monkeypatch.setitem(sys.modules, "sensor", sensor)
        monkeypatch.setitem(sys.modules, "command_protocol",
                            _load("command_protocol", "openmv/common/command_protocol.py"))
        svc = _load("capture_service_3a", "openmv/common/capture_service.py")
        svc.time = FakeTime()
        n6 = _load("n6_board_config_3a", "openmv/n6/board_config.py")
        usb = FakeUsb(frames)
        svc.stream_frames(usb, "c1", n6, {"framesize": "HD", **settings})
        msgs = usb.msgs()
        return [m["frame"] for m in msgs if m["status"] == "frame"], msgs[-1], sensor
    return _run


def test_default_stream_is_unchanged(run):
    frames, done, sensor = run({})
    assert len(frames) == 3
    assert set(frames[0]) == {"seq", "size_bytes", "sharpness", "width", "height"}
    assert "locked_3a" not in done["output"]
    assert not any(c[0] in ("ae", "ag", "awb") for c in sensor.calls if isinstance(c, tuple))


def test_meta_puts_exposure_and_gain_in_every_header(run):
    frames, _, _ = run({"meta": True})
    assert all(isinstance(f["exposure_us"], int) and f["gain_db"] == 6.0 for f in frames)
    assert "snap_ms" not in frames[0]  # meta alone does not switch timing on


def test_meta_and_timing_together(run):
    frames, _, _ = run({"meta": True, "timing": True})
    assert {"snap_ms", "jpeg_ms", "prev_write_ms", "exposure_us", "gain_db"} <= set(frames[0])


def test_lock_3a_locks_at_the_settled_values_and_reports_it(run):
    _, done, sensor = run({"lock_3a": True})
    lk = done["output"]["locked_3a"]
    assert (lk["exposure"], lk["gain"], lk["awb"]) == (True, True, True)
    assert ("ae", False, lk["exposure_us"]) in sensor.calls
    assert ("ag", False, 6.0) in sensor.calls
    assert ("skip", 3, None) in sensor.calls   # stale frames flushed after the lock


def test_lock_3a_reports_unsupported_awb_instead_of_failing(run):
    frames, done, _ = run({"lock_3a": True}, awb_supported=False)
    lk = done["output"]["locked_3a"]
    assert (lk["exposure"], lk["gain"], lk["awb"]) == (True, True, False)
    assert "not supported" in lk["awb_error"]
    assert len(frames) == 3   # the stream still runs


def _args(**kw):
    import argparse
    base = dict(seconds=5.0, board_framesize="HD", board_quality=70, board_fps=10.0,
                board_subsampling="auto", timing=False, meta=False, lock_3a=False,
                n6_framesize=None, n6_quality=None, n6_fps=None, n6_subsampling=None,
                ae3_framesize=None, ae3_quality=None, ae3_fps=None, ae3_subsampling=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_host_flags_reach_the_board_settings_only_when_set():
    assert "meta" not in sv.board_settings(_args(), "openmv_n6")
    s = sv.board_settings(_args(meta=True, lock_3a=True), "openmv_n6")
    assert (s["meta"], s["lock_3a"]) == (True, True)


def test_cli_parses_the_new_flags():
    a = sv.build_parser().parse_args(["--meta", "--lock-3a", "--n6-serial", "020023000450433547373200"])
    assert (a.meta, a.lock_3a, a.n6_serial) == (True, True, "020023000450433547373200")
    assert sv.META_KEYS == ("exposure_us", "gain_db")
