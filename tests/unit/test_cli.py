"""Unit tests for the CLI skeleton — Spec §9, §15."""

from __future__ import annotations

import pytest

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


@pytest.mark.parametrize("argv", [["capture"], ["experiment", "--profile", "x.yaml"]])
def test_unimplemented_commands_exit_nonzero(argv, capsys):
    rc = cli.main(argv)
    err = capsys.readouterr().err
    assert rc == 2
    assert "not implemented" in err.lower()
