"""Unit tests for the CLI skeleton — Spec §9, §15."""

from __future__ import annotations

from nereus_camera_test_rig import cli


def test_info_command(capsys):
    rc = cli.main(["info"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "nereus-camera-test-rig" in out


def test_no_command_prints_help(capsys):
    rc = cli.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "usage" in out.lower()


def test_experiment_command_runs_and_reports(capsys, tmp_path, monkeypatch):
    # Fake imx708 driver (no hardware, no camera binary) writing a blank JPEG.
    import pytest

    pytest.importorskip("cv2")
    import cv2
    import numpy as np

    from nereus_camera_test_rig.cameras import registry
    from nereus_camera_test_rig.cameras.base import CameraDevice
    from nereus_camera_test_rig.models import CameraIdentity, CaptureResult
    from nereus_camera_test_rig.storage.checksums import sha256_bytes

    ok, buf = cv2.imencode(".jpg", np.full((480, 640, 3), 127, np.uint8))
    blob = buf.tobytes()

    class FakeCam(CameraDevice):
        driver = "imx708"

        def __init__(self, settings=None, **kw):
            pass

        def get_device_info(self):
            return {}

        def configure(self, settings):
            pass

        def capture_image(self, destination, request):
            from pathlib import Path

            Path(destination).write_bytes(blob)
            return CaptureResult(
                camera=CameraIdentity(driver="imx708", platform="raspberry_pi"),
                request=request, status="completed", output_path=str(destination),
                size_bytes=len(blob), sha256=sha256_bytes(blob), image_format="jpeg",
            )

        def capture_video(self, destination, request):
            return CaptureResult(
                camera=CameraIdentity(driver="imx708", platform="raspberry_pi"),
                request=request, status="failed", error={"code": "x", "message": "y"},
            )

        def health_check(self):
            return {"healthy": True}

    registry.clear()
    registry.register("imx708", FakeCam)

    cfg = tmp_path / "rig.yaml"
    cfg.write_text(
        "rig:\n  id: t\n  results_directory: %r\n"
        "cameras:\n  imx708:\n    enabled: true\n    driver: imx708\n" % str(tmp_path / "results")
    )
    try:
        argv = ["--config", str(cfg), "experiment", "--type", "smoke", "--cameras", "imx708"]
        rc = cli.main(argv)
    finally:
        registry.clear()

    out = capsys.readouterr().out
    assert rc == 0  # captured OK; no card found is not a run failure
    assert "status: completed" in out
    assert "smoke" in out


def test_capture_without_camera_binary_fails_gracefully(monkeypatch, capsys):
    # No rpicam/libcamera on the host -> capture must exit non-zero with a clear
    # message, never a traceback.
    from nereus_camera_test_rig.cameras import imx708

    monkeypatch.setattr(imx708.shutil, "which", lambda c: None)
    rc = cli.main(["capture", "--camera", "imx708", "--out", "results/_clitest"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "failed" in err.lower()
