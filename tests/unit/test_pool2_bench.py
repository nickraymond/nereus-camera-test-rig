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
        {**base, "camera": "openmv_n6", "fps": 10.0, "frames": 300, "fps_asked": 10,
         "frames_lost": 0, "error": None, "first_frame_delay_ms": 9.0, **(n6 or {})},
        {**base, "camera": "openmv_ae3", "fps": 9.33, "frames": 280, "fps_asked": 10,
         "frames_lost": 0, "error": None, "first_frame_delay_ms": 16.0, **(ae3 or {})},
    ]


def test_fridays_n6_clip_holds_when_the_n6_is_under_test():
    # The AE3 at its 9.33 fps control is the other board: flagged, not a bar.
    res = pb.clip_misses(_rows(), "openmv_n6")
    assert res["misses"] == [] and res["cross_effect"] == ["openmv_ae3_fps"] and not res["void"]


def test_ae3_under_test_uses_9p5_or_its_relative_floor():
    assert pb.clip_misses(_rows(), "openmv_ae3")["misses"] == ["openmv_ae3_fps"]
    assert pb.clip_misses(_rows(), "openmv_ae3", ae3_floor=9.13)["misses"] == []


def test_ae3_lost_errored_or_wedged_stream_misses():
    lost = pb.clip_misses(_rows(ae3={"frames_lost": 3}), "openmv_ae3", ae3_floor=9.1)["misses"]
    assert lost == ["openmv_ae3_lost"]
    unknown = pb.clip_misses(_rows(ae3={"frames_lost": None, "error": "timeout"}), "openmv_ae3",
                             ae3_floor=9.1)
    assert unknown["misses"] == ["openmv_ae3_lost"]
    wedge = pb.clip_misses(_rows(ae3={"error": "arm: bad_arm", "fps": None,
                                      "frames_lost": None}), "openmv_ae3")["misses"]
    assert wedge == ["openmv_ae3_fps", "openmv_ae3_lost", "openmv_ae3_wedge"]
    # rev 8: a wedge fails the setting on either board, even on an unusable N6 clip
    other = pb.clip_misses(_rows(ae3={"error": "arm: bad_arm", "fps": None}), "openmv_n6")
    assert other["misses"] == ["openmv_ae3_wedge"]
    n6_dead = pb.clip_misses(_rows(n6={"error": "arm: bad_arm", "frames": 0, "fps": None}),
                             "openmv_n6")
    assert not n6_dead["n6_usable"] and n6_dead["misses"] == ["openmv_n6_wedge"]


def test_n6_frame_loss_is_judged_by_usable_share_rev7():
    # d57 / spec rev 7: an N6 clip with >= 95 % of its expected frames is usable, lost or not
    ok = pb.clip_misses(_rows(n6={"frames": 290, "frames_lost": 10, "fps": 9.67}), "openmv_n6")
    assert ok["misses"] == [] and ok["n6_usable"] and ok["n6_lost"] == 10
    short = pb.clip_misses(_rows(n6={"frames": 120, "frames_lost": None, "fps": 9.9,
                                     "error": "timeout"}), "openmv_n6")
    assert short["misses"] == [] and not short["n6_usable"]
    slow = pb.clip_misses(_rows(n6={"fps": 9.3, "frames": 290}), "openmv_n6")
    assert slow["misses"] == ["openmv_n6_fps"]  # a usable clip still needs >= 9.5 fps
    # QE R3: a 20 %-loss clip at ~8 fps (and a late start) counts only in the usable share
    lossy = pb.clip_misses(_rows(skew=31.0, n6={"frames": 240, "fps": 8.0, "frames_lost": 60}),
                           "openmv_n6")
    assert lossy["misses"] == [] and not lossy["n6_usable"]
    assert lossy["ignored"] == ["openmv_n6_fps", "start_skew"]
    # expected frames = asked fps x seconds: 284 of 300 is under 95 %
    assert not pb.clip_misses(_rows(n6={"frames": 284}), "openmv_n6")["n6_usable"]
    assert pb.clip_misses(_rows(n6={"frames": 285}), "openmv_n6")["n6_usable"]


def test_setting_holds_on_two_of_three_usable_n6_clips():
    good = {"void": False, "misses": [], "n6_usable": True}
    short = {"void": False, "misses": [], "n6_usable": False}
    assert pb.setting_holds([good, short, good], "openmv_n6")
    assert not pb.setting_holds([good, short, short], "openmv_n6")
    assert pb.n6_hopeless([good, short, short]) and not pb.n6_hopeless([short, good])
    assert not pb.setting_holds([good, {**good, "misses": ["start_skew"]}, good], "openmv_n6")
    assert not pb.setting_holds([good, good], "openmv_n6")              # too few clips
    assert pb.setting_holds([{"void": True, "misses": ["x"]}, good, good, good], "openmv_ae3")


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


def test_board_hash_readback_parses_mpremote_output():
    spec = importlib.util.spec_from_file_location("board_hashes_t",
                                                  ROOT / "scripts" / "board_hashes.py")
    bh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bh)
    # verbatim mpremote 1.29 output on nereus002's N6, Sat 10/17 3:28 PM
    out = ("sha256sum :boot.py\n27816e97b8720596a02b255c6ca919a094e0a4007471f7b4c7c4f3daf8489c77\n"
           "sha256sum :main.py\n7c225472316d9fb2ddb7b59f536891a30fdef2bd9322d8d3c1bffab2e64b071b\n")
    assert bh.parse_sums(out) == {
        "boot.py": "27816e97b8720596a02b255c6ca919a094e0a4007471f7b4c7c4f3daf8489c77",
        "main.py": "7c225472316d9fb2ddb7b59f536891a30fdef2bd9322d8d3c1bffab2e64b071b"}
