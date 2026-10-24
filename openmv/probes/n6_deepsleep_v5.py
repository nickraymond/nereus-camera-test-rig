"""N6 sleep retry (TEST_CARD_BENCH_1024 bench 4): one variant per run, the board logs to /flash.

``scripts/n6_deepsleep_retry.py`` writes ``VARIANT = "<name>"`` as the first line of a copy of this
file and runs it with mpremote. The base is OpenMV's own N6 example (v5.0.1
``53-N6-Boards/80-Low-Power/deep_sleep.py``): set the RTC, then ``machine.deepsleep(ms)``.
Every step is appended to /flash/ds_log.txt and synced before the next, because USB goes away in sleep.
"""
import machine
import os
import time

HOLD_MS = 20000


def log(msg):
    with open("/flash/ds_log.txt", "a") as f:
        f.write("%d %s %s\n" % (time.ticks_ms(), VARIANT, msg))  # noqa: F821 - set by the runner
    try:
        os.sync()
    except Exception:
        pass


rtc = machine.RTC()
rtc.datetime((2026, 10, 24, 6, 12, 0, 0, 0))  # as the official example: the RTC must run to wake
log("start reset_cause=%d rtc=%s" % (machine.reset_cause(), rtc.datetime()))

if VARIANT in ("csi_sleep", "csi_shutdown"):  # noqa: F821
    import csi

    cam = csi.CSI()
    cam.reset()
    cam.pixformat(csi.RGB565)
    cam.framesize(csi.VGA)
    cam.snapshot(time=1000)
    if VARIANT == "csi_sleep":  # noqa: F821
        cam.sleep(True)
    else:
        cam.shutdown(True)
    log("sensor " + VARIANT)  # noqa: F821

if VARIANT == "lightsleep":  # noqa: F821
    log("lightsleep")
    machine.lightsleep(HOLD_MS)
    log("woke from lightsleep")
else:
    log("deepsleep")
    time.sleep_ms(100)
    machine.deepsleep(HOLD_MS)
