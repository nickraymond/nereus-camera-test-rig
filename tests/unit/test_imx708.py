"""Unit tests for the IMX708 adapter — Spec §9.

The rpicam subprocess is mocked so these run on any host. A fake runner writes a
real (minimal) JPEG + metadata so the adapter's validation path is exercised for
real, not stubbed out.
"""

from __future__ import annotations

import subprocess

import pytest
from _capture_helpers import Completed, FakeRunner

from nereus_camera_test_rig.cameras import imx708
from nereus_camera_test_rig.cameras.imx708 import Imx708Camera, select_command
from nereus_camera_test_rig.models import CaptureRequest


# -- select_command ---------------------------------------------------------
def test_select_command_prefers_rpicam(monkeypatch):
    monkeypatch.setattr(
        imx708.shutil, "which", lambda c: f"/usr/bin/{c}" if c == "rpicam-still" else None
    )
    path, backend = select_command("image")
    assert path == "/usr/bin/rpicam-still" and backend == "rpicam"


def test_select_command_falls_back_to_libcamera(monkeypatch):
    def which(c):
        return "/usr/bin/libcamera-still" if c == "libcamera-still" else None

    monkeypatch.setattr(imx708.shutil, "which", which)
    path, backend = select_command("image")
    assert backend == "libcamera"


def test_select_command_missing_raises(monkeypatch):
    monkeypatch.setattr(imx708.shutil, "which", lambda c: None)
    with pytest.raises(imx708.CaptureError, match="no camera command"):
        select_command("image")


# -- control-flag builder (OQ-10: default auto == no flags) -----------------
def test_control_args_empty_is_auto():
    assert imx708._control_args({}) == []


def test_control_args_manual_controls():
    args = imx708._control_args(
        {
            "focus": {"mode": "manual", "lens_position": 3.2},
            "white_balance": {"red_gain": 2.5, "blue_gain": 1.8},
            "exposure": {"shutter_us": 10000, "analogue_gain": 1.5},
        }
    )
    assert "--autofocus-mode" in args and "--lens-position" in args
    assert "--awbgains" in args and "2.5000,1.8000" in args
    assert "--shutter" in args and "10000" in args
    assert "--gain" in args


# -- capture_image ----------------------------------------------------------
def test_capture_image_success(tmp_path):
    runner = FakeRunner(width=4608, height=2592)
    cam = Imx708Camera(still_command="rpicam-still", runner=runner)
    dest = tmp_path / "out.jpg"
    res = cam.capture_image(str(dest), CaptureRequest(kind="image"))
    assert res.ok
    assert res.width == 4608 and res.height == 2592
    assert res.image_format == "jpeg"
    assert res.size_bytes > 0 and res.sha256
    assert res.sensor_metadata.get("ExposureTime") == 13539  # parsed from --metadata


def test_capture_image_default_command_has_auto_and_no_controls(tmp_path):
    runner = FakeRunner()
    cam = Imx708Camera(still_command="rpicam-still", runner=runner)
    cam.capture_image(str(tmp_path / "o.jpg"), CaptureRequest(kind="image"))
    cmd = runner.calls[-1]
    # Auto path: no exposure/wb/focus control flags present.
    for flag in ("--shutter", "--gain", "--awb", "--autofocus-mode"):
        assert flag not in cmd
    assert "--metadata" in cmd and "-o" in cmd


def test_capture_image_nonzero_returns_failed(tmp_path):
    runner = FakeRunner(returncode=1, stderr=b"Sensor snapshot failed")
    cam = Imx708Camera(still_command="rpicam-still", runner=runner)
    res = cam.capture_image(str(tmp_path / "o.jpg"), CaptureRequest(kind="image"))
    assert not res.ok
    assert res.error["code"] == "capture_failed"


def test_capture_image_timeout_returns_failed(tmp_path):
    runner = FakeRunner(raises=subprocess.TimeoutExpired(cmd="rpicam-still", timeout=1))
    cam = Imx708Camera(still_command="rpicam-still", runner=runner)
    res = cam.capture_image(str(tmp_path / "o.jpg"), CaptureRequest(kind="image"))
    assert not res.ok
    assert "timed out" in res.error["message"]


def test_capture_image_empty_output_is_failure(tmp_path):
    # Runner reports success but writes nothing -> must be caught as failed.
    class SilentRunner(FakeRunner):
        def __call__(self, cmd, **kw):
            self.calls.append(cmd)
            return Completed(0)

    cam = Imx708Camera(still_command="rpicam-still", runner=SilentRunner())
    res = cam.capture_image(str(tmp_path / "o.jpg"), CaptureRequest(kind="image"))
    assert not res.ok
    assert res.error["code"] in {"empty_output", "corrupt_output"}


def test_capture_video_success_defaults_to_mjpeg(tmp_path):
    runner = FakeRunner()
    cam = Imx708Camera(video_command="rpicam-vid", runner=runner)
    dest = tmp_path / "clip.mjpeg"
    res = cam.capture_video(str(dest), CaptureRequest(kind="video"))
    assert res.ok and res.size_bytes > 0 and res.sha256
    cmd = runner.calls[-1]
    # Pi 5 has no H.264 encoder -> default codec is mjpeg at 1080p (OQ-17).
    assert cmd[cmd.index("--codec") + 1] == "mjpeg"
    assert "1920" in cmd and "1080" in cmd


def test_capture_video_nonzero_returns_failed(tmp_path):
    cam = Imx708Camera(video_command="rpicam-vid", runner=FakeRunner(returncode=1))
    res = cam.capture_video(str(tmp_path / "clip.mjpeg"), CaptureRequest(kind="video"))
    assert not res.ok and res.error["code"] == "capture_failed"


def test_health_check(monkeypatch):
    runner = FakeRunner()
    cam = Imx708Camera(still_command="rpicam-still", runner=runner)
    health = cam.health_check()
    assert health["healthy"] is True
    assert health["cameras_detected"] == 1
