"""Unit checks for the E7 video screen study code (compression_study/video_cutsheet, RULES.md)."""

from compression_study.video_cutsheet import analyze, encode, run, score
from compression_study.video_cutsheet.common import REEFCHECK, STEP, WIN


def test_reefcheck_pick_is_1st_3rd_5th_in_c_locale_order():
    assert [n.split("-")[-1] for n in REEFCHECK] == ["c1", "c3", "leadline2"]


def test_decimation_frame_counts():
    assert {fps: WIN // k for fps, k in STEP.items()} == {30: 150, 15: 75, 10: 50}
    assert run.nframes("rc_c3", 15) == 75
    assert run.nframes("bmcam003_round1", 10) == 50


def test_openmv_subsampling_rule():
    # OpenMV lib/imlib/jpege.c AUTO: q <= 35 -> 4:2:0, q < 60 -> 4:2:2, else 4:4:4; N6 VC8000 path: 4:2:0 only
    assert [encode._subsample("ae3", q) for q in (1, 35, 36, 59, 60, 100)] == ["2x2", "2x2", "2x1", "2x1", "1x1", "1x1"]
    assert {encode._subsample("n6", q) for q in (10, 50, 100)} == {"2x2"}


def test_knee_bracket():
    assert analyze.knee({100: 80.0, 500: 81.0, 1000: 82.0}) == "≤ 100 KB"
    assert analyze.knee({100: 40.0, 500: 80.0, 1000: 82.0}) == "100–500 KB (bracket)"
    assert analyze.knee({100: 10.0, 500: 50.0, 1000: 70.0}).startswith("500 KB–1 MB")
    # monotone envelope: a dip at 500 KB keeps the knee above it
    assert analyze.knee({100: 81.0, 500: 70.0, 1000: 82.0}).startswith("500 KB–1 MB")
    assert analyze.knee({100: None, 500: 70.0, 1000: 82.0}).startswith("n/a")


def test_ssimulacra2_frames_span_the_clip():
    for n in (50, 75, 150):
        f = score.s2_frames(n)
        assert len(f) == 10 and f[0] == 0 and f[-1] == n - 1 and f == sorted(set(f))
