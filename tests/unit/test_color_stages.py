"""Stage framework (SPEC §4 Phase 8 S0, §20): reader registry, ``inspect`` outputs, and the
``stage.json`` provenance chain that makes stale inputs fail loudly."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("tifffile")

from _color_helpers import FLAT, dng_tags, flat_mosaic, write_dng  # noqa: E402

from nereus_camera_test_rig.color import stages  # noqa: E402
from nereus_camera_test_rig.color.stages import (  # noqa: E402
    StaleInputError,
    inspect,
    open_raw,
    verify_fresh,
    write_stage,
)


def test_unknown_extension_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match=r"no RAW reader for '\.cr3'.*\.dng"):
        open_raw(tmp_path / "x.cr3")


def test_registered_reader_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "READERS", dict(stages.READERS))
    seen = []
    stages.register_reader(".FAKE", lambda p: seen.append(p) or "frame")
    assert open_raw(tmp_path / "a.fake") == "frame" and seen == [tmp_path / "a.fake"]


def test_inspect_writes_summary_preview_and_provenance(tmp_path):
    dng = write_dng(tmp_path / "shot.dng", flat_mosaic(), dng_tags())
    summary = inspect(dng, tmp_path / "out")
    out = tmp_path / "out" / "inspect" / "shot"
    assert summary["cfa_active"] == "GRBG" and summary["white_level"] == 4095
    assert summary["linear_mean_rgb"] == pytest.approx(list(FLAT), abs=1e-3)
    assert summary["clip_pct_rgb"] == [0.0, 0.0, 0.0]
    assert (out / "preview.png").stat().st_size > 0
    assert json.loads((out / "summary.json").read_text())["reader"] == "dng/tifffile"
    record = verify_fresh(out)
    assert record["stage"] == "inspect" and len(record["params"]["sha256"]) == 64
    assert "sha" in record["git"]


def test_fresh_chain_passes_and_config_change_makes_it_stale(tmp_path):
    cfg = tmp_path / "card.yaml"
    cfg.write_text("a: 1\n")
    up, down = tmp_path / "locate", tmp_path / "qc"
    write_stage(up, "locate", configs=[cfg])
    write_stage(down, "qc", upstream=[up])
    verify_fresh(down)

    cfg.write_text("a: 2\n")
    with pytest.raises(StaleInputError, match="config .*card.yaml changed"):
        verify_fresh(down)


def test_rerunning_upstream_makes_downstream_stale(tmp_path):
    up, down = tmp_path / "locate", tmp_path / "qc"
    write_stage(up, "locate", params={"run": 1})
    write_stage(down, "qc", upstream=[up])
    write_stage(up, "locate", params={"run": 2})  # upstream re-run after qc
    verify_fresh(up)
    with pytest.raises(StaleInputError, match="upstream .* was re-run"):
        verify_fresh(down)


def test_missing_upstream_is_stale(tmp_path):
    with pytest.raises(StaleInputError, match="no stage.json"):
        verify_fresh(tmp_path / "never_ran")


def test_git_state_outside_a_checkout(tmp_path):
    assert stages.git_state(tmp_path) == {"sha": "unknown", "dirty": None}


def test_cli_inspect_exit_codes(tmp_path, capsys):
    from host_tools import color as cli

    dng = write_dng(tmp_path / "shot.dng", flat_mosaic(), dng_tags())
    assert cli.main(["inspect", str(dng), "--out", str(tmp_path / "out")]) == 0
    assert json.loads(capsys.readouterr().out)["cfa_active"] == "GRBG"
    assert cli.main(["inspect", str(tmp_path / "x.cr3"), "--out", str(tmp_path / "out")]) == 1
    assert "no RAW reader" in capsys.readouterr().err
