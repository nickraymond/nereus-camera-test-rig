"""The reference-card analysis run in a child process (analysis/isolated.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("cv2")

from nereus_camera_test_rig.analysis.isolated import analyze_isolated  # noqa: E402
from nereus_camera_test_rig.analysis.result_writer import (  # noqa: E402
    AnalysisConfig,
    analyze_reference_card,
)

FIXTURE = (Path(__file__).resolve().parents[1] / "fixtures" / "reference_card"
           / "Nereus_Reef_Reference_Card_V2.png")


def test_child_result_matches_in_process(tmp_path):
    cfg = AnalysisConfig()
    here = analyze_reference_card(FIXTURE, tmp_path / "a", cfg)
    child = analyze_isolated(FIXTURE, tmp_path / "b", cfg)
    assert child.status == here.status == "pass"
    assert child.tags_detected == here.tags_detected
    assert (child.crop_width, child.crop_height) == (here.crop_width, here.crop_height)
    assert (tmp_path / "b" / "card_crop.jpg").is_file()


def test_a_dead_child_is_a_recorded_failure(tmp_path):
    cfg = AnalysisConfig()
    cfg.scales = (2, "bad")  # the child raises TypeError on the scaled pass
    res = analyze_isolated(FIXTURE, tmp_path / "c", cfg)
    assert res.status == "fail" and res.errors[0].startswith("analysis process exit")
    assert (tmp_path / "c" / "detection.json").is_file()
