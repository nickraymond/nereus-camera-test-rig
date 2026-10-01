"""wl53 lossy codec as compiled C (``nrwl53.mpy``) on a live HD Bayer frame (compression study,
wl53 bench, 2026-10-01). Sensor defaults (denoise + lens shading ON — Nick's call): nothing is
written to the sensor.

Needs ``/flash/nrwl53.mpy`` and ``/flash/nrpack.mpy`` (copied by the runner, removed after).
1. Capture, copy the frame (the camera keeps refilling its buffers).
2. Lossless reference: the 4 planes through ``nrpack`` — the host rebuilds the exact planes
   from these and re-encodes them with its own wl53 to check byte-identity.
3. wl53 at fixed scales (timing) and an on-board rate search (bisection on log Q) for a frame
   total of 0.4 bpp = 51,200 bytes; the final streams are sent back.
Output: ``#R`` results, ``#B`` CRC-checked payload lines sent 3× (console drops, OQ-55).
"""
import binascii
import gc
import hashlib
import json
import math
import sys
import time

import csi

CHUNK, COPIES, PACE_MS = 192, 3, 2
TARGET = 51200  # 0.4 bpp over the 1280 x 800 mosaic
FIXED_Q = (8.0, 20.0, 60.0)
OFFS = (("R", 1, 1), ("G1", 0, 1), ("G2", 1, 0), ("B", 0, 0))


def log(tag, obj):
    print("#R", tag, json.dumps(obj))


def sha(buf):
    return binascii.hexlify(hashlib.sha256(buf).digest()).decode()


def emit(name, data):
    mv = memoryview(data)
    for i in range(0, len(data), CHUNK):
        part = mv[i:i + CHUNK]
        line = "#B %s %d %08x %s" % (name, i, binascii.crc32(part) & 0xFFFFFFFF,
                                     binascii.b2a_base64(part).decode().strip())
        for _ in range(COPIES):
            print(line)
        time.sleep_ms(PACE_MS)
    print("#B", name, "end", len(data), sha(data))


def sqrt12_lut():
    """The study's sqrt curve, 8-bit linear → 12-bit codes (common.sqrt_lut(0, 255, 12))."""
    s = 4095 / math.sqrt(255)
    lut = bytearray(512)
    for v in range(256):
        c = math.floor(math.sqrt(v) * s + 0.5)
        lut[2 * v], lut[2 * v + 1] = c & 0xFF, c >> 8
    return lut


def encode_frame(nrwl53, frame, W, pw, ph, lut, q16, out, emit_tag=None):
    """All 4 planes through one shared output buffer (8 bpp: a dark, high-gain frame is mostly
    noise); with ``emit_tag`` each plane's stream is sent right after it is coded."""
    sizes, us = {}, {}
    for name, dy, dx in OFFS:
        t1 = time.ticks_us()
        sizes[name] = nrwl53.encode(frame, 2 * W, dy * W + dx, pw, ph, lut, q16, out)
        us[name] = time.ticks_diff(time.ticks_us(), t1)
        if emit_tag:
            emit("%s_%s" % (emit_tag, name), memoryview(out)[:sizes[name]])
    return sizes, us


def main():
    if "/flash" not in sys.path:
        sys.path.append("/flash")
    import nrpack
    import nrwl53
    cam = csi.CSI()
    cam.reset()
    cam.pixformat(csi.BAYER)
    cam.framesize(csi.HD)
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 1500:
        cam.snapshot()
    cam.auto_exposure(False, exposure_us=cam.exposure_us())
    cam.auto_gain(False, gain_db=cam.gain_db())
    for _ in range(3):
        cam.snapshot()
    img = cam.snapshot()
    W, H = img.width(), img.height()
    frame = bytearray(img.bytearray())
    del img
    pw, ph = W // 2, H // 2
    mean = sum(frame[i] for i in range(0, len(frame), 1009)) / (len(frame) // 1009 + 1)
    log("frame", {"w": W, "h": H, "exposure_us": cam.exposure_us(), "gain_db": cam.gain_db(),
                  "mean_dn": mean, "sha256": sha(frame)})
    gc.collect()
    heap0 = gc.mem_free()
    out = bytearray(pw * ph + 8192)
    for name, dy, dx in OFFS:  # lossless reference planes
        n = nrpack.encode(frame, 2 * W, dy * W + dx, pw, ph, 8, out)
        emit("C_" + name, memoryview(out)[:n])
    del out
    gc.collect()
    lut = sqrt12_lut()
    outs = bytearray(pw * ph + 4096)
    heap1 = gc.mem_free()
    fixed = []
    for q in FIXED_Q:
        sizes, us = encode_frame(nrwl53, frame, W, pw, ph, lut, int(q * 16), outs)
        fixed.append({"q": q, "bytes": sizes, "us": us, "total_bytes": sum(sizes.values()),
                      "total_us": sum(us.values())})
    gc.collect()
    log("fixed", {"runs": fixed, "heap_free_before": heap0, "heap_free_with_buffers": heap1,
                  "heap_free_after": gc.mem_free()})
    # rate search: bisection on log Q for TARGET bytes over the 4 planes
    t1 = time.ticks_ms()
    lo, hi, steps = math.log(0.5), math.log(4000.0), []
    best = None
    for _ in range(10):
        q = math.exp((lo + hi) / 2)
        sizes, us = encode_frame(nrwl53, frame, W, pw, ph, lut, int(q * 16), outs)
        tot = sum(sizes.values())
        steps.append({"q16": int(q * 16), "bytes": tot})
        if best is None or abs(tot - TARGET) < abs(best[1] - TARGET):
            best = (int(q * 16), tot)
        if abs(tot / TARGET - 1) <= 0.03:
            break
        if tot > TARGET:
            lo = math.log(q)
        else:
            hi = math.log(q)
    q16 = best[0]
    search_ms = time.ticks_diff(time.ticks_ms(), t1)
    sizes, us = encode_frame(nrwl53, frame, W, pw, ph, lut, q16, outs, "W_%d" % q16)
    log("rate_search", {"target": TARGET, "steps": steps, "q16": q16, "bytes": sizes,
                        "us": us, "search_ms": search_ms})
    log("done", {"ok": True})


try:
    main()
except Exception as exc:
    print("#E", repr(exc))
