"""Video feasibility probe (video codec study) — how fast can an OpenMV board make HD frames
on its own, and can it hold a 15 s clip in RAM?

Run on the Pi via ``compression_study/phase2/pi_run_probe.py --probe <this file>``
(reset before/after, handshake, UPS power). Same sensor path as the rig's ``start_stream``
(legacy ``sensor`` API, RGB565 HD, firmware JPEG ``img.compress``), minus the USB write, so
the difference to the streamed rate is the USB cost.

Output lines: ``#R <tag> <json>`` (short, the console drops long lines — OQ-55), ``#E`` errors.
Nothing is written to /flash.
"""
import gc
import json
import os
import time

import sensor

LOOP_MS = 4000
QUALITIES = (95, 90, 80, 60)


def log(tag, obj):
    print("#R", tag, json.dumps(obj))


def fs_info():
    out = {"root": os.listdir("/")}
    for p in ("/flash", "/sdcard", "/sd"):
        try:
            s = os.statvfs(p)
            out[p] = {"free": s[0] * s[3], "total": s[0] * s[2]}
        except OSError:
            pass
    log("fs", out)


def rate(label, quality=None):
    n = 0
    nbytes = 0
    t_snap = 0
    t_comp = 0
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < LOOP_MS:
        a = time.ticks_us()
        img = sensor.snapshot()
        b = time.ticks_us()
        t_snap += time.ticks_diff(b, a)
        if quality is not None:
            img.compress(quality=quality)
            t_comp += time.ticks_diff(time.ticks_us(), b)
            nbytes += img.size()
        n += 1
    ms = time.ticks_diff(time.ticks_ms(), t0)
    log("rate", {"what": label, "q": quality, "frames": n, "fps": round(1000 * n / ms, 2),
                 "snap_ms": round(t_snap / n / 1000, 2),
                 "jpeg_ms": round(t_comp / n / 1000, 2) if quality else None,
                 "kB": round(nbytes / n / 1000, 1) if quality else None,
                 "exp_us": sensor.get_exposure_us()})


def buffer_test(quality, limit=450):
    """Keep compressed frames in RAM until 'limit' frames or MemoryError."""
    gc.collect()
    free0 = gc.mem_free()
    frames = []
    total = 0
    t0 = time.ticks_ms()
    err = None
    try:
        while len(frames) < limit:
            img = sensor.snapshot()
            img.compress(quality=quality)
            frames.append(bytes(img.bytearray()))
            total += len(frames[-1])
    except MemoryError:
        err = "MemoryError"
    ms = time.ticks_diff(time.ticks_ms(), t0)
    n = len(frames)
    frames = None
    gc.collect()
    log("buffer", {"q": quality, "frames": n, "MB": round(total / 1e6, 2),
                   "fps": round(1000 * n / ms, 2), "heap_free0_MB": round(free0 / 1e6, 2),
                   "stopped_by": err or "limit"})


def main():
    gc.collect()
    log("mem", {"free": gc.mem_free(), "alloc": gc.mem_alloc()})
    fs_info()
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(sensor.HD)
    sensor.skip_frames(time=1500)
    img = sensor.snapshot()
    log("frame", {"w": img.width(), "h": img.height()})
    rate("snapshot_only")
    for q in QUALITIES:
        rate("snapshot_jpeg", q)
    buffer_test(90)
    log("done", {})


try:
    main()
except Exception as exc:  # noqa: BLE001
    print("#E", repr(exc))
