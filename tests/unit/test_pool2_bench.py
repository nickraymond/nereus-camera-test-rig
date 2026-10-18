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


def _rows(skew=17.1, imx=None, n6=None, ae3=None, throttled="0x0", void=False):
    # Friday clip102: IMX708 first frame 0.8 ms after the fire, N6 9.0, AE3 16.0. The bars read
    # fps_clip (frames in the 30 s / 30, QE 005 R2); ``fps`` is the first -> last frame rate.
    base = {"start_skew_ms": skew, "host_throttled": throttled, "host_throttle_void": void}
    return [
        {**base, "camera": "imx708", "fps": 26.4, "fps_clip": 26.4, "max_gap_ms": 66.7,
         "start_offset_ms": 0.0, "first_frame_delay_ms": 0.8, "ok": True, "rc": 0,
         **(imx or {})},
        {**base, "camera": "openmv_n6", "fps": 10.0, "fps_clip": 10.0, "frames": 300,
         "fps_asked": 10, "frames_lost": 0, "error": None, "start_offset_ms": 8.2,
         "first_frame_delay_ms": 9.0, "framesize_asked": "VGA",
         "resolution_measured": "640x400", "subsampling": "auto", "subsampling_measured": "420",
         "bad_frames": 0, **(n6 or {})},
        {**base, "camera": "openmv_ae3", "fps": 9.33, "fps_clip": 9.33, "frames": 280,
         "fps_asked": 10, "frames_lost": 0, "error": None, "start_offset_ms": 17.1,
         "first_frame_delay_ms": 16.0, "framesize_asked": "VGA",
         "resolution_measured": "640x400", "subsampling": "auto", "subsampling_measured": "444",
         "bad_frames": 0, **(ae3 or {})},
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
    wedge = pb.clip_misses(_rows(ae3={"error": "arm: bad_arm", "fps_clip": None,
                                      "frames_lost": None}), "openmv_ae3")["misses"]
    assert wedge == ["openmv_ae3_fps", "openmv_ae3_lost", "openmv_ae3_wedge"]
    # rev 8: a wedge fails the setting on either board, even on an unusable N6 clip
    other = pb.clip_misses(_rows(ae3={"error": "arm: bad_arm", "fps_clip": None}),
                           "openmv_n6")
    assert other["misses"] == ["openmv_ae3_wedge"]
    n6_dead = pb.clip_misses(_rows(n6={"error": "arm: bad_arm", "frames": 0, "fps_clip": None}),
                             "openmv_n6")
    assert not n6_dead["n6_usable"] and n6_dead["misses"] == ["openmv_n6_wedge"]


def test_n6_frame_loss_is_judged_by_usable_share_rev7():
    # d57 / spec rev 7: an N6 clip with >= 95 % of its expected frames is usable, lost or not
    ok = pb.clip_misses(_rows(n6={"frames": 290, "frames_lost": 10, "fps_clip": 9.67}),
                        "openmv_n6")
    assert ok["misses"] == [] and ok["n6_usable"] and ok["n6_lost"] == 10
    short = pb.clip_misses(_rows(n6={"frames": 120, "frames_lost": None, "fps_clip": 4.0,
                                     "error": "timeout"}), "openmv_n6")
    assert short["misses"] == [] and not short["n6_usable"]
    slow = pb.clip_misses(_rows(n6={"fps_clip": 9.3, "frames": 290}), "openmv_n6")
    assert slow["misses"] == ["openmv_n6_fps"]  # a usable clip still needs >= 9.5 fps
    # QE R3: a 20 %-loss clip at ~8 fps (and a late start) counts only in the usable share
    lossy = pb.clip_misses(_rows(n6={"frames": 240, "fps_clip": 8.0, "frames_lost": 60,
                                     "start_offset_ms": 50.0}), "openmv_n6")
    assert lossy["misses"] == [] and not lossy["n6_usable"]
    assert lossy["ignored"] == ["openmv_n6_fps", "board_skew"]
    # expected frames = asked fps x seconds: 284 of 300 is under 95 %
    assert not pb.clip_misses(_rows(n6={"frames": 284}), "openmv_n6")["n6_usable"]
    assert pb.clip_misses(_rows(n6={"frames": 285}), "openmv_n6")["n6_usable"]


def test_setting_holds_on_two_of_three_usable_n6_clips():
    good = {"void": False, "misses": [], "n6_usable": True}
    short = {"void": False, "misses": [], "n6_usable": False}
    assert pb.setting_holds([good, short, good], "openmv_n6")
    assert not pb.setting_holds([good, short, short], "openmv_n6")
    assert pb.n6_hopeless([good, short, short]) and not pb.n6_hopeless([short, good])
    assert not pb.setting_holds([good, {**good, "misses": ["board_skew"]}, good], "openmv_n6")
    assert not pb.setting_holds([good, good], "openmv_n6")              # too few clips
    assert pb.setting_holds([{"void": True, "misses": ["x"]}, good, good, good], "openmv_ae3")


def test_imx_bars_and_d55_sync_bars():
    assert pb.clip_misses(_rows(imx={"fps_clip": 25.9}), "openmv_n6")["misses"] == ["imx708_fps"]
    assert pb.clip_misses(_rows(imx={"max_gap_ms": 133.3}), "openmv_n6")["misses"] == [
        "imx708_max_gap"]
    # d55 (Nick 10/17): a late IMX708 (33.3 ms, Friday clip098) no longer fails the clip
    late = pb.clip_misses(_rows(skew=24.3, imx={"first_frame_delay_ms": 33.3,
                                                "start_offset_ms": 24.3},
                                n6={"start_offset_ms": 0.0}, ae3={"start_offset_ms": 10.0}),
                          "openmv_n6")
    assert late["misses"] == [] and late["board_skew_ms"] == 10.0
    late_imx = pb.clip_misses(_rows(imx={"first_frame_delay_ms": 34.5}), "openmv_n6")
    assert late_imx["misses"] == [] and late_imx["imx_start_ok"] is False  # rev 9: own line
    assert late_imx["imx_period_ms"] == 37.9 and late_imx["imx_first_frame_ms"] == 34.5
    boards = pb.clip_misses(_rows(ae3={"start_offset_ms": 33.5}), "openmv_n6")
    assert boards["misses"] == ["board_skew"] and boards["board_skew_ms"] == 25.3


def test_throttled_clip_is_void_and_no_reading_is_a_miss():
    assert pb.clip_misses(_rows(void=True), "openmv_n6")["void"]
    # QE 005 nit: a missing host_stats.csv used to count as 0x0
    none = pb.clip_misses(_rows(void=None), "openmv_n6")
    assert not none["void"] and none["misses"] == ["host_no_throttle_reading"]


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


# -- QE 005 FAIL @ a938f76: R1, R2, R4, R7 + nits ------------------------------------------------
import json  # noqa: E402
import types  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.parametrize("argv, under, sync_args", [
    # the card's own lines (TEST_CARD_B.md §4), verbatim
    ("ae3_420 --under openmv_ae3 -- --ae3-subsampling 420 --timing", "openmv_ae3",
     ["--ae3-subsampling", "420", "--timing"]),
    ("ctl_1 --under openmv_ae3 -- --timing", "openmv_ae3", ["--timing"]),
    ("n6_hd --under openmv_n6 -- --n6-framesize HD", "openmv_n6", ["--n6-framesize", "HD"]),
    ("n6_960_probe --under openmv_n6 --probe 960x600 -- --n6-framesize 960x600", "openmv_n6",
     ["--n6-framesize", "960x600"]),
])
def test_r1_the_cards_command_lines_parse(argv, under, sync_args):
    a = pb.parse_args(argv.split())
    assert (a.under, a.sync_args) == (under, sync_args)


def test_r1_options_after_dashdash_belong_to_sync_video_and_usage_is_exit_2():
    a = pb.parse_args("x --under openmv_n6 -- --n6-quality 80 --timing".split())
    assert a.under == "openmv_n6" and a.sync_args == ["--n6-quality", "80", "--timing"]
    with pytest.raises(SystemExit):   # not a sync_video option: refused before any clip
        pb.parse_args("x --under openmv_n6 -- --under openmv_ae3".split())
    assert pb.parse_args("p --under openmv_n6 --probe 960x600".split()).n == 1
    with pytest.raises(SystemExit) as e:
        pb.parse_args("x -- --under openmv_n6".split())   # --under only after --: usage error
    assert e.value.code == pb.EXIT_USAGE == 2 and pb.EXIT_REFUSED == 3


def test_r2_a_camera_that_stops_early_fails_its_fps_bar():
    # QE 005 R2: the AE3 sending 201 clean frames in 20 s had (201-1)/20 = 10.0 fps
    ae3 = {"fps": 10.0, "fps_clip": 6.7, "frames": 201, "frames_lost": 0}
    assert pb.clip_misses(_rows(ae3=ae3), "openmv_ae3")["misses"] == ["openmv_ae3_fps"]
    # an rpicam-vid that died at 20 s: 30 fps over its span, max gap 33 ms, but 17.6 over 30 s
    imx = {"fps": 30.0, "fps_clip": 17.6, "max_gap_ms": 33.3, "ok": True, "rc": 0}
    assert pb.clip_misses(_rows(imx=imx), "openmv_n6")["misses"] == ["imx708_fps"]
    # and the IMX708's own verdict: ok False or a non-zero rc is a miss
    crash = pb.clip_misses(_rows(imx={"rc": -11}), "openmv_n6")["misses"]
    assert crash == ["imx708_not_ok"]
    assert pb.clip_misses(_rows(imx={"ok": False}), "openmv_n6")["misses"] == ["imx708_not_ok"]


def test_probe_result_is_dims_whole_frames_and_the_whole_clip():
    row = {"camera": "openmv_n6", "resolution_measured": "960x600", "bad_frames": 0,
           "last_frame_s": 29.9, "error": None}
    dec = {"frames": 300, "failed": 0}
    assert pb.probe_result([row], "openmv_n6", "960x600", decode=dec)["probe_ok"]
    assert pb.probe_result([{**row, "resolution_measured": "1280x800"}], "openmv_n6",
                           "960x600", decode=dec)["probe_why"] == ["dims:1280x800"]
    assert not pb.probe_result([{**row, "last_frame_s": 21.0}], "openmv_n6", "960x600",
                               decode=dec)["probe_ok"]
    assert not pb.probe_result([{**row, "bad_frames": 2}], "openmv_n6", "960x600",
                               decode=dec)["probe_ok"]
    assert not pb.probe_result([row], "openmv_n6", "960x600")["probe_ok"]   # never decoded


class _Bench:
    """main() with the Pi parts stubbed: prechecks, the clip run and the clip scoring."""

    def __init__(self, monkeypatch, tmp_path, scores, refuse=None):
        self.scores, self.refuse, self.ran = list(scores), refuse or [], []
        monkeypatch.setattr(pb, "OUT_ROOT", tmp_path)
        monkeypatch.setattr(pb, "prechecks", self.prechecks)
        monkeypatch.setattr(pb, "score_clip", self.score)
        monkeypatch.setattr(pb.time, "sleep", lambda s: None)
        monkeypatch.setattr(pb.subprocess, "run", self.run)
        monkeypatch.setattr(pb, "build_id", lambda: "testbuild")

    def prechecks(self):
        return self.refuse.pop(0) if self.refuse else []

    def run(self, cmd, **kw):
        out = Path(cmd[cmd.index("--out") + 1])
        out.mkdir(parents=True)
        self.ran.append(out.name)

    def score(self, clip, under, floor, seconds):
        res = self.scores.pop(0)
        return {"clip": clip.name, "void": False, "misses": [], "cross_effect": [],
                "n6_usable": True, "rows": [], **res}


def _setting(tmp_path, label):
    return json.loads((tmp_path / label / "setting.json").read_text())


def test_r4_a_failed_setting_cannot_be_retried_until_it_holds(monkeypatch, tmp_path):
    b = _Bench(monkeypatch, tmp_path, [{"misses": ["openmv_ae3_fps"]}, {}, {}, {}])
    argv = "ae3_420 --under openmv_ae3 -- --ae3-subsampling 420".split()
    assert pb.main(argv) == pb.EXIT_FAILS and b.ran == ["clip01"]
    assert pb.main(argv) == pb.EXIT_FAILS and b.ran == ["clip01"]   # no new clip, still FAILS
    rec = _setting(tmp_path, "ae3_420")
    assert rec["verdict"] == "fails" and [c["clip"] for c in rec["clips"]] == ["clip01"]
    assert len(rec["runs"]) == 2


def test_r4_a_stopped_setting_resumes_and_is_judged_over_every_clip(monkeypatch, tmp_path):
    # clip01 passes, then a pre-check refuses (exit 3); the next run adds clip02 + clip03
    b = _Bench(monkeypatch, tmp_path, [{}, {}, {}], refuse=[[], [], ["SD free 1 B < 2 GB"]])
    argv = "ctl_1 --under openmv_ae3 -- --timing".split()
    assert pb.main(argv) == pb.EXIT_REFUSED and b.ran == ["clip01"]
    assert pb.main(argv) == pb.EXIT_HOLDS and b.ran == ["clip01", "clip02", "clip03"]
    rec = _setting(tmp_path, "ctl_1")
    assert rec["verdict"] == "holds" and len(rec["clips"]) == 3 and "stopped" not in rec
    # other arguments on the same label: refused, never mixed
    assert pb.main("ctl_1 --under openmv_ae3 -- --timing --ae3-fps 9".split()) == \
        pb.EXIT_REFUSED


def test_r4_a_clip_folder_without_a_record_is_scored_from_its_files(monkeypatch, tmp_path):
    # a crash mid-clip leaves clip01/ but no setting.json entry: it is judged, not skipped
    (tmp_path / "n6_hd" / "clip01").mkdir(parents=True)
    b = _Bench(monkeypatch, tmp_path, [{"misses": ["no_video_sync_json"]}])
    assert pb.main("n6_hd --under openmv_n6 -- --n6-framesize HD".split()) == pb.EXIT_FAILS
    assert b.ran == [] and _setting(tmp_path, "n6_hd")["clips"][0]["orphan"] is True


def test_two_throttled_clips_stop_the_bench_with_their_own_exit_code(monkeypatch, tmp_path):
    _Bench(monkeypatch, tmp_path, [{"void": True}, {"void": True}])
    assert pb.main("ctl_1 --under openmv_n6".split()) == pb.EXIT_THROTTLED == 4
    assert "throttled" in _setting(tmp_path, "ctl_1")["stopped"]


def test_n9_a_probe_never_prints_holds(monkeypatch, tmp_path, capsys):
    row = {"camera": "openmv_n6", "resolution_measured": "960x600", "bad_frames": 0,
           "last_frame_s": 29.95, "error": None}
    _Bench(monkeypatch, tmp_path, [{"rows": [row]}])
    monkeypatch.setattr(pb, "decode_check", lambda clip, cam, dims: {"frames": 300, "failed": 0,
                                                                     "first_failure": None})
    argv = "n6_960_probe --under openmv_n6 --probe 960x600 -- --n6-framesize 960x600".split()
    assert pb.main(argv) == 0
    out = capsys.readouterr().out
    assert "PROBE PASS" in out and "HOLDS" not in out
    rec = _setting(tmp_path, "n6_960_probe")
    assert rec["holds"] is None and rec["probe_ok"] and len(rec["clips"]) == 1


def test_a_clip_the_table_cannot_read_is_a_miss_not_a_crash(tmp_path):
    clip = tmp_path / "clip01"
    clip.mkdir()
    (clip / "video_sync.json").write_text('{"cameras": {"imx708": {"camera": "imx708"}}}')
    res = pb.score_clip(clip, "openmv_ae3", None, 30.0)  # error-only IMX708, no boards
    assert "imx708_not_ok" in res["misses"] and "openmv_ae3_fps" in res["misses"]
    # the N6 under test: an unusable clip counts only in the usable share (d57, QE R3)
    assert not pb.score_clip(clip, "openmv_n6", None, 30.0)["n6_usable"]
    (clip / "video_sync.json").write_text("not json")
    assert pb.score_clip(clip, "openmv_n6", None, 30.0)["misses"][0].startswith(
        "clip_table_error")


def test_r7_prechecks_refuse_when_ps_gave_nothing_or_a_gate_is_armed(monkeypatch, tmp_path):
    gate = types.SimpleNamespace(recorder_procs=lambda: None)
    ups = types.SimpleNamespace(ups_power=lambda: {"on_battery": False, "vbat_mv": 4100})
    monkeypatch.setattr(pb, "_load", lambda name: {"gate_cycle": gate, "pool_capture": ups}[name])
    monkeypatch.setattr(pb, "DIVE_FLAG", tmp_path / "flag")
    monkeypatch.setattr(pb, "ARM_FILES", (tmp_path / ".gate_armed.json",))
    monkeypatch.setattr(pb, "ROOT", tmp_path)
    monkeypatch.setattr(pb, "throttled_now", lambda: "0x0")
    (tmp_path / "results").mkdir()
    (tmp_path / "flag").touch()
    assert pb.prechecks() == ["process check failed (ps gave no output)"]
    gate.recorder_procs = lambda: []
    (tmp_path / ".gate_armed.json").write_text("{}")
    assert pb.prechecks() == [f"{tmp_path / '.gate_armed.json'} present (a gate or pool mode "
                              "is armed)"]
    # QE 006 R2: spec rev 9 voids any non-0x0 sample, and sticky bits last until a reboot
    (tmp_path / ".gate_armed.json").unlink()
    monkeypatch.setattr(pb, "throttled_now", lambda: "0x50000")
    assert pb.prechecks() == ["vcgencmd get_throttled = 0x50000 (not 0x0): cold boot the Pi "
                              "first"]


# -- QE 006 FAIL @ 9ec4bfe: R1 + nits ------------------------------------------------------------
def test_qe006_r1_deleting_a_failed_clip_folder_never_erases_its_miss(monkeypatch, tmp_path):
    b = _Bench(monkeypatch, tmp_path, [{"misses": ["no_video_sync_json"]}, {}, {}, {}])
    argv = "ae3_420 --under openmv_ae3 -- --ae3-subsampling 420".split()
    assert pb.main(argv) == pb.EXIT_FAILS
    import shutil as _sh
    _sh.rmtree(tmp_path / "ae3_420" / "clip01")       # "cleaning up" the crashed clip
    assert pb.main(argv) == pb.EXIT_FAILS and b.ran == ["clip01"]
    rec = _setting(tmp_path, "ae3_420")
    assert rec["clips"][0]["folder_missing"] and rec["verdict"] == "fails"


def test_qe006_r1_new_clips_are_numbered_past_every_known_clip(monkeypatch, tmp_path):
    b = _Bench(monkeypatch, tmp_path, [{}, {}, {}], refuse=[[], [], ["UPS"]])
    argv = "ctl_1 --under openmv_ae3 -- --timing".split()
    assert pb.main(argv) == pb.EXIT_REFUSED and b.ran == ["clip01"]
    import shutil as _sh
    _sh.rmtree(tmp_path / "ctl_1" / "clip01")
    assert pb.main(argv) == pb.EXIT_HOLDS and b.ran == ["clip01", "clip02", "clip03"]
    rec = _setting(tmp_path, "ctl_1")
    assert [c["clip"] for c in rec["clips"]] == ["clip01", "clip02", "clip03"]
    assert rec["clips"][0].get("folder_missing") and not rec["clips"][1].get("folder_missing")


def test_a_label_started_on_another_build_is_refused(monkeypatch, tmp_path):
    _Bench(monkeypatch, tmp_path, [{}], refuse=[[], [], ["UPS"]])
    monkeypatch.setattr(pb, "build_id", lambda: "aaaa1111")
    argv = "ctl_1 --under openmv_ae3 -- --timing".split()
    assert pb.main(argv) == pb.EXIT_REFUSED            # one clip, then a pre-check stop
    assert _setting(tmp_path, "ctl_1")["build"] == "aaaa1111"
    monkeypatch.setattr(pb, "build_id", lambda: "bbbb2222")
    assert pb.main(argv) == pb.EXIT_REFUSED            # a new deploy: ctl_2, never a resume


def test_frames_must_be_what_was_asked():
    ok = pb.clip_misses(_rows(), "openmv_ae3", ae3_floor=9.1)
    assert ok["misses"] == [] and ok["format"] == {"openmv_n6": [], "openmv_ae3": []}
    # the AE3 ignored subsampling: ae3_420 would be scored on 4:4:4 frames
    chroma = pb.clip_misses(_rows(ae3={"subsampling": "420"}), "openmv_ae3", ae3_floor=9.1)
    assert chroma["misses"] == ["openmv_ae3_format"]
    assert chroma["format"]["openmv_ae3"] == ["chroma:444!=420"]
    size = pb.clip_misses(_rows(n6={"framesize_asked": "HD"}), "openmv_n6")
    assert size["misses"] == ["openmv_n6_format"] and size["cross_effect"] == ["openmv_ae3_fps"]
    custom = pb.clip_misses(_rows(n6={"framesize_asked": "960x600",
                                      "resolution_measured": "960x600"}), "openmv_n6")
    assert custom["misses"] == []
    bad = pb.clip_misses(_rows(ae3={"bad_frames": 1}), "openmv_ae3", ae3_floor=9.1)
    assert bad["misses"] == ["openmv_ae3_format"]
    # the other board at its control: a wrong format is flagged, not a bar
    other = pb.clip_misses(_rows(ae3={"bad_frames": 2}), "openmv_n6")
    assert other["misses"] == [] and "openmv_ae3_format" in other["cross_effect"]


def test_the_bench_owns_seconds_out_and_cameras():
    for flag in ("--seconds 10", "--out /tmp/x", "--cameras openmv_ae3", "--seconds=10"):
        with pytest.raises(SystemExit) as e:
            pb.parse_args(f"x --under openmv_ae3 -- {flag}".split())
        assert e.value.code == pb.EXIT_USAGE


def test_a_probe_whose_clips_were_all_void_resumes(monkeypatch, tmp_path):
    row = {"camera": "openmv_n6", "resolution_measured": "960x600", "bad_frames": 0,
           "last_frame_s": 29.95, "error": None}
    b = _Bench(monkeypatch, tmp_path, [{"void": True}, {"void": True}, {"rows": [row]}])
    monkeypatch.setattr(pb, "decode_check", lambda clip, cam, dims: {"frames": 300, "failed": 0,
                                                                     "first_failure": None})
    argv = "p960 --under openmv_n6 --probe 960x600 -- --n6-framesize 960x600".split()
    assert pb.main(argv) == pb.EXIT_THROTTLED
    assert pb.main(argv) == 0 and b.ran == ["clip01", "clip02", "clip03"]


def test_probe_decodes_every_frame_with_pillow(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    import io as _io
    frames = []
    for k in range(3):
        buf = _io.BytesIO()
        Image.new("RGB", (64, 40), (k * 50, 30, 90)).save(buf, "JPEG", quality=70)
        frames.append(buf.getvalue())
    (tmp_path / "openmv_n6.mjpeg").write_bytes(b"".join(frames[:2]) + frames[2][:-40])
    (tmp_path / "openmv_n6_frames.csv").write_text(
        "seq,bytes\n" + "".join(f"{k},{len(f) - (40 if k == 2 else 0)}\n"
                               for k, f in enumerate(frames)))
    d = pb.decode_check(tmp_path, "openmv_n6", "64x40")
    assert d["frames"] == 3 and d["failed"] == 1 and "frame 2" in d["first_failure"]
    assert pb.decode_check(tmp_path, "openmv_n6", "960x600")["failed"] == 3
    row = {"camera": "openmv_n6", "resolution_measured": "64x40", "bad_frames": 0,
           "last_frame_s": 29.9, "error": None}
    assert pb.probe_result([row], "openmv_n6", "64x40", decode=d)["probe_why"][0].startswith(
        "decode:1 of 3")
    assert pb.probe_result([row], "openmv_n6", "64x40",
                           decode={"frames": 3, "failed": 0})["probe_ok"]


def test_sync_args_are_checked_before_any_clip():
    with pytest.raises(SystemExit) as e:
        pb.parse_args("n6_hd --under openmv_n6 -- --n6-framesize hd".split())
    assert e.value.code == pb.EXIT_USAGE
    with pytest.raises(SystemExit):
        pb.parse_args("x --under openmv_ae3 -- --ae3-framesize 960x600".split())
