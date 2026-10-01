"""Lossless packer as compiled C (native module ``nrpack.mpy``) vs the viper port, on a live
HD Bayer frame (compression study Phase 2b).

Needs ``/flash/nrpack.mpy`` (``compression_study/natmod/build.sh``; copied by the runner and
removed afterwards). Sensor defaults only: no register is written.
Output as ``compress_probe_v5.py``: ``#R`` results, ``#B`` CRC-checked payload lines sent 3×.
"""
import binascii
import gc
import hashlib
import json
import sys
import time

import csi
import image
import micropython

CHUNK, COPIES, PACE_MS = 192, 3, 2


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


@micropython.viper
def split_plane(src: ptr8, dst: ptr8, w: int, h: int, dy: int, dx: int):  # noqa: F821
    pw = w >> 1
    for y in range(h >> 1):
        s = (2 * y + dy) * w + dx
        d = y * pw
        for x in range(pw):
            dst[d + x] = src[s + 2 * x]


def main():
    if "/flash" not in sys.path:
        sys.path.append("/flash")
    t1 = time.ticks_us()
    import nrpack
    log("import", {"us": time.ticks_diff(time.ticks_us(), t1)})
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
    # A copy: the camera keeps filling its frame buffers in the background, so the snapshot's
    # bytes change within seconds (first run: every plane checksum moved before it was coded).
    t1 = time.ticks_us()
    frame = bytearray(img.bytearray())
    copy_us = time.ticks_diff(time.ticks_us(), t1)
    pw, ph = W // 2, H // 2
    offs = (("R", 1, 1), ("G1", 0, 1), ("G2", 1, 0), ("B", 0, 0))
    shas = {}
    p = image.Image(pw, ph, image.GRAYSCALE)
    for name, dy, dx in offs:
        split_plane(frame, p.bytearray(), W, H, dy, dx)
        shas[name] = sha(p.bytearray())
    log("frame", {"w": W, "h": H, "exposure_us": cam.exposure_us(), "gain_db": cam.gain_db(),
                  "copy_us": copy_us,
                  "plane_sha256": shas})
    out = bytearray(pw * ph + 8192)
    gc.collect()
    free0 = gc.mem_free()
    res = {}
    for name, dy, dx in offs:
        t1 = time.ticks_us()
        n = nrpack.encode(frame, 2 * W, dy * W + dx, pw, ph, 8, out)
        res[name] = {"bytes": n, "us": time.ticks_diff(time.ticks_us(), t1)}
        emit("C_" + name, memoryview(out)[:n])
    log("natmod", {"planes": res, "total_us": sum(r["us"] for r in res.values()),
                   "heap_used_bytes": free0 - gc.mem_free()})
    # repeat for a steady timing (no emit)
    t1 = time.ticks_us()
    for _ in range(5):
        for name, dy, dx in offs:
            nrpack.encode(frame, 2 * W, dy * W + dx, pw, ph, 8, out)
    log("natmod_x5", {"us_per_frame": time.ticks_diff(time.ticks_us(), t1) // 5})
    log("done", {"ok": True})


try:
    main()
except Exception as exc:
    print("#E", repr(exc))
