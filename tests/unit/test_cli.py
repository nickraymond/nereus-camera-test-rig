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


def test_experiment_command_unimplemented(capsys):
    rc = cli.main(["experiment", "--profile", "x.yaml"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "not implemented" in err.lower()


def test_capture_without_camera_binary_fails_gracefully(monkeypatch, capsys):
    # No rpicam/libcamera on the host -> capture must exit non-zero with a clear
    # message, never a traceback.
    from nereus_camera_test_rig.cameras import imx708

    monkeypatch.setattr(imx708.shutil, "which", lambda c: None)
    rc = cli.main(["capture", "--camera", "imx708", "--out", "results/_clitest"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "failed" in err.lower()
