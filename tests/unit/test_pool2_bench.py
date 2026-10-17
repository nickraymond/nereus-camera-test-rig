"""pool2_bench.py: the test card's bars per clip (docs/pool2_20261021/TEST_CARD_B.md §5)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
_SPEC = importlib.util.spec_from_file_location("pool2_bench_t", ROOT / "scripts" / "pool2_bench.py")
pb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pb)


def _rows(skew=17.1, imx=None, n6=None, ae3=None, throttled="0x0"):
    base = {"start_skew_ms": skew, "host_throttled": throttled}
    return [
        {**base, "camera": "imx708", "fps": 26.4, "max_gap_ms": 66.7,
         "first_frame_delay_ms": 0.8, **(imx or {})},
        {**base, "camera": "openmv_n6", "fps": 10.0, "frames_lost": 0, "error": None,
         "first_frame_delay_ms": 9.0, **(n6 or {})},
        {**base, "camera": "openmv_ae3", "fps": 9.33, "frames_lost": 0, "error": None,
         "first_frame_delay_ms": 16.0, **(ae3 or {})},
    ]


def test_fridays_n6_clip_holds_when_the_n6_is_under_test():
    # The AE3 at its 9.33 fps control is the other board: flagged, not a bar.
    res = pb.clip_misses(_rows(), "openmv_n6")
    assert res["misses"] == [] and res["cross_effect"] == ["openmv_ae3_fps"] and not res["void"]


def test_ae3_under_test_uses_9p5_or_its_relative_floor():
    assert pb.clip_misses(_rows(), "openmv_ae3")["misses"] == ["openmv_ae3_fps"]
    assert pb.clip_misses(_rows(), "openmv_ae3", ae3_floor=9.13)["misses"] == []


def test_lost_errored_or_wedged_stream_misses():
    lost = pb.clip_misses(_rows(n6={"frames_lost": 3}), "openmv_n6")["misses"]
    assert lost == ["openmv_n6_lost"]
    unknown = pb.clip_misses(_rows(n6={"frames_lost": None, "error": "timeout"}), "openmv_n6")
    assert unknown["misses"] == ["openmv_n6_lost"]
    wedge = pb.clip_misses(_rows(ae3={"error": "arm: bad_arm", "fps": None,
                                      "frames_lost": None}), "openmv_ae3")["misses"]
    assert wedge == ["openmv_ae3_fps", "openmv_ae3_lost", "openmv_ae3_wedge"]


def test_imx_bars_and_skew_only_marking():
    assert pb.clip_misses(_rows(imx={"fps": 25.9}), "openmv_n6")["misses"] == ["imx708_fps"]
    assert pb.clip_misses(_rows(imx={"max_gap_ms": 133.3}), "openmv_n6")["misses"] == [
        "imx708_max_gap"]
    late = pb.clip_misses(_rows(skew=28.0, imx={"first_frame_delay_ms": 33.3}), "openmv_n6")
    assert late["misses"] == ["start_skew"] and late["skew_only"]
    boards = pb.clip_misses(_rows(skew=28.0, imx={"first_frame_delay_ms": 12.0},
                                  ae3={"first_frame_delay_ms": 37.0}), "openmv_n6")
    assert boards["misses"] == ["start_skew"] and not boards["skew_only"]


def test_throttled_clip_is_void():
    assert pb.clip_misses(_rows(throttled="0x80000"), "openmv_n6")["void"]
