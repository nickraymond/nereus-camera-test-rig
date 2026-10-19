"""edge_ml/pool2/wscore.py: the rev 12 W1 verdict, set before any data."""

from __future__ import annotations

import pytest
from edge_ml.pool2 import wscore as w


def _frames(bins, px80=60.0, hit=True, per_bin=8, hold=False, px_scale=True, mtf=0.5):
    """A swim-in: per_bin frames inside each bin centre, closest bin last in time."""
    out, t = [], 0.0
    for c in sorted(bins, reverse=True):
        for k in range(per_bin):
            r = c - 0.2 + 0.4 * k / max(per_bin - 1, 1)
            if hold:
                r = c
            px = px80 * (0.5 / c) if px_scale else px80   # px ~ 1 / range, at the bin centre
            out.append({"t": t, "range_m": r, "tags": 4, "board_whole": True, "mtf50_lpmm": mtf,
                        "samples": {n: {"px": px * (50 / 80 if n.startswith("u50") else 1),
                                        "hit": hit, "human": True}
                                    for n in ("u80a", "u80b", "u50a", "u50b")}})
            t += 0.1 if hold else 0.5
    return out


def test_bins_and_speed():
    assert w.BIN_CENTRES == (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0)
    assert w.bin_of(0.74) == 0.5 and w.bin_of(0.75) == 1.0 and w.bin_of(4.3) is None
    fr = [{"t": 0.0, "range_m": 2.0}, {"t": 1.0, "range_m": 1.5}, {"t": 2.0, "range_m": None}]
    assert w.speeds(fr) == [0.5, 0.5, None]
    gap = [{"t": 0.0, "range_m": 2.0}, {"t": 0.5, "range_m": 1.9}, {"t": 40.0, "range_m": 1.0}]
    assert w.speeds(gap)[1] == pytest.approx(0.2) and w.speeds(gap)[2] is None   # no clip-gap speed


def test_range_is_the_last_bin_of_an_unbroken_chain():
    # 80 mm sample at 60 px at 0.5 m -> 30 px at 1.0 m, 20 px at 1.5 m (px ~ 1 / range)
    res = w.detectable_range(_frames(w.BIN_CENTRES, px80=60.0))
    assert res["range_m"] == 1.0 and res["end_bin"] == 1.5 and res["end_reason"] == "px"
    big = w.detectable_range(_frames(w.BIN_CENTRES, px80=240.0))   # 30 px at 4.0 m
    assert big["range_m"] == 4.0 and big["end_reason"] is None


def test_a_missing_bin_breaks_the_chain_and_f16_marks_tag_limited():
    # tags decode out to 2.0 m but the 1.5 m bin has no scorable frames: a chain break
    gap = w.detectable_range(_frames((0.5, 1.0, 2.0), px80=240.0))
    assert gap["range_m"] == 1.0 and gap["end_reason"] == "frames" and not gap["tag_limited"]
    # the chain held through the farthest bin with tag-decoded frames: the tags set the range
    tags = w.detectable_range(_frames((0.5, 1.0), px80=240.0))
    assert tags["range_m"] == 1.0 and tags["end_reason"] == "tag-limited" and tags["tag_limited"]


def test_hits_need_4_of_5_and_px_every_frame():
    fr = _frames((0.5, 1.0), px80=240.0)
    sel = w.select_bin(fr, 0.5)
    assert len(sel) == 5 and w.bin_sees(sel)["ok"]
    for f in sel[:2]:
        f["samples"]["u80b"]["hit"] = False
    assert w.bin_sees(sel)["why"] == ["u80b:hits 3/5"]
    sel[0]["samples"]["u80b"]["hit"] = True
    assert w.bin_sees(sel)["ok"]                               # 4 of 5
    sel[2]["samples"]["u80a"]["px"] = 29.9                     # one frame under 30 px
    assert w.bin_sees(sel)["why"] == ["u80a:px"]


def test_hold_frames_first_f17():
    fr = _frames((1.0,), hold=False) + _frames((1.0,), hold=True, per_bin=6)
    for f in fr[8:]:
        f["t"] += 100
    sel = w.select_bin(fr, 1.0)
    assert all(f["t"] >= 100 for f in sel)           # 6 hold frames -> only hold frames


def test_50mm_report_fallback_and_n6_vs_ae3():
    fr = _frames(w.BIN_CENTRES, px80=240.0)
    assert w.detectable_range(fr, "50")["range_m"] == 2.5   # 150 px x 0.5 / r >= 30 to 2.5 m
    miss = _frames(w.BIN_CENTRES, px80=240.0, hit=False)
    weak = {c: w.detectable_range(miss) for c in ("imx708", "openmv_n6", "openmv_ae3")}
    assert w.detector_fallback(weak)
    assert w.detectable_range(miss, key="human")["range_m"] == 4.0
    assert not w.detector_fallback({**weak, "imx708": w.detectable_range(fr)})
    a = w.detectable_range(_frames(w.BIN_CENTRES, px80=120.0, mtf=0.6))
    b = w.detectable_range(_frames(w.BIN_CENTRES, px80=120.0, mtf=0.4))
    assert w.n6_vs_ae3(a, b) == "openmv_n6" and w.n6_vs_ae3(b, a) == "openmv_ae3"
    assert w.n6_vs_ae3(w.detectable_range(_frames(w.BIN_CENTRES, px80=240.0)), a) == "openmv_n6"


def test_w2_f10_f12():
    fr = [{"t": 2.0, "range_m": 3.1, "tags": 1}, {"t": 1.0, "range_m": None, "tags": 0},
          {"t": 3.0, "range_m": 2.9, "tags": 4}]
    assert w.w2_secchi(fr) == 3.1
    assert w.f12_water_changed(3.0, 2.3) and not w.f12_water_changed(3.0, 2.5)
    assert w.f10_flags({"imx708": 2.0, "openmv_n6": 2.25}) and not w.f10_flags({"a": 2.0, "b": 2.1})
    assert w.f10_pass_unmeasured([True, False, False])
    assert not w.f10_pass_unmeasured([True] + [False] * 3)


def test_rev13_chain_starts_at_the_first_bin_and_05_is_report_only():
    fr = _frames(w.BIN_CENTRES, px80=240.0)
    for f in fr:                                     # the 0.5 m bin fails (board cut, misses)
        if w.bin_of(f["range_m"]) == 0.5:
            f["samples"]["u80a"]["hit"] = False
    res = w.detectable_range(fr)
    assert res["first_bin"] == 1.0 and res["range_m"] == 4.0
    assert res["bins"][0.5]["report_only"] and not res["bins"][0.5]["ok"]
    late = w.detectable_range(fr, first_bin=1.5)     # the Wed card says the board fits from 1.5 m
    assert late["range_m"] == 4.0 and late["bins"][1.0]["report_only"]
    weak = {c: w.detectable_range(fr, first_bin=1.5) for c in ("openmv_n6", "openmv_ae3")}
    assert not w.detector_fallback(weak)             # judged at each camera's first bin


def test_fallback_leaves_out_cameras_without_five_first_bin_frames():
    miss = _frames(w.BIN_CENTRES, px80=240.0, hit=False)
    empty = w.detectable_range([])                    # no frames: can't judge the detector
    assert w.detector_fallback({"openmv_n6": w.detectable_range(miss), "imx708": empty})
    assert not w.detector_fallback({"imx708": empty, "openmv_ae3": empty})


def test_rev13_frame_pick_rounds_half_up():
    fr = [{"t": float(k), "range_m": 1.0, "board_whole": True} for k in range(7)]
    for f in fr:                                      # all moving (no hold frames): speed 0 ...
        f["range_m"] = 1.0 + 0.2 * f["t"] / 6         # ... 0.2 m over 6 s = 0.033 m/s -> hold
    sel = w.select_bin(fr, 1.0)
    # N = 7: i (N-1)/4 = 0, 1.5, 3, 4.5, 6 -> half up 0, 2, 3, 5, 6 (Python round: 0, 2, 3, 4, 6)
    assert [f["t"] for f in sel] == [0.0, 2.0, 3.0, 5.0, 6.0]


def test_f10_flagged_frames_stay_w1_candidates():
    """QE 008 R2 on #133: F10 acts only through its 25 % rule; a flagged hold frame is still a
    candidate (6 hold frames, 2 flagged -> the 5 come from the holds, not from moving frames)."""
    fr = [{"t": k * 0.1, "range_m": 1.0, "pos_m": [0.0, 0.0, 1.0], "board_whole": True,
           "f10": k in (1, 3)} for k in range(6)]
    fr += [{"t": 5.0 + k * 0.1, "range_m": 1.0, "pos_m": [0.05 * k, 0.0, 1.0],
            "board_whole": True} for k in range(1, 4)]   # moving at 0.5 m/s
    sel = w.select_bin(fr, 1.0)
    assert len(sel) == 5 and all(f["t"] < 1.0 for f in sel)
    assert any(f.get("f10") for f in sel)


def test_n6_vs_ae3_tag_limited_range_is_a_lower_bound():
    """QE 008 N6 reading: a tag-limited range that isn't longer can't lose."""
    a = {"range_m": 2.0, "tag_limited": True, "bins": {}}
    b = {"range_m": 2.5, "tag_limited": False, "bins": {}}
    assert w.n6_vs_ae3(a, b) == "undecided (tag-limited)"
    assert w.n6_vs_ae3(b, a) == "undecided (tag-limited)"
    assert w.n6_vs_ae3({**a, "range_m": 3.0}, b) == "openmv_n6"   # longer and tag-limited: wins
