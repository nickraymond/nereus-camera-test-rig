"""gate_cycle.py PASS/FAIL logic (PREREG §4) and board_probe's ladder summary. No hardware."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_recorder_match_is_by_the_program_a_process_runs():
    assert gate.is_recorder("/usr/bin/rpicam-vid -t 0 --codec h264")
    assert gate.is_recorder("python3 /home/pi/nereus-rig-poolcam/scripts/sync_video.py -t 3")
    assert gate.is_recorder("/home/pi/.venv/bin/nereus-rig experiment --raw")
    assert gate.is_recorder("/bin/bash /home/pi/bin/ci-record.sh")
    # the repo folder name contains "nereus-rig" but is not the CLI; the gate itself is not one
    assert not gate.is_recorder("python3 /home/pi/nereus-rig-poolcam/scripts/gate_cycle.py boot")
    assert not gate.is_recorder("sshd: pi@pts/0")
    # QE 005 nit: only the program counts, not any argv token
    assert not gate.is_recorder("vim scripts/sync_video.py")
    assert not gate.is_recorder("less /home/pi/results/hil_soak.py")
    assert gate.is_recorder("/home/pi/.venv/bin/python3 -u scripts/hil_soak.py run")
    assert gate.is_recorder("timeout 60 rpicam-still -o x.jpg")
    assert gate.is_recorder("env PYTHONPATH=src python3 -m nereus_camera_test_rig.cli experiment")
    assert gate.is_recorder("/home/pi/.venv/bin/python3 /home/pi/.venv/bin/nereus-rig experiment")
    for name in ("capture_dark.py", "pool_selfcheck.py", "selfcheck_bench.py",
                 "selfcheck_inject.py", "pool2_bench.py", "capture_raw_openmv.py"):
        assert gate.is_recorder(f"python3 scripts/{name} --x"), name
    assert gate.program_name("python3 -c 'import x'") == "-c"
    # QE 006 nit: launcher options that take a value
    assert gate.is_recorder("python3 -X dev scripts/sync_video.py --seconds 30")
    assert gate.is_recorder("timeout -s INT 60 rpicam-vid -t 0")
    assert gate.is_recorder("sudo -u pi /home/pi/.venv/bin/nereus-rig experiment")
    assert gate.is_recorder("bash -c rpicam-vid -t 0 -o x.h264")
    assert gate.is_recorder("nice -n 10 python3 scripts/hil_soak.py run")


def test_r7_a_failed_ps_is_not_checked_never_clean(monkeypatch):
    monkeypatch.setattr(gate, "sh", lambda cmd, timeout=20: "")   # OSError / timeout -> ""
    assert gate.recorder_procs() is None
    rec = _vrec()
    rec["procs_after"] = gate.recorder_procs()
    assert "procs:not_checked" in gate.evaluate(rec, {})["fail_reasons"]
    monkeypatch.setattr(gate, "sh", lambda cmd, timeout=20: "  1 /sbin/init\n")
    assert gate.recorder_procs() == []


def test_r8_each_cycle_attempt_records_into_its_own_folder(tmp_path, monkeypatch):
    first = gate.video_dir(tmp_path, 7, "3f1c9a20aaaa")
    assert first.name == "cycle007_3f1c9a20"
    first.mkdir(parents=True)   # a hard reset mid-cycle left this attempt's clips
    assert gate.video_dir(tmp_path, 7, "3f1c9a20aaaa").name == "cycle007_3f1c9a20_2"
    assert gate.video_dir(tmp_path, 7, "77aa0000ffff").name == "cycle007_77aa0000"
    seen = []
    monkeypatch.setattr(gate.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or
                        type("R", (), {"stdout": "", "stderr": ""})())
    v = gate.run_video(tmp_path, 7, 30.0, gate.Log(tmp_path / "gate.log"), [], "3f1c9a20aaaa")
    out = seen[-1][seen[-1].index("--out") + 1]
    assert out.endswith("video/cycle007_3f1c9a20_2") and v["dir"] == "video/cycle007_3f1c9a20_2"
    assert not v["ok"]   # the fake sync_video wrote no video_sync.json


def test_r6_low_sd_stops_the_power_cycling(tmp_path, monkeypatch):
    assert gate.sd_stop({"gb_fails": ["sd_post_free:1999999999"]})
    assert gate.sd_stop({"gb_fails": ["sd_boot:no_reading"]})
    assert not gate.sd_stop({"gb_fails": ["sd_cycle_growth:110000001"]})
    monkeypatch.setattr(gate, "uptime_s", lambda: 123.4)
    ups = []
    monkeypatch.setattr(gate.subprocess, "run", lambda cmd, **kw: ups.append(cmd))
    state = {"run_id": "g", "mode": "auto", "target": 60, "cycles_done": 4, "streak": 4}
    rec = {"cycle": 4, "result": "PASS", "video_only": True,
           "gb_fails": ["sd_post_free:1999999999"]}
    gate.next_step(state, rec, tmp_path, gate.Log(tmp_path / "gate.log"))
    assert ups == [] and "SD stop" in (tmp_path / "gate.log").read_text()
    assert (tmp_path / "STOP").exists()     # QE 006 nit: persisted for the next boot


def test_no_clip_is_added_below_2gb_at_boot(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "uptime_s", lambda: 123.4)
    monkeypatch.setattr(gate, "run_video", lambda *a, **k: pytest.fail("no clip below 2 GB"))
    log = gate.Log(tmp_path / "gate.log")
    for free in (1_999_999_999, None):
        v = gate.cycle_clip({"sd_boot": {"free_b": free}}, tmp_path, 3, {}, log)
        assert not v["ok"] and v["error"].startswith("skipped")
    rec = _vrec(video={"ok": False, "error": "skipped: SD free 1 B at boot"})
    assert "video:skipped: SD free 1 B at boot" in gate.evaluate(rec, {})["fail_reasons"]
    seen = []
    monkeypatch.setattr(gate, "run_video", lambda *a, **k: seen.append(a) or {"ok": True})
    assert gate.cycle_clip({"sd_boot": {"free_b": 3_000_000_000}, "boot_id": "ab"}, tmp_path,
                           3, {"video_s": 30, "video_args": ["--timing"]}, log)["ok"]
    assert seen[0][1:3] == (3, 30.0) and seen[0][4:] == (["--timing"], "ab")


def test_r5_sd_after_the_clear_reads_the_start_kept_in_home(tmp_path, monkeypatch):
    monkeypatch.setattr(gate.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(gate, "GATE_ROOT", tmp_path / "gate")
    monkeypatch.setattr(gate, "STATE", tmp_path / ".gate_armed.json")
    start = {"free_b": 30_000_000_000, "used_b": 5_000_000_000}
    (tmp_path / ".gate_sd_start_gate009_x.json").write_text(json.dumps({"sd_start": start}))
    now = {"free_b": 29_800_000_000}
    monkeypatch.setattr(gate, "sd_reading", lambda run_dir: dict(now))
    a = SimpleNamespace(run="gate009_x", wed_mb=2000.0)
    assert gate.cmd_sd(a) == 0                  # run folder gone; start from ~
    now["free_b"] = 29_700_000_000              # 300 MB below the start
    assert gate.cmd_sd(a) == 1
    now["free_b"] = 29_800_000_000
    assert gate.cmd_sd(SimpleNamespace(run="gate009_x", wed_mb=None)) == 1   # no Wed MB
    assert gate.cmd_sd(SimpleNamespace(run="gate009_x", wed_mb=15_000.0)) == 1  # < 2x Wed
    assert gate.cmd_sd(SimpleNamespace(run="nope", wed_mb=1.0)) == 1
    (tmp_path / "gate" / "gate009_x").mkdir(parents=True)      # QE 006 nit: not cleared yet
    assert gate.cmd_sd(a) == 1


def test_r5_sd_clean_bars():
    start = {"free_b": 30_000_000_000}
    v = gate.sd_clean(start, {"free_b": 29_750_000_000}, 1000)
    assert v["ok"] and v["free_drop_b"] == 250_000_000
    assert gate.sd_clean(start, {"free_b": 1_900_000_000}, 100)["checks"] == {
        "free_within_250MB_of_start": False, "free_ge_2GB": False, "free_ge_2x_wed": True}
    assert not gate.sd_clean(start, {}, 100)["ok"]


def test_seal_writes_the_gb_verdict_before_the_sums(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "GATE_ROOT", tmp_path)
    monkeypatch.setattr(gate, "STATE", tmp_path / ".gate_armed.json")
    run = tmp_path / "gate009_x"
    run.mkdir()
    start = {"free_b": 30_000_000_000, "outside_b": 5_000_000_000}
    (run / "run_manifest.json").write_text(json.dumps({"video_only": True, "sd_start": start}))
    (run / "cycles.jsonl").write_text(json.dumps(
        {"cycle": 1, "video_only": True, "gb_ok": True, "sd_run_growth_b": 1_000_000}) + "\n")
    with (run / "cycles.jsonl").open("a") as f:
        f.write('{"cycle": 2, "video_only": tr')   # a hard reset mid-write (QE 006 nit)
    assert gate.cmd_seal(SimpleNamespace(run="gate009_x")) == 0
    gb = json.loads((run / "gb_verdict.json").read_text())
    assert gb["ok"] and gb["cycles"] == 1 and gb["torn_lines"] == 1
    assert "gb_verdict.json" in (run / "SHA256SUMS").read_text()


def test_video_args_parse_with_equals(monkeypatch):
    seen = {}
    monkeypatch.setattr(gate, "cmd_arm", lambda a: seen.update(vars(a)) or 0)
    monkeypatch.setattr(sys, "argv", ["gate_cycle.py", "arm", "--video-only",
                                      "--video-args=--timing --n6-framesize HD"])
    assert gate.main() == 0 and seen["video_args"] == ["--timing", "--n6-framesize", "HD"]


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
    (tmp_path / "2026-10-20" / "exp_1").mkdir(parents=True)
    (tmp_path / "2026-10-20" / "exp_1" / "SHA256SUMS").write_text("x  a\n")  # nested: covered
    assert gate.write_run_sums(tmp_path) == 4
    lines = (tmp_path / "SHA256SUMS").read_text().splitlines()
    assert sorted(ln.split("  ")[1] for ln in lines) == [
        "2026-10-20/exp_1/SHA256SUMS", "cycles.csv", "gate.log",
        "video/cycle001/openmv_n6.mjpeg"]
    assert gate.write_run_sums(tmp_path) == 4          # the top SHA256SUMS never lists itself


def test_ram_flag_is_relative_to_the_boots_idle_cma_rev7():
    idle = {"cma_free_mb": 42, "mem_avail_mb": 250}
    assert not gate.ram_flag({"cma_free_mb": 23, "mem_avail_mb": 190}, idle)   # 19 MB below
    assert gate.ram_flag({"cma_free_mb": 21, "mem_avail_mb": 190}, idle)       # 21 MB below
    assert gate.ram_flag({"cma_free_mb": 40, "mem_avail_mb": 149}, idle)       # avail < 150


def test_arm_refuses_bad_video_args_before_arming(monkeypatch):
    monkeypatch.setattr(gate, "cmd_arm", lambda a: pytest.fail("must not arm"))
    monkeypatch.setattr(sys, "argv", ["gate_cycle.py", "arm", "--video-only",
                                      "--video-args=--n6-framesize hd"])
    with pytest.raises(SystemExit) as e:
        gate.main()
    assert e.value.code == 2
