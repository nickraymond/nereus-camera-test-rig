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
