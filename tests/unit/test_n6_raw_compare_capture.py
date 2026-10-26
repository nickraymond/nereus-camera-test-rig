"""Per-frame validity checks of scripts/n6_raw_compare_capture.py (TEST_CARD_RAW_COMPARE)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import n6_raw_compare_capture as cap  # noqa: E402


def test_valid_frame_has_no_problems():
    assert cap.check({"exposure_us": 4010, "frame_time_us": None}, 4000) == []


def test_frame_time_path_is_flagged():
    problems = cap.check({"exposure_us": 4000, "frame_time_us": 9000}, 4000)
    assert any("frame_time_us" in p for p in problems)


def test_exposure_outside_2_percent_is_flagged():
    assert cap.check({"exposure_us": 3900, "frame_time_us": None}, 4000)
    assert cap.check({"exposure_us": None, "frame_time_us": None}, 4000)
