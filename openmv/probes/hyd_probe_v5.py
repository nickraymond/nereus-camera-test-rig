"""hydrium (``nrhyd.mpy``) vs wl53 (``nrwl53.mpy``) on one live HD Bayer frame (compression study,
hydrium bench, 2026-10-01). Sensor defaults (denoise + lens shading ON — Nick's call): nothing
is written to the sensor.

Needs ``/flash/nrhyd.mpy``, ``/flash/nrhydm.mpy``, ``/flash/nrwl53.mpy`` and
``/flash/nrpack.mpy`` (copied by the runner, removed after).
1. Capture, copy the frame (the camera keeps refilling its buffers) — or, if
   ``/flash/bench.bayer`` exists (a stored 1280×800 8-bit mosaic), use that, so both boards are
   timed on the same scene.
2. Lossless reference: the 4 planes through ``nrpack`` — the host rebuilds the exact planes
   from these and re-encodes them with its own encoders to check byte-identity.
3. For each codec: fixed knobs (timing, heap), then an on-board rate search (bisection on the
   log knob) for a frame total of 0.4 bpp (51,200 B) and 0.8 bpp (102,400 B); the final
   streams are sent back.
4. hydrium's peak heap at the two final knobs, with ``nrhydm`` (same code + a size header per
   block, which the GC cannot trace — so with the GC disabled; same bytes as ``nrhyd``). It
   excludes the 768 KB tile buffer ``work``, which the probe allocates first (AE3 heap).
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
BENCH_FILE = "/flash/bench.bayer"  # used instead of the camera when present
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
    for _ in range(COPIES):
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
    def __init__(self, name, mod, frame, W, lut=None, work=None):
        self.name, self.mod, self.frame, self.W, self.lut = name, mod, frame, W, lut
        self.work = work

    def label(self, k):
        if self.name == "H":
            return "%d_%d" % hyd_params(k)
        return "%d" % int(k * 16)

    def plane(self, dy, dx, pw, ph, k, out):
        off = dy * self.W + dx
        if self.name == "H":
            hf, gs = hyd_params(k)
            n, where = self.mod.encode(self.frame, 2 * self.W, off, pw, ph, 0, 255, hf, gs, LF,
                                       out, self.work)
            if not n:
                raise ValueError("nrhyd failed: where=%d hf=%d gs=%d plane=%d,%d free=%d" % (
                    where, hf, gs, dy, dx, gc.mem_free()))
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


FINALS = []  # (target, knob label) of hydrium's rate searches


def run_codec(tag, codec, fixed_k, search, pw, ph, out, heap0, heap1):
    """Fixed knobs (timing), then a rate search per target; final streams are sent back."""
    lo, hi, d = search
    for k in fixed_k:
        gc.collect()
        free0 = gc.mem_free()
        sizes, us = encode_frame(codec, pw, ph, k, out)
        log("fixed_" + tag, {"knob": codec.label(k), "bytes": sizes, "us": us,
                             "heap_free_before": free0})
    log("heap_" + tag, {"heap_free_before": heap0, "heap_free_with_buffers": heap1})
    for tname, target in TARGETS:
        k, steps, search_ms = rate_search(codec, pw, ph, out, target, lo, hi, d)
        lab = codec.label(k)
        if tag == "H":
            FINALS.append((tname, lab))
        sizes, us = encode_frame(codec, pw, ph, k, out, "%s_%s_%s" % (tag, tname, lab))
        log("rate_" + tag, {"target_name": tname, "knob": lab, "steps": steps,
                            "search_ms": search_ms})
        log("final_" + tag, {"target_name": tname, "knob": lab, "bytes": sizes, "us": us})


def capture():
    """One live HD Bayer frame at locked exposure, copied (the camera keeps refilling)."""
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
    return frame, W, H, (cam.exposure_us(), cam.gain_db())


def main():
    if "/flash" not in sys.path:
        sys.path.append("/flash")
    import nrhyd
    import nrpack
    import nrwl53
    work = bytearray(786432)  # hydrium's tile buffer, first: the AE3 heap fragments later
    try:  # a stored HD Bayer frame (e.g. an S4 dataset frame), for timings on the same scene
        with open(BENCH_FILE, "rb") as f:
            frame = bytearray(1280 * 800)  # readinto: one 1 MB block, not two (AE3)
            f.readinto(frame)
        W, H, src, exp = 1280, 800, BENCH_FILE, (None, None)
    except OSError:
        frame, W, H, exp = capture()
        src = "camera"
    pw, ph = W // 2, H // 2
    mean = sum(frame[i] for i in range(0, len(frame), 1009)) / (len(frame) // 1009 + 1)
    log("frame", {"w": W, "h": H, "mean_dn": mean, "sha256": sha(frame), "source": src,
                  "exposure_us": exp[0], "gain_db": exp[1]})
    gc.collect()
    heap0 = gc.mem_free()
    out = bytearray(pw * ph + 8192)
    for name, dy, dx in OFFS:  # lossless reference planes
        n = nrpack.encode(frame, 2 * W, dy * W + dx, pw, ph, 8, out)
        emit("C_" + name, memoryview(out)[:n])
    gc.collect()
    heap1 = gc.mem_free()
    try:  # optional: which codecs to run, e.g. "W" (the AE3 heap cannot always hold both)
        with open("/flash/codecs.txt") as f:
            codecs = f.read().strip()
    except OSError:
        codecs = "HW"
    if "H" not in codecs:
        del work
        gc.collect()
    # hydrium knob range 0.05..6 (0.8 bpp is k ~ 2.6-3.5): above ~6 (2+ bpp) its symbol buffers
    # outgrow the AE3's fragmented ~2 MB heap
    if "H" in codecs:
        run_codec("H", Codec("H", nrhyd, frame, W, work=work), (0.3, 1.0, 3.0),
                  (0.05, 6.0, +1), pw, ph, out, heap0, heap1)
    import nrhydm
    for tname, lab in FINALS:
        hf, gs = (int(x) for x in lab.split("_"))
        gc.collect()
        gc.disable()
        try:
            free0 = gc.mem_free()
            n, peak = nrhydm.encode(frame, 2 * W, W + 1, pw, ph, 0, 255, hf, gs, LF, out, work)
            log("peak_H", {"target_name": tname, "knob": lab, "bytes_R": n, "peak_heap": peak,
                           "heap_free_before": free0})
        finally:
            gc.enable()
    if "H" in codecs:
        del work  # wl53 needs a 512 KB block of its own (AE3)
        gc.collect()
    if "W" in codecs:
        run_codec("W", Codec("W", nrwl53, frame, W, sqrt12_lut()), (8.0, 20.0, 60.0),
                  (0.5, 4000.0, -1), pw, ph, out, heap0, heap1)
    gc.collect()
    log("done", {"ok": True, "heap_free_end": gc.mem_free()})


try:
    main()
except Exception as exc:
    print("#E", repr(exc))
