"""gate_cycle.py PASS/FAIL logic (PREREG §4) and board_probe's ladder summary. No hardware."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load("gate_cycle_t", "scripts/gate_cycle.py")
probe = _load("board_probe_t", "scripts/board_probe.py")


def _contact(ok=True, ticks_ms=30_000, recovered=None):
    first = {"ok": ok, "stage": "replied" if ok else "write_timeout", "reply_s": 0.3,
             "info": {"ticks_ms": ticks_ms, "boot_ms": {"main": 900, "imported": 1500,
                                                      "run": 1510}} if ok else {}}
    rec = {"first": first, "steps": [], "ok": ok}
    if recovered:
        rec["steps"] = [{"step": recovered, "retry": {"ok": True, "stage": "replied",
                                                       "info": {"ticks_ms": ticks_ms}}}]
        rec.update(ok=True, recovered_by=recovered)
    return rec


def _still(ok=True, why=None):
    return {"ok": ok, "bytes": 600_000, "seconds": 3.0, "mtime": 1000.0, **({"why": why} if why
                                                                           else {})}


def _rec(**kw):
    rec = {"cycle": 2, "started_by": "boot", "uptime_start_s": 25.0, "uptime_end_s": 120.0,
           "uptime_set_end_s": 95.0,
           "prev": {"clean": True, "by": "gate_timer"},
           "contact": {"openmv_ae3": _contact(), "openmv_n6": _contact()},
           "verify": {c: {"still": _still(), "raw": {"ok": True}} for c in gate.CAMERAS}}
    rec.update(kw)
    return rec


def test_all_good_is_a_pass_and_extends_the_streak():
    out = gate.evaluate(_rec(), {"streak": 4})
    assert out["result"] == "PASS" and out["streak"] == 5 and out["fail_reasons"] == []
    assert out["boards_power_cycled"] is True


def test_any_still_failure_fails_and_resets_the_streak():
    rec = _rec()
    rec["verify"]["openmv_ae3"]["still"] = _still(False, "timeout")
    out = gate.evaluate(rec, {"streak": 24})
    assert out["result"] == "FAIL" and out["streak"] == 0
    assert out["fail_reasons"] == ["openmv_ae3_still:timeout"]


def test_unclean_previous_shutdown_fails_but_not_on_the_first_cycle():
    assert "prev_shutdown_not_clean" in gate.evaluate(
        _rec(prev={"clean": False, "by": "unknown"}), {})["fail_reasons"]
    assert gate.evaluate(_rec(cycle=1, prev={"clean": False}), {})["result"] == "PASS"


def test_board_that_kept_power_fails_a_boot_cycle():
    rec = _rec(contact={"openmv_ae3": _contact(ticks_ms=9_000_000), "openmv_n6": _contact()})
    out = gate.evaluate(rec, {})
    assert out["boards_power_cycled"] is False and "boards_not_power_cycled" in out["fail_reasons"]


def test_ladder_recovery_in_boot_counts_as_pass_and_is_visible_in_the_csv():
    rec = _rec(contact={"openmv_ae3": _contact(ok=False, recovered="L3_usb_reset"),
                        "openmv_n6": _contact()})
    out = gate.evaluate(rec, {"streak": 1})
    assert out["result"] == "PASS"
    row = gate.csv_row(out)
    assert row["ae3_contact"] == "write_timeout->L3_usb_reset"
    assert set(row) == set(gate.CSV_FIELDS)


@pytest.mark.parametrize("rec,text", [
    ({"first": {"ok": True, "reply_s": 0.29}}, "ok 0.29s"),
    ({"first": {"ok": False, "stage": "write_timeout"}, "ok": True,
      "recovered_by": "L3_usb_reset",
      "steps": [{"step": "L1_dtr", "retry": {"stage": "write_timeout"}},
                {"step": "L3_usb_reset", "retry": {"stage": "replied"}}]},
     "write_timeout -> L1:write_timeout,L3:replied RECOVERED by L3_usb_reset"),
])
def test_probe_summary(rec, text):
    assert probe.summary(rec) == text


def test_video_clip_must_be_ok_synchronized_and_lossless():
    ok = {"ok": True, "skew_ms": 18.5, "fps": {"imx708": 27.4}, "lost": 0}
    assert gate.evaluate(_rec(video=ok), {})["result"] == "PASS"
    assert gate.evaluate(_rec(video={**ok, "skew_ms": 140.0}), {})["fail_reasons"] == [
        "video_skew:140.0"]
    assert gate.evaluate(_rec(video={**ok, "lost": 3}), {})["fail_reasons"] == ["video_lost:3"]
    assert gate.evaluate(_rec(video={"ok": False, "error": "timeout"}), {})["fail_reasons"] == [
        "video:timeout"]
    assert gate.csv_row(gate.evaluate(_rec(video=ok), {}))["video"] == "ok"


def test_slow_start_fails_on_the_monotonic_set_end():
    assert gate.evaluate(_rec(uptime_set_end_s=181.0), {})["fail_reasons"] == ["slow:181s"]


def _dark_still(mean=0.0, size_ok=False):
    return {"ok": size_ok, "why": None if size_ok else "size_ok", "sha_ok": True,
            "decode_ok": True, "size_ok": size_ok, "bytes": 28794, "mean": mean,
            "mtime": 1000.0}


def test_dark_scene_is_void_and_keeps_the_streak():
    rec = _rec()
    rec["verify"]["imx708"]["still"] = {**_still(), "mean": 1.5}
    rec["verify"]["openmv_ae3"]["still"] = _dark_still()
    out = gate.evaluate(rec, {"streak": 7})
    assert out["result"] == "VOID" and out["streak"] == 7


def test_black_board_frame_in_a_lit_scene_is_a_fail():
    rec = _rec()
    rec["verify"]["imx708"]["still"] = {**_still(), "mean": 120.0}
    rec["verify"]["openmv_ae3"]["still"] = _dark_still()
    out = gate.evaluate(rec, {"streak": 7})
    assert out["result"] == "FAIL" and out["streak"] == 0


def test_dark_cycle_with_any_other_failure_is_a_fail():
    rec = _rec(video={"ok": False, "error": "timeout"})
    rec["verify"]["imx708"]["still"] = {**_still(), "mean": 1.5}
    rec["verify"]["openmv_ae3"]["still"] = _dark_still()
    assert gate.evaluate(rec, {"streak": 3})["result"] == "FAIL"
    rec2 = _rec()
    rec2["verify"]["imx708"]["still"] = {**_still(), "mean": 1.5}
    rec2["verify"]["openmv_ae3"]["still"] = {**_dark_still(), "sha_ok": False}
    assert gate.evaluate(rec2, {"streak": 3})["result"] == "FAIL"


def test_passing_run_arms_the_repeat_run_and_power_cycles(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(gate, "GATE_ROOT", tmp_path / "gate")
    monkeypatch.setattr(gate, "DIVE_FLAG", tmp_path / "dive_flag")
    monkeypatch.setattr(gate, "POOL_ARMED", tmp_path / "pool_armed")
    cron: list = []
    monkeypatch.setattr(gate, "crontab_lines", lambda: list(cron))
    monkeypatch.setattr(gate, "set_crontab", lambda lines: cron.__setitem__(slice(None), lines))
    monkeypatch.setattr(gate.pc, "write_run_config", lambda d, a: d / "rig.yaml")
    monkeypatch.setattr(gate.pc, "git_sha", lambda: "test")
    monkeypatch.setattr(gate, "uptime_s", lambda: 123.4)  # no /proc on the Mac
    ups = []
    monkeypatch.setattr(gate.subprocess, "run", lambda cmd, **kw: ups.append(cmd) or
                        type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    args = type("A", (), dict(target=60, mode="auto", wake_min=1, stop_after_fails=3,
                              label="gate25a", sync=True, video_s=10, until_streak=25, repeat=2,
                              dark_wake_min=10, lens_position="0.8",
                              config="configs/rig.example.yaml"))()
    assert gate.cmd_arm(args) == 0
    state = gate.load_state()
    first = state["run_id"]
    state.update(streak=25, cycles_done=31)
    gate.save_state(state)
    gate.next_step(state, {"cycle": 31, "result": "PASS"}, Path(state["run_dir"]),
                   gate.Log(Path(state["run_dir"]) / "gate.log"))
    new = gate.load_state()
    assert new["run_id"] != first and new["previous_run"] == first
    assert new["repeat"] == 1 and new["streak"] == 0 and new["label"] == "gate25a-repeat"
    assert sum(gate.CRON_MARK in ln for ln in cron) == 1
    assert ups and "--wake-min" in ups[-1]


# -- pool 2 ready gate (docs/pool2_20261021/SPEC.md rev 5+, d52, QE 005 N1-N7) ----------------
def _sync(**over):
    cams = {
        "imx708": {"ok": True, "bytes": 37_000_000, "frames": 791, "fps": 26.4},
        "openmv_n6": {"ok": True, "bytes": 8_400_000, "frames": 300, "fps": 10.0,
                      "frames_lost": 0, "settings": {"fps": 10}},
        "openmv_ae3": {"ok": True, "bytes": 7_200_000, "frames": 281, "fps": 9.34,
                       "frames_lost": 0, "settings": {"fps": 10}},
    }
    for cam, r in over.items():
        cams[cam] = r
    return {"cameras": cams, "args": {"imx_fps": 30, "board_fps": 10},
            "first_frame_delay_ms": {"imx708": 33.3, "openmv_n6": 9.0, "openmv_ae3": 17.0}}


def _vrec(sync=None, procs=(), **kw):
    rec = _rec(video_only=True, verify={}, procs_after=list(procs),
               video={"cams": gate.video_cams(sync or _sync(), 30.0)})
    rec.update(kw)
    return rec


def test_video_only_cycle_passes_without_stills():
    out = gate.evaluate(_vrec(), {"streak": 7})
    assert out["result"] == "PASS" and out["streak"] == 8 and out["fail_reasons"] == []
    cams = out["video"]["cams"]
    assert cams["openmv_ae3"]["of_nominal"] == 0.937 and cams["openmv_ae3"]["complete"]
    assert cams["imx708"]["first_frame_ms"] == 33.3


def test_lost_frames_are_scored_but_never_fail_the_cycle():
    # d52 (Nick 10/17): video completeness is scored separately; a broken N6 stream that still
    # recorded frames is INCOMPLETE, not a FAIL.
    broken = {"ok": False, "bytes": 2_000_000, "frames": 120, "fps": 10.0, "frames_lost": 47,
              "error": "OpenMvError('timeout', 'openmv_n6: no frame header for 10 s')"}
    out = gate.evaluate(_vrec(_sync(openmv_n6=broken)), {"streak": 3})
    assert out["result"] == "PASS" and out["streak"] == 4
    assert out["video"]["cams"]["openmv_n6"]["complete"] is False
    assert "INCOMPLETE" in gate.csv_row(out)["n6_video"]


def test_a_camera_that_never_started_fails_the_cycle():
    dead = {"camera": "openmv_ae3", "ok": False, "error": "arm: OpenMvError('bad_arm')"}
    out = gate.evaluate(_vrec(_sync(openmv_ae3=dead)), {"streak": 9})
    assert out["result"] == "FAIL" and out["streak"] == 0
    assert any(f.startswith("openmv_ae3_video:arm:") for f in out["fail_reasons"])
    missing = gate.evaluate(_vrec(_sync(imx708={"ok": True, "bytes": 0, "frames": 0})), {})
    assert "imx708_video:no_frames" in missing["fail_reasons"]


def test_recorder_left_running_or_unchecked_fails_the_cycle():
    out = gate.evaluate(_vrec(procs=["812 rpicam-vid -t 34500 ..."]), {"streak": 2})
    assert out["result"] == "FAIL" and "procs_left:1" in out["fail_reasons"]
    rec = _vrec()
    rec["procs_after"] = None
    assert "procs:not_checked" in gate.evaluate(rec, {})["fail_reasons"]


def test_recorder_match_is_by_argv_basename():
    assert gate.is_recorder("/usr/bin/rpicam-vid -t 0 --codec h264")
    assert gate.is_recorder("python3 /home/pi/nereus-rig-poolcam/scripts/sync_video.py -t 3")
    assert gate.is_recorder("/home/pi/.venv/bin/nereus-rig experiment --raw")
    assert gate.is_recorder("/bin/bash /home/pi/bin/ci-record.sh")
    # the repo folder name contains "nereus-rig" but is not the CLI; the gate itself is not one
    assert not gate.is_recorder("python3 /home/pi/nereus-rig-poolcam/scripts/gate_cycle.py boot")
    assert not gate.is_recorder("sshd: pi@pts/0")


def _sd(free, outside):
    return {"free_b": free, "outside_b": outside, "used_b": outside, "run_b": 0}


def test_sd_growth_is_post_clip_to_post_clip_and_cycle_one_from_gate_start():
    start = _sd(30_000_000_000, 5_000_000_000)
    rec = {"sd_boot": _sd(29_990_000_000, 5_010_000_000),
           "sd_post": _sd(29_900_000_000, 5_060_000_000)}
    out = gate.evaluate_sd(rec, {"sd_start": start})            # cycle 1: from the gate start
    assert out["gb_ok"] and out["sd_cycle_growth_b"] == 60_000_000
    rec2 = {"sd_boot": _sd(29_800_000_000, 5_070_000_000),
            "sd_post": _sd(29_700_000_000, 5_170_000_001)}
    out2 = gate.evaluate_sd(rec2, {"sd_start": start, "sd_prev_post": rec["sd_post"]})
    assert out2["sd_cycle_growth_b"] == 110_000_001 and not out2["gb_ok"]
    assert out2["sd_run_growth_b"] == 170_000_001


def test_sd_free_below_2gb_or_a_missing_reading_fails_gb():
    start = _sd(30_000_000_000, 5_000_000_000)
    low = gate.evaluate_sd({"sd_boot": _sd(1_999_999_999, 5_000_000_000),
                            "sd_post": _sd(30_000_000_000, 5_000_000_000)}, {"sd_start": start})
    assert not low["gb_ok"] and low["gb_fails"] == ["sd_boot_free:1999999999"]
    none = gate.evaluate_sd({"sd_post": _sd(30_000_000_000, 5_000_000_000)}, {"sd_start": start})
    assert "sd_boot:no_reading" in none["gb_fails"]


def test_sd_verdict_judges_the_last_reading_against_the_start():
    start = _sd(30_000_000_000, 5_000_000_000)
    rows = [{"cycle": 1, "video_only": True, "gb_ok": True, "sd_run_growth_b": 300_000_000},
            {"cycle": 2, "video_only": True, "gb_ok": True, "sd_run_growth_b": 240_000_000}]
    assert gate.sd_verdict(rows, start)["ok"]          # a transient bump that went away
    rows.append({"cycle": 3, "video_only": True, "gb_ok": False, "gb_fails": ["x"],
                 "sd_run_growth_b": 251_000_000})
    v = gate.sd_verdict(rows, start)
    assert not v["ok"] and "run_growth:251000000" in v["fails"] and v["fails"][0].startswith(
        "cycle 3")


def test_seal_covers_every_file_in_the_run_folder(tmp_path):
    (tmp_path / "video" / "cycle001").mkdir(parents=True)
    (tmp_path / "video" / "cycle001" / "openmv_n6.mjpeg").write_bytes(b"\xff\xd8abc")
    (tmp_path / "cycles.csv").write_text("cycle\n1\n")
    (tmp_path / "gate.log").write_text("x\n")
    assert gate.write_run_sums(tmp_path) == 3
    lines = (tmp_path / "SHA256SUMS").read_text().splitlines()
    assert sorted(ln.split("  ")[1] for ln in lines) == [
        "cycles.csv", "gate.log", "video/cycle001/openmv_n6.mjpeg"]
    assert gate.write_run_sums(tmp_path) == 3          # SHA256SUMS never lists itself


def test_ram_flag_is_relative_to_the_boots_idle_cma_rev7():
    idle = {"cma_free_mb": 42, "mem_avail_mb": 250}
    assert not gate.ram_flag({"cma_free_mb": 23, "mem_avail_mb": 190}, idle)   # 19 MB below
    assert gate.ram_flag({"cma_free_mb": 21, "mem_avail_mb": 190}, idle)       # 21 MB below
    assert gate.ram_flag({"cma_free_mb": 40, "mem_avail_mb": 149}, idle)       # avail < 150
