"""nrhyd under memory pressure (compression study debug, 2026-10-01): the AE3 failed with an
internal error at a high-quality knob while the N6 did not. Encodes one plane of a live frame at
rising knobs with the GC on, then off; ``BALLAST`` bytes are held to shrink the free heap (set
by the runner per board). Nothing is written to the sensor.
"""
import gc
import json
import sys
import time

import csi

BALLAST = 0
JUNK = 1_600_000  # garbage left before each encode, so a collection must run inside it
KNOBS = ((1, 32768), (3, 32768), (7, 35208), (12, 60000), (20, 73728))


def main():
    if "/flash" not in sys.path:
        sys.path.append("/flash")
    import nrhyd
    cam = csi.CSI()
    cam.reset()
    cam.pixformat(csi.BAYER)
    cam.framesize(csi.HD)
    for _ in range(10):
        cam.snapshot()
    img = cam.snapshot()
    W, H = img.width(), img.height()
    frame = bytearray(img.bytearray())
    del img
    gc.collect()
    ballast = []
    while BALLAST and sum(len(b) for b in ballast) < BALLAST:
        ballast.append(bytearray(1 << 16))
    out = bytearray(W * H // 4 + 8192)
    gc.collect()
    for gc_on in (True, False):
        if gc_on:
            gc.enable()
        else:
            gc.collect()
            gc.disable()
        for hf, gs in KNOBS:
            gc.collect()
            junk = [bytearray(512) for _ in range(JUNK // 528)] if gc_on else None
            junk = None  # now garbage (only reclaimed by a collection)
            free0 = gc.mem_free()
            t = time.ticks_ms()
            try:
                n, peak = nrhyd.encode(frame, 2 * W, W + 1, W // 2, H // 2, 0, 255, hf, gs, 4,
                                       out)
                res = {"n": n, "peak_or_where": peak}
            except Exception as exc:
                res = {"exc": repr(exc)}
            res.update({"gc": gc_on, "hf": hf, "gs": gs, "free_before": free0,
                        "ms": time.ticks_diff(time.ticks_ms(), t)})
            for _ in range(3):
                print("#R stress", json.dumps(res))
    gc.enable()
    print("#R done {}")


try:
    main()
except Exception as exc:
    print("#E", repr(exc))
