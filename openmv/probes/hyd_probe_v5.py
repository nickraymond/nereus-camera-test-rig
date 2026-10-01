"""hydrium (``nrhyd.mpy``) vs wl53 (``nrwl53.mpy``) on one live HD Bayer frame (compression study,
hydrium bench, 2026-10-01). Sensor defaults (denoise + lens shading ON — Nick's call): nothing
is written to the sensor.

Needs ``/flash/nrhyd.mpy``, ``/flash/nrwl53.mpy`` and ``/flash/nrpack.mpy`` (copied by the
runner, removed after).
1. Capture, copy the frame (the camera keeps refilling its buffers).
2. Lossless reference: the 4 planes through ``nrpack`` — the host rebuilds the exact planes
   from these and re-encodes them with its own encoders to check byte-identity.
3. For each codec: fixed knobs (timing, heap), then an on-board rate search (bisection on the
   log knob) for a frame total of 0.4 bpp (51,200 B) and 0.8 bpp (102,400 B); the final
   streams are sent back.
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
TARGETS = (("T1", 51200), ("T2", 102400))  # 0.4 / 0.8 bpp over the 1280 x 800 mosaic
OFFS = (("R", 1, 1), ("G1", 0, 1), ("G2", 1, 0), ("B", 0, 0))
LF = 4  # hydrium LF divisor (desk-study variant hyd-256-lf4-lin)


def log(tag, obj):
    """One short ``#R`` line, printed 3× (the console drops ~512-byte blocks, OQ-55)."""
    line = "#R %s %s" % (tag, json.dumps(obj))
    for _ in range(COPIES):
        print(line)


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


def hyd_params(k):
    """Continuous knob k = HF multiplier × globalScale / 32768 (desk study, mcu_codec_bench)."""
    hf = max(1, int(math.floor(k)))
    gs = min(73728, max(1, int(32768 * k / hf + 0.5)))
    return hf, gs


class Codec:
    def __init__(self, name, mod, frame, W, lut=None):
        self.name, self.mod, self.frame, self.W, self.lut = name, mod, frame, W, lut
        self.peak = 0

    def label(self, k):
        if self.name == "H":
            return "%d_%d" % hyd_params(k)
        return "%d" % int(k * 16)

    def plane(self, dy, dx, pw, ph, k, out):
        off = dy * self.W + dx
        if self.name == "H":
            hf, gs = hyd_params(k)
            n, peak = self.mod.encode(self.frame, 2 * self.W, off, pw, ph, 0, 255, hf, gs, LF,
                                      out)
            self.peak = max(self.peak, peak)
            return n
        return self.mod.encode(self.frame, 2 * self.W, off, pw, ph, self.lut, int(k * 16), out)


def encode_frame(codec, pw, ph, k, out, emit_tag=None):
    """All 4 planes through one shared output buffer; with ``emit_tag`` each plane's stream is
    sent right after it is coded."""
    sizes, us = {}, {}
    for name, dy, dx in OFFS:
        t1 = time.ticks_us()
        sizes[name] = codec.plane(dy, dx, pw, ph, k, out)
        us[name] = time.ticks_diff(time.ticks_us(), t1)
        if emit_tag:
            emit("%s_%s" % (emit_tag, name), memoryview(out)[:sizes[name]])
    return sizes, us


def rate_search(codec, pw, ph, out, target, lo, hi, direction):
    """Bisection on log k for ``target`` bytes over the 4 planes (direction +1: bigger k →
    more bytes); keeps the closest knob, stops within 3 %."""
    t1 = time.ticks_ms()
    lo, hi, steps, best = math.log(lo), math.log(hi), [], None
    for _ in range(12):
        k = math.exp((lo + hi) / 2)
        sizes, us = encode_frame(codec, pw, ph, k, out)
        tot = sum(sizes.values())
        steps.append([codec.label(k), tot, sum(us.values()) // 1000])  # knob, bytes, ms
        if best is None or abs(tot - target) < abs(best[1] - target):
            best = (k, tot)
        if abs(tot / target - 1) <= 0.03:
            break
        if (tot > target) == (direction > 0):
            hi = math.log(k)
        else:
            lo = math.log(k)
    return best[0], steps, time.ticks_diff(time.ticks_ms(), t1)


def main():
    if "/flash" not in sys.path:
        sys.path.append("/flash")
    import nrhyd
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
    gc.collect()
    heap1 = gc.mem_free()
    codecs = (("H", Codec("H", nrhyd, frame, W), (0.3, 1.0, 3.0), (0.05, 40.0, +1)),
              ("W", Codec("W", nrwl53, frame, W, sqrt12_lut()), (8.0, 20.0, 60.0),
               (0.5, 4000.0, -1)))
    for tag, codec, fixed_k, (lo, hi, d) in codecs:
        for k in fixed_k:
            gc.collect()
            free0 = gc.mem_free()
            sizes, us = encode_frame(codec, pw, ph, k, out)
            log("fixed_" + tag, {"knob": codec.label(k), "bytes": sizes, "us": us,
                                 "heap_free_before": free0, "hyd_peak_heap": codec.peak})
        log("heap_" + tag, {"heap_free_before": heap0, "heap_free_with_buffers": heap1})
        for tname, target in TARGETS:
            k, steps, search_ms = rate_search(codec, pw, ph, out, target, lo, hi, d)
            lab = codec.label(k)
            sizes, us = encode_frame(codec, pw, ph, k, out, "%s_%s_%s" % (tag, tname, lab))
            log("rate_" + tag, {"target_name": tname, "knob": lab, "steps": steps,
                                "search_ms": search_ms})
            log("final_" + tag, {"target_name": tname, "knob": lab, "bytes": sizes, "us": us,
                                 "hyd_peak_heap": codec.peak})
    gc.collect()
    log("done", {"ok": True, "heap_free_end": gc.mem_free()})


try:
    main()
except Exception as exc:
    print("#E", repr(exc))
