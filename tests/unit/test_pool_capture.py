"""Unit tests for the pool capture loop helpers (scripts/pool_capture.py) and the Mac pull's
verification (host_tools/pool_pull.py). No hardware: synthetic set folders only."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pytest
from host_tools import pool_pull

_SPEC = importlib.util.spec_from_file_location(
    "pool_capture", Path(__file__).resolve().parents[2] / "scripts" / "pool_capture.py")
pool_capture = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pool_capture)


def _capture(board_or_sensor: str, path: str, size: int, sha: str, meta: dict) -> dict:
    cam = {"sensor": "imx708"} if board_or_sensor == "imx708" else {"board": board_or_sensor,
                                                                     "firmware": "5.0.1"}
    return {"camera": cam, "status": "completed", "output_path": path, "size_bytes": size,
            "sha256": sha, "duration_seconds": 3.21, "sensor_metadata": meta}


def _make_set(root: Path, fail_ae3_raw: bool = False) -> Path:
    exp = root / "2026-10-14" / "exp_20261014T170000Z_pool"
    files = {"imx708/imx708_image_x.jpg": b"j" * 100, "imx708/imx708_raw_x.dng": b"d" * 200,
             "imx708/imx708_raw_x.jpg": b"k" * 50, "openmv_n6/openmv_n6_image_x.jpg": b"n" * 30,
             "openmv_n6/openmv_n6_raw_x.bayer": b"b" * 64, "openmv_ae3/openmv_ae3_image_x.jpg":
             b"a" * 20, "openmv_ae3/openmv_ae3_raw_x.bayer": b"c" * 64}
    for rel, data in files.items():
        p = exp / "captures" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    sha = {rel: pool_pull._sha256(exp / "captures" / rel) for rel in files}
    pi = "/home/pi/x/captures/"
    rec = {
        "captures": [
            _capture("imx708", pi + "imx708/imx708_image_x.jpg", 100,
                     sha["imx708/imx708_image_x.jpg"], {"ExposureTime": 1000}),
            _capture("n6", pi + "openmv_n6/openmv_n6_image_x.jpg", 30,
                     sha["openmv_n6/openmv_n6_image_x.jpg"], {"exposure_us": 8000}),
            _capture("ae3", pi + "openmv_ae3/openmv_ae3_image_x.jpg", 20,
                     sha["openmv_ae3/openmv_ae3_image_x.jpg"], {"exposure_us": 9000})],
        "raw_captures": [
            _capture("imx708", pi + "imx708/imx708_raw_x.dng", 200,
                     sha["imx708/imx708_raw_x.dng"],
                     {"LensPosition": 0.8, "ColourGains": [1.5, 2.0], "jpeg_path":
                      pi + "imx708/imx708_raw_x.jpg", "junk": 1}),
            _capture("n6", pi + "openmv_n6/openmv_n6_raw_x.bayer", 64,
                     sha["openmv_n6/openmv_n6_raw_x.bayer"], {"cfa": "BGGR", "gain_db": 6.0}),
            _capture("ae3", pi + "openmv_ae3/openmv_ae3_raw_x.bayer", 64,
                     sha["openmv_ae3/openmv_ae3_raw_x.bayer"], {"cfa": "BGGR"})],
    }
    if fail_ae3_raw:
        rec["raw_captures"][2] = {"camera": {"board": "ae3"}, "status": "failed",
                                  "error": {"code": "timeout", "message": "x"}}
    (exp / "experiment.json").write_text(json.dumps(rec))
    return exp


def test_summarize_set_records_files_and_applied_settings(tmp_path):
    exp = _make_set(tmp_path)
    s = pool_capture.summarize_set(exp)
    assert set(s) == {"imx708", "openmv_n6", "openmv_ae3"}
    raw = s["imx708"]["raw"]
    assert raw["file"] == "imx708_raw_x.dng" and raw["jpeg"] == "imx708_raw_x.jpg"
    assert raw["applied"] == {"LensPosition": 0.8, "ColourGains": [1.5, 2.0]}  # junk dropped
    assert s["openmv_n6"]["raw"]["firmware"] == "5.0.1"


def test_sha256sums_cover_every_file_and_pull_verifies(tmp_path):
    exp = _make_set(tmp_path)
    total = pool_capture.write_sha256sums(exp)
    lines = (exp / "SHA256SUMS").read_text().splitlines()
    assert len(lines) == 8 and total > 0  # 7 captures + experiment.json
    assert any(line.endswith("  captures/imx708/imx708_raw_x.jpg") for line in lines)
    ok = pool_pull.verify_set(exp)
    assert ok["verified"] == 8 and not ok["bad"] and not ok["missing"]
    (exp / "captures/openmv_n6/openmv_n6_raw_x.bayer").write_bytes(b"corrupt")
    (exp / "captures/imx708/imx708_raw_x.jpg").unlink()
    bad = pool_pull.verify_set(exp)
    assert "captures/openmv_n6/openmv_n6_raw_x.bayer" in bad["bad"]
    assert "captures/imx708/imx708_raw_x.jpg" in bad["missing"]


def test_set_without_sha256sums_is_unverified_but_record_checked(tmp_path):
    exp = _make_set(tmp_path)
    r = pool_pull.verify_set(exp)
    assert r["unverified"] and not r["bad"] and not r["missing"]


def _line(cams: dict) -> dict:
    return {"set": 7, "run_s": 600.0, "clock": {"uptime_s": 900.0}, "set_seconds": 41.2,
            "cameras": cams, "cma_free_mb": 90,
            "pi_after": {"cpu_temp_c": 51.0, "throttled": "0x0", "mem_available_mb": 210,
                         "disk_free_gb": 55.1},
            "power": {"vin_mv": 4950, "vbat_mv": 3380, "w_out": 3.9, "on_battery": False}}


def test_health_text_ok_and_partial(tmp_path):
    good = pool_capture.summarize_set(_make_set(tmp_path / "a"))
    text = pool_capture.health_text(_line(good))
    assert text.startswith("[set 007 | run+ 10m | boot+15m] OK 3/3")
    assert "4.95Vin" in text and "cma 90MB" in text
    part = pool_capture.summarize_set(_make_set(tmp_path / "b", fail_ae3_raw=True))
    text = pool_capture.health_text(_line(part))
    assert "PARTIAL 2/3" in text and "ae3 FAIL(still ok/raw timeout)" in text


def test_storage_check_plans_sets_and_respects_max_sets(tmp_path):
    args = argparse.Namespace(hours=1.0, plan_set_s=40.0, interval_s=0.0, reserve_gb=2.0,
                              max_sets=0)
    s = pool_capture.storage_check(tmp_path, args)
    assert s["planned_sets"] == 91
    assert s["need_gb"] == round(91 * pool_capture.SET_MB_PLAN / 1024 + 2.0, 2)
    args.max_sets = 10
    assert pool_capture.storage_check(tmp_path, args)["planned_sets"] == 10
    args.reserve_gb = 1e9
    assert pool_capture.storage_check(tmp_path, args)["ok"] is False


def test_backoff_benches_a_failing_board_but_never_the_imx708():
    fails = {"imx708": 9, "openmv_n6": 0, "openmv_ae3": 2}
    cams, benched = pool_capture.cameras_for_set(7, fails, retry_every=5)
    assert cams == ["imx708", "openmv_n6"] and benched == ["openmv_ae3"]
    cams, benched = pool_capture.cameras_for_set(10, fails, retry_every=5)  # retry set
    assert cams == ["imx708", "openmv_n6", "openmv_ae3"] and benched == []
    fails["openmv_ae3"] = 1
    assert pool_capture.cameras_for_set(7, fails, 5)[1] == []


def test_health_text_marks_benched(tmp_path):
    line = _line(pool_capture.summarize_set(_make_set(tmp_path)))
    line["benched"] = ["openmv_ae3"]
    del line["cameras"]["openmv_ae3"]
    assert "ae3 benched" in pool_capture.health_text(line)


def test_power_cycle_only_for_a_persistently_failing_board_after_warmup():
    fails = {"imx708": 7, "openmv_n6": 1, "openmv_ae3": 3}
    assert pool_capture.should_power_cycle(fails, 3, uptime_s=900) == ["openmv_ae3"]
    assert pool_capture.should_power_cycle(fails, 3, uptime_s=300) == []  # boot-loop guard
    assert pool_capture.should_power_cycle(fails, 0, uptime_s=900) == []  # disabled
    assert pool_capture.should_power_cycle({"imx708": 50}, 3, uptime_s=900) == []  # never IMX


def test_light_pull_defers_dngs_and_render_picks_them(tmp_path):
    run = tmp_path / "pool009_x"
    exp = _make_set(run)
    pool_capture.write_sha256sums(exp)
    (exp / "captures/imx708/imx708_raw_x.dng").unlink()  # as after --no-dng
    r = pool_pull.verify_set(exp, deferred=(".dng",))
    assert r["deferred"] == 1 and not r["missing"] and not r["bad"]
    assert pool_pull.verify_set(exp)["missing"] == ["captures/imx708/imx708_raw_x.dng",
                                                    "imx708_raw_x.dng"]
    render = tmp_path / "pool009_x_render"
    render.mkdir()
    (render / "render_manifest.json").write_text(json.dumps({"selected": {
        "imx708": [f"pool009_x/{exp.name}/imx708_raw_x.jpg"], "openmv_n6": []}}))
    assert pool_pull.dng_list_for_render(render, tmp_path) == {
        "pool009_x": [f"2026-10-14/{exp.name}/captures/imx708/imx708_raw_x.dng"]}


def test_pull_exit_codes_ok_unverified_bad(tmp_path, monkeypatch):
    run = tmp_path / "pool010_x"
    exp = _make_set(run)
    pool_capture.write_sha256sums(exp)
    cut = _make_set(run / "late")  # a set cut short at power-off: no SHA256SUMS
    cut.rename(run / "2026-10-14" / "exp_20261014T170100Z_pool")
    args = ["--verify-only", "--dest", str(tmp_path), "--run", "pool010_x"]
    assert pool_pull.main(args) == 3  # UNVERIFIED, not OK
    (run / "2026-10-14" / "exp_20261014T170100Z_pool" / "experiment.json").unlink()
    import shutil
    shutil.rmtree(run / "2026-10-14" / "exp_20261014T170100Z_pool")
    assert pool_pull.main(args) == 0  # every set verified
    (exp / "captures/openmv_ae3/openmv_ae3_raw_x.bayer").write_bytes(b"x")
    assert pool_pull.main(args) == 1  # BAD
    report = json.loads((tmp_path / "pull_report.json").read_text())
    assert report["runs"][0]["bad"]


def test_dng_pick_ignores_openmv_only_selections(tmp_path):
    run = tmp_path / "pool011_x"
    exp = _make_set(run)
    pool_capture.write_sha256sums(exp)
    render = tmp_path / "r"
    render.mkdir()
    (render / "render_manifest.json").write_text(json.dumps({"selected": {
        "imx708": [], "openmv_n6": [f"pool011_x/{exp.name}/openmv_n6_raw_x.bayer"]}}))
    assert pool_pull.dng_list_for_render(render, tmp_path) == {}


def test_run_ids_never_repeat_even_if_the_counter_is_lost(tmp_path, monkeypatch):
    monkeypatch.setattr(pool_capture, "POOL_ROOT", tmp_path)
    a = pool_capture.next_run_id()
    (tmp_path / a).mkdir()
    (tmp_path / ".run_counter").write_text("")  # a hard cut emptied it
    b = pool_capture.next_run_id()
    assert a.startswith("pool001_") and b.startswith("pool002_")
    (tmp_path / b).mkdir()
    (tmp_path / ".run_counter").write_text("\x00\x00garbage")
    assert pool_capture.next_run_id().startswith("pool003_")
    assert not list(tmp_path.glob("*.tmp"))


def test_durable_write_replaces_atomically(tmp_path):
    f = tmp_path / "run_manifest.json"
    pool_capture.atomic_json(f, {"sets": 1})
    pool_capture.atomic_json(f, {"sets": 2})
    assert json.loads(f.read_text()) == {"sets": 2} and not list(tmp_path.glob("*.tmp"))


def test_summary_survives_a_torn_last_line(tmp_path, capsys):
    run = tmp_path / "pool012_x"
    run.mkdir()
    good = {"set": 1, "set_seconds": 44.0, "cameras": {}, "pi_after": {
        "cpu_temp_c": 47.0, "throttled": "0x0", "mem_available_mb": 230, "disk_free_gb": 55.0}}
    (run / "sets.jsonl").write_text(json.dumps(good) + "\n" + '{"set": 2, "set_secon')
    (run / "run_manifest.json").write_text(json.dumps({"run_id": "pool012_x"}))
    lines, torn = pool_capture.read_sets(run)
    assert [ln["set"] for ln in lines] == [1] and len(torn) == 1
    assert pool_capture.summary(argparse.Namespace(run_dir=str(run))) == 0
    out = capsys.readouterr().out
    assert "1 sets" in out and "1 torn line(s) skipped" in out and "switched off" in out


def test_dive_program_phases_are_cumulative_and_video_counts_its_clips():
    prog = pool_capture.parse_program("cal_a:12,cal_b:10,stills:10,video:6x30")
    assert [(p["name"], p["start_min"], p["end_min"]) for p in prog] == [
        ("cal_a", 0, 12), ("cal_b", 12, 22), ("stills", 22, 32), ("video", 32, 38)]
    assert prog[3]["period_s"] == 60.0  # 30 s clip + ~30 s arm/finish -> one per minute
    assert pool_capture.phase_at(prog, 12.5)["name"] == "cal_b"
    tail = pool_capture.phase_at(prog, 40)
    assert tail["name"] == "stills" and tail["tail"]


def test_dive_program_rejects_unknown_phase():
    with pytest.raises(ValueError):
        pool_capture.parse_program("cal_a:5,warp:3")


# -- pool 2 (Wed 10/21): per-phase labels + sync_video args (PE 003 conditions 2-4) -------------
WED = "video@ctl:1x30,video@best:6x30[--n6-framesize HD --ae3-subsampling 420]"


def test_program_phase_args_and_labels():
    ctl, best = pool_capture.parse_program(WED)
    assert (ctl["label"], ctl["start_min"], ctl["end_min"], "args" in ctl) == ("ctl", 0, 1, False)
    assert best["label"] == "best" and best["args"] == ["--n6-framesize", "HD",
                                                        "--ae3-subsampling", "420"]
    assert (best["start_min"], best["end_min"], best["clips"]) == (1, 7, 6)


def test_program_without_brackets_is_unchanged():
    assert pool_capture.parse_program("cal_a:12,cal_b:10,stills:10,video:6x30") == [
        {"name": "cal_a", "start_min": 0, "end_min": 12},
        {"name": "cal_b", "start_min": 12, "end_min": 22},
        {"name": "stills", "start_min": 22, "end_min": 32},
        {"name": "video", "clips": 6, "clip_s": 30.0, "period_s": 60.0, "start_min": 32,
         "end_min": 38}]


@pytest.mark.parametrize("bad", [
    "video:6x30[--n6-framesize HD",            # unclosed
    "video:6x30[[--timing]]",                  # nested
    "video:6x30[--timing]x",                   # text after the bracket
    "video:6x30]",                             # stray close
    "video:6x30[]",                            # empty: leave the brackets out
    "stills:5[--timing]",                      # args only on video phases
    "video:6x30[--bogus 1]",                   # sync_video would refuse it
    "video:6x30[--n6-framesize]",              # missing value
    "video:6x30[--seconds 5]",                 # owned by pool_capture
    "video:6x30[--out /tmp/x]",
    "video@Best:6x30",                         # labels are lower case
    "video:6x30[--sec 5]",                     # QE 007 nit 1: an abbreviation of --seconds
    "video:6x30[--ou /tmp/x]",                 # ... and of --out
    "video@a:1x30,video@a:1x30",               # QE 007 nit 4: a label names one phase
])
def test_malformed_program_is_refused_before_any_recording(bad):
    with pytest.raises(ValueError):
        pool_capture.parse_program(bad)


def test_check_only_prints_the_program_and_its_sha_and_touches_nothing(monkeypatch, capsys):
    import hashlib
    import sys as _sys
    monkeypatch.setattr(pool_capture, "run", lambda a: pytest.fail("run() must not be called"))
    monkeypatch.setattr(_sys, "argv", ["pool_capture.py", "run", "--check-only", "--program", WED])
    assert pool_capture.main() == 0
    out = capsys.readouterr().out
    assert hashlib.sha256(WED.encode()).hexdigest() in out
    assert "video@best 1-7 min --n6-framesize HD --ae3-subsampling 420" in out
    monkeypatch.setattr(_sys, "argv", ["pool_capture.py", "run", "--check-only", "--program",
                                       "video:6x30[--bogus 1]"])
    with pytest.raises(SystemExit) as e:
        pool_capture.main()
    assert e.value.code == 2


def test_video_clip_passes_the_phase_args_and_records_what_the_boards_sent(tmp_path, monkeypatch):
    """PE 003 condition 2: each clip records its args, the boards' requested settings and the
    format of their first frame, so a wrong-setting clip shows at the pool pull."""
    def jpeg(w, h, y_hv):
        body = bytes([8]) + h.to_bytes(2, "big") + w.to_bytes(2, "big") + bytes(
            [3, 1, y_hv, 0, 2, 0x11, 1, 3, 0x11, 1])
        return b"\xff\xd8\xff\xc0" + (len(body) + 2).to_bytes(2, "big") + body + b"\xff\xd9"

    seen = {}

    class FakeProc:
        pid = 999_999_999

        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd
            out = Path(cmd[cmd.index("--out") + 1])
            out.mkdir(parents=True)
            cams = {}
            for cam, fr in (("openmv_n6", jpeg(1280, 800, 0x22)),
                            ("openmv_ae3", jpeg(640, 400, 0x22))):
                (out / f"{cam}.mjpeg").write_bytes(fr)
                (out / f"{cam}_frames.csv").write_text(
                    f"seq,ticks_ms,host_rx_mono,bytes,width,height\n0,1,0,{len(fr)},1,1\n")
                cams[cam] = {"ok": True, "frames": 1, "fps": 10.0, "fps_clip": 9.9,
                             "frames_lost": 0, "settings": {"framesize": "X"}}
            (out / "video_sync.json").write_text(json.dumps({"ok": True, "cameras": cams}))

        def communicate(self, timeout=None):
            return "", None

        def poll(self):
            return 0

    monkeypatch.setattr(pool_capture.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(pool_capture, "_killpg_quiet", lambda pgid: None)
    args = ["--n6-framesize", "HD", "--ae3-subsampling", "420"]
    v = pool_capture.run_video_clip(tmp_path, 7, 30.0, args)
    assert seen["cmd"][-4:] == args and v["args"] == args and v["ok"]
    assert v["measured"] == {"openmv_n6": {"dims": "1280x800", "subsampling": "420"},
                             "openmv_ae3": {"dims": "640x400", "subsampling": "420"}}
    assert v["settings"]["openmv_n6"] == {"framesize": "X"}
    assert "n6 1280x800 420" in pool_capture.health_text({"set": 7, "video": v})
    # a phase without args runs the same command as before
    pool_capture.run_video_clip(tmp_path, 8, 30.0)
    assert seen["cmd"][-4:] == ["--seconds", "30.0", "--out", str(tmp_path / "video" / "clip008")]


class _FakeLed:
    def __init__(self):
        self.modes: list[str] = []

    def set_mode(self, mode: str) -> None:
        self.modes.append(mode)


def test_clip_with_led_pulses_during_the_clip_then_steady():
    """Nick 10/20 (rig #138): recording around the clip, verdict_ok after; fault / low battery
    / no self-check never pulse."""
    led = _FakeLed()
    seen = []
    out = pool_capture.clip_with_led(led, "verdict_ok", False,
                                     lambda: seen.append(list(led.modes)) or {"ok": 1})
    assert out == {"ok": 1} and seen == [["recording"]]
    assert led.modes == ["recording", "verdict_ok"]
    led = _FakeLed()
    pool_capture.clip_with_led(led, "fault:3", False, dict)
    assert led.modes == ["fault:3", "fault:3"]                    # the code keeps showing
    led = _FakeLed()
    pool_capture.clip_with_led(led, "verdict_ok", True, dict)
    assert led.modes == ["lowbatt", "lowbatt"]                    # B beats REC
    assert pool_capture.clip_with_led(None, "verdict_ok", False, lambda: 7) == 7
    led = _FakeLed()
    assert pool_capture.clip_with_led(led, None, False, lambda: 8) == 8 and led.modes == []


def test_clip_with_led_restores_steady_when_the_clip_raises():
    led = _FakeLed()

    def boom():
        raise RuntimeError("rpicam-vid died")
    with pytest.raises(RuntimeError):
        pool_capture.clip_with_led(led, "verdict_ok", False, boom)
    assert led.modes == ["recording", "verdict_ok"]
