#!/usr/bin/env python3
"""pool_led.py — Nick's pool LED codes on the LiFePO4wered/Pi+ green PWR LED, plus the shared
I2C lock.

Nick's spec (2026-10-13 8:45 PM), readable through the housing:
    C  CONSTANT ON                            = system OK
    A  one short flash, long dwell off, ...   = one or more cameras NOT working
    B  continuous rapid flashing              = LOW BATTERY
    Precedence: B > A > C.

What the board supports (lifepo4wered-data.h, ~/LiFePO4wered-Pi on the rig): LED_STATE
"when Pi on" = 0 off, 1 on, 2 pulsing, 3 flashing. There are no timing registers. So:
    C = LED_STATE 1, written once.
    B = LED_STATE 3, the firmware's own flashing, written once. Whether its rate reads as
        "rapid" through the housing is checked by eye in the bench demo.
    A = driven from the Pi: LED_STATE 1 for 0.25 s, then 0 for 3.75 s. That is 2 tiny I2C
        writes every 4 s, 0.5 writes/s, the slowest that still reads as a beacon.
The firmware's own boot/shutdown breathing is never touched: the loop only writes while it
runs. On stop it restores 1, the firmware's normal steady-on (Nick: "leave the LED in the
firmware default when the loop stops").

One I2C lock: bus 1 carries the UPS (0x43) and the Bar30 (0x76). Every pool-mode access takes
an flock on I2C_LOCK first: LED writes, UPS reads through lifepo4wered-cli, and the Bar30
session in depth_ms5837.py. The kernel already makes each SMBus transfer atomic; the lock only
keeps our own multi-step users from interleaving. It is fail-soft: if the lock cannot be taken
within 2 s, the operation goes ahead without it, so the lock can never stall a capture. The
root lifepo4wered-daemon (PI_RUNNING poll, 1/s) does not take it.

Boot self-check verdict (Nick, 10/15 ~3:40 PM, plan A; `pool_capture.py run --self-check`):
    OK     slow blink, 2 s on / 2 s off, OK_BLINKS
           times, then steady ON                   = all three cameras answered
    REC    the firmware's own pulsing (LED_STATE 2) = a video clip is recording (Nick 10/20: "one
           while a clip records, steady ON between    blink sequence after fresh boot ... 5 sec
           clips                                      solid green, then ... breathing ... when
                                                      it's recording"). Pulsing starts only once
                                                      the OK blinks are done and steady ON has
                                                      shown READY_SOLID_S, so the very first clip
                                                      (T+1) can start pulsing a few s late.
    F<N>   N quick flashes (0.2 s on / 0.3 s off),
           then 4 s dark, repeating                = camera N failed: 1 IMX708, 2 N6, 3 AE3;
                                                     two failed cameras alternate their codes
    B (low battery, above) still overrides both. Not steady ON for OK on purpose: steady is
    also the firmware's default whenever the Pi runs, so it cannot prove the check ran.
    I2C: OK = 0.5 writes/s; F3 = 6 writes per 5.5 s (~1.1/s), all to the UPS (0x43).
    Mode "idle" writes nothing (the firmware keeps the LED while the check runs).
    REC costs 2 writes per clip (pulsing on, steady after). The firmware also pulses while the
    Pi boots and shuts down: the order (blinks, then steady, then pulsing) is what tells them apart.
    A fault code stays during recording (it is the switch-off/on signal); B overrides REC.

CLI (bench demo / recovery, on the Pi):
    python3 scripts/pool_led.py demo A --seconds 20     # show one pattern, then restore
    python3 scripts/pool_led.py demo F2 --seconds 20    # the self-check fault code for the N6
    python3 scripts/pool_led.py demo SEQ --seconds 40   # OK blinks, 5 s steady, then REC pulsing
    python3 scripts/pool_led.py restore                 # LED_STATE 1 (firmware default)
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
import statistics
import subprocess
import sys
import threading
import time

I2C_LOCK = os.environ.get("POOL_I2C_LOCK", "/tmp/nereus_pool_i2c.lock")
LED_OFF, LED_ON, LED_PULSING, LED_FLASHING = 0, 1, 2, 3  # lifepo4wered-data.h LED_STATE_*
FIRMWARE_DEFAULT = LED_ON
# I2C budget (Nick 2026-10-13 9:08 PM: polling the Bar30 faster than every few seconds can
# wedge the shared bus, and then the LiFePO4wered misbehaves). All in one place:
A_ON_S, A_OFF_S = 0.25, 3.75          # pattern A: 2 LED writes per 4 s = 0.5 writes/s max
OK_ON_S, OK_OFF_S = 2.0, 2.0          # self-check OK: slow blink, 0.5 writes/s
OK_BLINKS = 5                         # the OK blink plays this many times, once per boot (20 s;
                                      # Nick 10/20: 5, more time to get into position)
READY_SOLID_S = 5.0                   # steady ON shown at least this long before REC pulsing
F_ON_S, F_OFF_S, F_DARK_S = 0.2, 0.3, 4.0   # self-check fault code: N flashes, then dark
BAR30_MIN_INTERVAL_S = 3.0            # >= 3 s between any two Bar30 sessions (pool: ~2 per set)
UPS_KEYS = ("VIN", "VBAT", "VOUT", "IOUT")  # the only UPS registers the loop polls
BAR30_STAMP = os.environ.get("POOL_BAR30_STAMP", "/tmp/nereus_pool_bar30.last")
LOW_VBAT_MV = 3200                    # B: on battery, median of 3 readings below this
LOW_VBAT_HYST_MV = 30                 # clears above LOW_VBAT_MV + this


@contextlib.contextmanager
def i2c_lock(timeout_s: float = 2.0):
    """flock on I2C_LOCK, giving up after ``timeout_s`` (then the caller proceeds unlocked)."""
    fh = None
    try:
        fh = open(I2C_LOCK, "a")
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    break  # fail-soft: proceed without the lock
                time.sleep(0.02)
    except OSError:
        pass
    try:
        yield
    finally:
        if fh is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()


def ups_cli(*args: str, timeout: float = 5.0, lock_timeout_s: float = 2.0) -> str:
    """lifepo4wered-cli under the I2C lock; '' on any failure (never raises)."""
    with i2c_lock(lock_timeout_s):
        try:
            return subprocess.run(["lifepo4wered-cli", *args], capture_output=True, text=True,
                                  timeout=timeout).stdout
        except (OSError, subprocess.SubprocessError):
            return ""


def ups_read(keys=UPS_KEYS) -> dict:
    """Just these UPS registers (``lifepo4wered-cli get <KEY>`` each, one lock hold) instead of
    the full ~37-register dump: {} if any read fails."""
    out = {}
    with i2c_lock():
        for k in keys:
            try:
                v = subprocess.run(["lifepo4wered-cli", "get", k], capture_output=True,
                                   text=True, timeout=5).stdout.strip()
                out[k] = int(v.split("=")[-1])
            except (OSError, subprocess.SubprocessError, ValueError):
                return {}
    return out


@contextlib.contextmanager
def bar30_session(min_interval_s: float | None = None, max_wait_s: float = 10.0):
    """Hold the I2C lock for one Bar30 session, starting it no sooner than
    BAR30_MIN_INTERVAL_S after the previous one (any process: the time is kept in BAR30_STAMP).
    The wait happens outside the lock, so LED/UPS users are never blocked by it."""
    gap = BAR30_MIN_INTERVAL_S if min_interval_s is None else min_interval_s
    deadline = time.monotonic() + max_wait_s
    while True:
        try:
            last = float(open(BAR30_STAMP).read().strip() or 0)
        except (OSError, ValueError):
            last = 0.0
        wait = last + gap - time.time()
        if wait <= 0 or time.monotonic() > deadline:
            break
        time.sleep(min(wait, 0.5))
    with i2c_lock():
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                with open(BAR30_STAMP, "w") as fh:
                    fh.write(f"{time.time():.3f}\n")


# An LED write waits at most this long for the shared lock, then goes ahead unlocked: it is a
# single register write, and the kernel serializes each SMBus transfer anyway. Bench 10/15: right
# after a cold boot the UPS logger's 4 locked reads took ~1.7 s, and a blink phase waiting
# behind them stretched from 2.0 s to 3.7 s (a fault code's flashes could split the same way).
LED_LOCK_WAIT_S = 0.2


def set_led(state: int) -> None:
    ups_cli("set", "LED_STATE", str(state), lock_timeout_s=LED_LOCK_WAIT_S)


def decide(mode: str, vbat_hist: list[int], on_battery: bool, last_set_all_ok: bool | None,
           low_vbat_mv: int = LOW_VBAT_MV) -> str:
    """Next LED mode: "lowbatt" (B) > "camera" (A) > "ok" (C).

    lowbatt: on battery AND the median of the last 3 VBAT readings < low_vbat_mv. It is
        sticky until the median rises above low_vbat_mv + LOW_VBAT_HYST_MV or the charger is in.
    camera: any camera failed (or was benched) in the most recent set; a fully OK set clears it.
        ``None`` = no set yet, counted as OK.
    """
    med = statistics.median(vbat_hist[-3:]) if len(vbat_hist) >= 3 else None
    if on_battery and med is not None:
        limit = low_vbat_mv + (LOW_VBAT_HYST_MV if mode == "lowbatt" else 0)
        if med < limit:
            return "lowbatt"
    return "camera" if last_set_all_ok is False else "ok"


# The OK sequence modes: while the mode stays in this family, the boot OK blinks play out in
# full (a clip starting mid-sequence does not cut them short).
OK_FAMILY = ("verdict_ok", "recording")


class LedDriver(threading.Thread):
    """Shows one mode. C and B are written once on entry; A is toggled from here. stop()
    restores the firmware default. Every write is fail-soft (ups_cli never raises).

    Self-check modes: "verdict_ok" plays the OK blink OK_BLINKS times (once per driver, i.e.
    once per boot), then holds steady ON; "recording" pulses (one write), but only after that
    sequence and READY_SOLID_S of steady ON."""

    def __init__(self, writer=set_led, mode: str = "ok"):
        super().__init__(daemon=True)
        self.mode, self._shown, self._write = mode, None, writer
        self.stopped = False
        self._ok_blinks_done, self._steady_since = 0, None
        self._wake, self._stop = threading.Event(), threading.Event()

    def set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self._wake.set()

    def run(self) -> None:
        self._wake.clear()  # a mode set before start() must not cut the first A flash short
        while not self._stop.is_set():
            mode = self.mode
            if mode == "idle":  # self-check running: the LED stays the firmware's
                self._sleep(1.0)
                continue
            if mode in OK_FAMILY and self._ok_blinks_done < OK_BLINKS:
                self._shown = "verdict_ok"
                if self._show(LED_ON, OK_ON_S, hold=True) or self._show(LED_OFF, OK_OFF_S,
                                                                         hold=True):
                    continue  # left the OK family (low battery, stop): no more blinks counted
                self._ok_blinks_done += 1
                continue
            if mode in OK_FAMILY:
                if mode == "recording" and self._shown == "recording":
                    self._sleep(1.0)  # pulsing: written once, the firmware animates it
                    continue
                if self._shown != "ok":
                    self._write(LED_ON)
                    self._shown, self._steady_since = "ok", time.monotonic()
                wait = (0.0 if mode == "verdict_ok" or self._steady_since is None
                        else self._steady_since + READY_SOLID_S - time.monotonic())
                if mode == "recording" and wait <= 0:
                    self._write(LED_PULSING)
                    self._shown, self._steady_since = "recording", None
                    continue
                self._sleep(wait if wait > 0 else 1.0)
                continue
            if mode.startswith("fault:"):
                self._shown = mode
                self._fault_codes([int(c) for c in mode[6:].split(",") if c.strip()])
                continue
            if mode == "camera":
                self._write(LED_ON)
                self._shown = "camera"
                if self._sleep(A_ON_S):
                    continue
                self._write(LED_OFF)
                self._sleep(A_OFF_S)
                continue
            if self._shown != mode:
                self._write(LED_FLASHING if mode == "lowbatt" else LED_ON)
                self._shown = mode
                # steady ON from "ok" counts toward READY_SOLID_S
                self._steady_since = time.monotonic() if mode == "ok" else None
            self._sleep(1.0)

    def _fault_codes(self, codes: list[int]) -> None:
        """One round: for each code N, N quick flashes then F_DARK_S dark. Returns early on a
        mode change or stop (the caller re-reads the mode)."""
        for n in codes or [0]:
            for _ in range(max(1, n)):
                if self._show(LED_ON, F_ON_S):
                    return
                if self._show(LED_OFF, F_OFF_S):
                    return
            if self._sleep(F_DARK_S):
                return

    def _show(self, state: int, hold_s: float, hold: bool = False) -> bool:
        """Write ``state`` and hold it ``hold_s`` from the START of the write, so a slow write
        does not stretch the phase (verdict patterns only). True if woken early; with ``hold``
        only a stop or a mode outside OK_FAMILY wakes it early."""
        t0 = time.monotonic()
        self._write(state)
        left = max(0.0, hold_s - (time.monotonic() - t0))
        return self._hold(left) if hold else self._sleep(left)

    def _hold(self, s: float) -> bool:
        """Sleep ``s``; True early only on stop or a mode outside OK_FAMILY."""
        end = time.monotonic() + s
        while (left := end - time.monotonic()) > 0:
            self._wake.wait(left)
            self._wake.clear()
            if self._stop.is_set() or self.mode not in OK_FAMILY:
                return True
        return False

    def _sleep(self, s: float) -> bool:
        """Sleep up to ``s``; True if woken early by a mode change or stop."""
        woke = self._wake.wait(s)
        self._wake.clear()
        return woke or self._stop.is_set()

    def stop(self, final: int | None = FIRMWARE_DEFAULT) -> None:
        self.stopped = True
        self._stop.set()
        self._wake.set()
        self.join(timeout=5)
        if final is not None:
            self._write(final)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="show one pattern on the real LED, then restore")
    d.add_argument("pattern", choices=("A", "B", "C", "OK", "F1", "F2", "F3", "REC", "SEQ"))
    d.add_argument("--seconds", type=float, default=20.0)
    sub.add_parser("restore", help="LED_STATE 1 (firmware default)")
    args = ap.parse_args(argv)
    if args.cmd == "restore":
        set_led(FIRMWARE_DEFAULT)
        return 0
    drv = LedDriver()
    if args.pattern == "REC":  # no OK blinks first: READY_SOLID_S steady, then pulsing
        drv._ok_blinks_done = OK_BLINKS
    drv.set_mode({"A": "camera", "B": "lowbatt", "C": "ok", "OK": "verdict_ok",
                  "F1": "fault:1", "F2": "fault:2", "F3": "fault:3", "REC": "recording",
                  "SEQ": "recording"}[args.pattern])
    drv.start()
    print(f"showing pattern {args.pattern} for {args.seconds:.0f} s", flush=True)
    time.sleep(args.seconds)
    drv.stop()
    print("restored LED_STATE 1", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
