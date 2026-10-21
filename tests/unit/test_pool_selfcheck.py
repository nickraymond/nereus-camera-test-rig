"""Boot camera self-check (scripts/pool_selfcheck.py) and its LED verdict patterns
(scripts/pool_led.py modes idle / verdict_ok / fault:N). Fakes only, no hardware."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sc = _load("pool_selfcheck")
led = _load("pool_led")
SERIALS = {"openmv_n6": "N6SER", "openmv_ae3": "AE3SER"}


def _ok_board(serial):
    return {"ok": True, "why": "ok"}


def _ok_imx():
    return {"ok": True, "why": "ok"}


def test_all_ok_is_the_slow_blink():
    r = sc.run_check(SERIALS, board_check=_ok_board, imx_check=_ok_imx)
    assert r["ok"] and r["faults"] == [] and r["codes"] == []
    assert sc.led_mode(r) == "verdict_ok"
    assert "OK 3/3" in sc.summary(r)


def test_each_camera_gets_its_own_code():
    dead = lambda serial: {"ok": False, "why": "absent"}  # noqa: E731
    r = sc.run_check(SERIALS, board_check=lambda s: dead(s) if s == "AE3SER" else _ok_board(s),
                     imx_check=_ok_imx)
    assert r["faults"] == ["openmv_ae3"] and sc.led_mode(r) == "fault:3"
    r = sc.run_check(SERIALS, board_check=lambda s: dead(s) if s == "N6SER" else _ok_board(s),
                     imx_check=_ok_imx)
    assert sc.led_mode(r) == "fault:2"
    r = sc.run_check(SERIALS, board_check=_ok_board,
                     imx_check=lambda: {"ok": False, "why": "in_use"})
    assert sc.led_mode(r) == "fault:1"
    assert "imx708 (in_use)" in sc.summary(r) and "1 flashes" in sc.summary(r)


def test_two_faults_alternate_in_camera_order_and_a_crashing_check_is_a_fault():
    def boom(serial):
        raise RuntimeError("serial exploded")
    r = sc.run_check(SERIALS, board_check=boom, imx_check=_ok_imx)
    assert r["faults"] == ["openmv_n6", "openmv_ae3"] and sc.led_mode(r) == "fault:2,3"
    assert r["cameras"]["openmv_n6"]["why"] == "check_error"


def test_check_board_waits_for_a_late_port_then_probes():
    ports = iter([None, None, "/dev/serial/by-id/x"])
    slept = []
    rec = sc.check_board("N6SER", wait_s=10, probe=lambda s: {"ok": True, "stage": "replied"},
                         port_of=lambda s: next(ports), sleep=slept.append)
    assert rec["ok"] and rec["why"] == "ok" and len(slept) == 2


def test_check_board_absent_and_wedged_reasons():
    rec = sc.check_board("X", wait_s=0, probe=lambda s: {"ok": False, "stage": "absent"},
                         port_of=lambda s: None)
    assert rec == {"ok": False, "why": "absent", "seconds": rec["seconds"]}
    rec = sc.check_board("X", wait_s=0, port_of=lambda s: "/p",
                         probe=lambda s: {"ok": False, "stage": "write_timeout", "error": "t"})
    assert rec["why"] == "write_timeout" and rec["error"] == "t"
    rec = sc.check_board("X", wait_s=0, port_of=lambda s: "/p",   # a REPL echo of the request
                         probe=lambda s: {"ok": False, "stage": "replied"})
    assert rec["why"] == "bad_reply" and not rec["ok"]


def test_check_imx708_listed_open_in_use():
    calls = []

    def run_ok(cmd, timeout):
        calls.append(cmd)
        return 0, "0 : imx708_wide [4608x2592]" if "--list-cameras" in cmd else ""
    assert sc.check_imx708(run=run_ok)["ok"] and len(calls) == 2
    assert sc.check_imx708(run=lambda c, t: (0, "No cameras available!"))["why"] == "not_listed"

    def run_busy(cmd, timeout):
        if "--list-cameras" in cmd:
            return 0, "0 : imx708_wide"
        return 255, "Pipeline handler in use by another process\nfailed to acquire camera"
    assert sc.check_imx708(run=run_busy)["why"] == "in_use"


def test_verdict_led_mode_low_battery_wins_recording_pulses_fault_stays():
    assert sc.verdict_led_mode("verdict_ok", True) == "lowbatt"
    assert sc.verdict_led_mode("fault:3", True) == "lowbatt"
    assert sc.verdict_led_mode("verdict_ok", True, recording=True) == "lowbatt"   # B beats REC
    assert sc.verdict_led_mode("verdict_ok", False) == "verdict_ok"  # driver: blinks, steady
    assert sc.verdict_led_mode("verdict_ok", False, recording=True) == "recording"
    assert sc.verdict_led_mode("fault:3", False, recording=True) == "fault:3"  # code stays


class FakeWriter:
    def __init__(self):
        self.writes: list[tuple[float, int]] = []

    def __call__(self, state: int) -> None:
        self.writes.append((time.monotonic(), state))


def _drive(mode: str, seconds: float, monkeypatch, initial: str = "idle") -> list[int]:
    monkeypatch.setattr(led, "F_ON_S", 0.02)
    monkeypatch.setattr(led, "F_OFF_S", 0.03)
    monkeypatch.setattr(led, "F_DARK_S", 0.25)
    monkeypatch.setattr(led, "OK_ON_S", 0.1)
    monkeypatch.setattr(led, "OK_OFF_S", 0.1)
    w = FakeWriter()
    d = led.LedDriver(writer=w, mode=initial)
    d.start()
    if mode != initial:
        d.set_mode(mode)
    time.sleep(seconds)
    d.stop(final=None)
    return [s for _, s in w.writes]


def test_idle_writes_nothing(monkeypatch):
    assert _drive("idle", 0.3, monkeypatch) == []


def test_fault_code_is_n_flashes_then_dark(monkeypatch):
    states = _drive("fault:3", 0.5, monkeypatch)   # one round = 3 x 0.05 s + 0.25 s dark
    assert states[:6] == [led.LED_ON, led.LED_OFF] * 3
    assert states[6:8] == [led.LED_ON, led.LED_OFF]  # the next round started after the dark
    assert len(states) <= 12                        # no extra writes during the dark


def test_two_codes_alternate(monkeypatch):
    states = _drive("fault:1,2", 0.5, monkeypatch)  # 1 flash, dark, 2 flashes, dark
    assert states[:6] == [led.LED_ON, led.LED_OFF] * 3


def test_ok_blink_then_steady(monkeypatch):
    w = FakeWriter()
    monkeypatch.setattr(led, "OK_ON_S", 0.1)
    monkeypatch.setattr(led, "OK_OFF_S", 0.1)
    d = led.LedDriver(writer=w, mode="verdict_ok")
    d.start()
    time.sleep(0.45)
    d.set_mode("ok")                                # a non-OK-family mode cuts the blinks
    time.sleep(0.3)
    d.stop(final=None)
    states = [s for _, s in w.writes]
    assert states[:4] == [led.LED_ON, led.LED_OFF, led.LED_ON, led.LED_OFF]
    assert states[-1] == led.LED_ON                 # steady: written once, then quiet
    quiet_from = w.writes[-1][0]
    assert time.monotonic() - quiet_from > 0.2 or len(states) <= 6


def test_low_battery_overrides_a_fault_code(monkeypatch):
    states = _drive("fault:3", 0.15, monkeypatch)
    assert states[0] == led.LED_ON
    w = FakeWriter()
    d = led.LedDriver(writer=w, mode="fault:3")
    d.start()
    time.sleep(0.05)
    d.set_mode("lowbatt")
    time.sleep(0.3)
    d.stop(final=None)
    assert w.writes[-1][1] == led.LED_FLASHING


def test_a_slow_write_does_not_stretch_the_phase(monkeypatch):
    """Bench 10/15 cold boot: a 1.7 s LED write stretched a 2 s blink phase to 3.7 s."""
    monkeypatch.setattr(led, "OK_ON_S", 0.3)
    monkeypatch.setattr(led, "OK_OFF_S", 0.3)
    calls = []

    def slow_writer(state):
        calls.append(time.monotonic())
        if len(calls) == 2:
            time.sleep(0.2)          # one slow write (lock wait), shorter than the phase
    d = led.LedDriver(writer=slow_writer, mode="verdict_ok")
    d.start()
    time.sleep(1.0)
    d.stop(final=None)
    gaps = [b - a for a, b in zip(calls, calls[1:])]
    assert all(0.25 <= g <= 0.4 for g in gaps[:3]), gaps   # the phase is timed from the write start


def test_led_writes_wait_only_briefly_for_the_shared_lock(monkeypatch):
    seen = {}
    monkeypatch.setattr(led, "ups_cli", lambda *a, **k: seen.update(k) or "")
    led.set_led(led.LED_ON)
    assert seen["lock_timeout_s"] == led.LED_LOCK_WAIT_S <= 0.25


def _seq(monkeypatch, on=0.03, off=0.03, solid=0.2):
    monkeypatch.setattr(led, "OK_ON_S", on)
    monkeypatch.setattr(led, "OK_OFF_S", off)
    monkeypatch.setattr(led, "OK_BLINKS", 3)
    monkeypatch.setattr(led, "READY_SOLID_S", solid)
    w = FakeWriter()
    return w, led.LedDriver(writer=w, mode="verdict_ok")


def test_ok_blinks_play_three_times_then_steady_once(monkeypatch):
    """Nick 10/20: one blink sequence after a fresh boot, then steady (no 10-min blink)."""
    assert led.OK_BLINKS == 5 and led.READY_SOLID_S == 5.0  # Nick 10/20 PM: 5 blinks
    w, d = _seq(monkeypatch)
    d.start()
    time.sleep(0.6)
    d.stop(final=None)
    assert [s for _, s in w.writes] == [led.LED_ON, led.LED_OFF] * 3 + [led.LED_ON]


def test_recording_waits_for_the_blinks_and_5s_steady_then_pulses_once(monkeypatch):
    """A clip starting mid-sequence does not cut the blinks; pulsing follows READY_SOLID_S of
    steady ON, is written once, and the end of the clip goes back to steady once."""
    w, d = _seq(monkeypatch)
    d.start()
    time.sleep(0.05)
    d.set_mode("recording")                         # the T+1 clip starts mid-sequence
    time.sleep(0.6)
    d.set_mode("verdict_ok")                        # clip done: the loop's call after the clip
    time.sleep(0.15)
    d.set_mode("recording")                         # the next clip: steady was > 0.2 s already?
    time.sleep(0.3)
    d.stop(final=None)
    states = [s for _, s in w.writes]
    assert states[:7] == [led.LED_ON, led.LED_OFF] * 3 + [led.LED_ON]
    assert states[7:] == [led.LED_PULSING, led.LED_ON, led.LED_PULSING]
    t_steady, t_pulse = w.writes[6][0], w.writes[7][0]
    assert t_pulse - t_steady >= 0.19               # >= READY_SOLID_S of steady first
    assert w.writes[9][0] - w.writes[8][0] >= 0.19  # between clips too


def test_blinks_play_once_per_driver_not_after_every_set(monkeypatch):
    w, d = _seq(monkeypatch, solid=0.05)
    d.start()
    time.sleep(0.4)
    for _ in range(3):                              # the loop re-sets the verdict after each set
        d.set_mode("recording")
        time.sleep(0.1)
        d.set_mode("verdict_ok")
        time.sleep(0.1)
    d.stop(final=None)
    states = [s for _, s in w.writes]
    assert states.count(led.LED_OFF) == 3           # only the boot blinks ever write OFF


def test_low_battery_overrides_recording(monkeypatch):
    w, d = _seq(monkeypatch, solid=0.05)
    d.start()
    d.set_mode("recording")
    time.sleep(0.4)
    d.set_mode("lowbatt")
    time.sleep(0.1)
    d.stop(final=None)
    states = [s for _, s in w.writes]
    assert led.LED_PULSING in states and states[-1] == led.LED_FLASHING
