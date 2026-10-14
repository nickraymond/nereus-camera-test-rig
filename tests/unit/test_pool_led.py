"""Nick's pool LED codes (scripts/pool_led.py): decision precedence, the A/B/C driver with a
fake LED writer, and the fail-soft shared I2C lock. No hardware."""

from __future__ import annotations

import fcntl
import importlib.util
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("pool_led", ROOT / "scripts" / "pool_led.py")
led = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(led)


def test_precedence_low_battery_over_camera_over_ok():
    low = [3150, 3160, 3170]
    assert led.decide("ok", low, True, False) == "lowbatt"          # B beats A
    assert led.decide("ok", low, True, True) == "lowbatt"
    assert led.decide("ok", [3400] * 3, True, False) == "camera"    # A
    assert led.decide("camera", [3400] * 3, True, True) == "ok"     # a fully OK set clears A
    assert led.decide("ok", [3400] * 3, True, None) == "ok"         # no set yet = OK


def test_low_battery_needs_battery_three_readings_and_has_hysteresis():
    assert led.decide("ok", [3100, 3100], True, True) == "ok"       # < 3 readings: no verdict
    assert led.decide("ok", [3100] * 3, False, True) == "ok"        # on the charger: never B
    assert led.decide("ok", [3100, 3300, 3300], True, True) == "ok" # one dip is not B (median)
    assert led.decide("lowbatt", [3210] * 3, True, True) == "lowbatt"  # sticky within +30 mV
    assert led.decide("lowbatt", [3240] * 3, True, True) == "ok"       # clears above 3230
    assert led.decide("ok", [3150] * 3, True, True, low_vbat_mv=3100) == "ok"  # flag honoured


class FakeWriter:
    def __init__(self):
        self.writes: list[tuple[float, int]] = []

    def __call__(self, state: int) -> None:
        self.writes.append((time.monotonic(), state))


def test_driver_writes_c_and_b_once_and_restores_default(monkeypatch):
    w = FakeWriter()
    d = led.LedDriver(writer=w)
    d.start()
    time.sleep(0.2)
    d.set_mode("lowbatt")
    time.sleep(0.2)
    d.set_mode("ok")
    time.sleep(0.2)
    d.stop()
    states = [s for _, s in w.writes]
    assert states == [led.LED_ON, led.LED_FLASHING, led.LED_ON, led.FIRMWARE_DEFAULT]


def test_pattern_a_short_flash_long_dwell(monkeypatch):
    monkeypatch.setattr(led, "A_ON_S", 0.05)
    monkeypatch.setattr(led, "A_OFF_S", 0.25)
    w = FakeWriter()
    d = led.LedDriver(writer=w)
    d.set_mode("camera")
    d.start()
    time.sleep(1.0)
    d.stop(final=led.LED_FLASHING)  # e.g. a battery stop keeps B showing
    states = [s for _, s in w.writes]
    assert states[:4] == [led.LED_ON, led.LED_OFF, led.LED_ON, led.LED_OFF]
    assert states[-1] == led.LED_FLASHING
    on_off = [(w.writes[i + 1][0] - w.writes[i][0]) for i in range(0, 4, 2)]
    assert all(0.04 <= dt <= 0.15 for dt in on_off)                  # short ON
    # writes stay rare: ~2 per period
    assert len(states) <= 2 * (1.0 / 0.30) + 3


def test_i2c_lock_is_fail_soft_when_held(tmp_path, monkeypatch):
    lock = tmp_path / "i2c.lock"
    monkeypatch.setattr(led, "I2C_LOCK", str(lock))
    holder = open(lock, "a")
    fcntl.flock(holder, fcntl.LOCK_EX)
    t0 = time.monotonic()
    with led.i2c_lock(timeout_s=0.2):
        waited = time.monotonic() - t0
    assert 0.15 <= waited < 1.0                                       # gave up, proceeded
    fcntl.flock(holder, fcntl.LOCK_UN)
    t0 = time.monotonic()
    with led.i2c_lock(timeout_s=0.2):
        assert time.monotonic() - t0 < 0.1                            # free: immediate
    holder.close()


def test_bar30_sessions_are_at_least_the_minimum_gap_apart(tmp_path, monkeypatch):
    monkeypatch.setattr(led, "I2C_LOCK", str(tmp_path / "i2c.lock"))
    monkeypatch.setattr(led, "BAR30_STAMP", str(tmp_path / "bar30.last"))
    starts = []
    for _ in range(3):
        with led.bar30_session(min_interval_s=0.3):
            starts.append(time.monotonic())
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(g >= 0.28 for g in gaps), gaps


def test_ups_read_only_the_four_registers(tmp_path, monkeypatch):
    calls = []
    fake = tmp_path / "lifepo4wered-cli"
    fake.write_text('#!/bin/sh\necho "$2" >> "' + str(tmp_path / "calls") + '"\necho 4970\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setattr(led, "I2C_LOCK", str(tmp_path / "i2c.lock"))
    v = led.ups_read()
    calls = (tmp_path / "calls").read_text().split()
    assert calls == ["VIN", "VBAT", "VOUT", "IOUT"] and v == dict.fromkeys(calls, 4970)
