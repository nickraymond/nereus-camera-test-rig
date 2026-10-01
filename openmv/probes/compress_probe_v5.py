"""Raw compression feasibility probe (compression study Phase 2) — OpenMV v5, N6 and AE3.

Run on the Pi (the boards hang off its hub), one board at a time:

    mpremote connect <port> run openmv/probes/compress_probe_v5.py   # stdout = results
    mpremote connect <port> reset                                   # restore the rig service

AE3: reset before AND after, and wait >= 35 s between mpremote sessions (one camera session
per boot on v5). Normally driven by ``compression_study/phase2/pi_run_probe.py``, which adds
host timestamps and samples the UPS power meter.

What it measures, on a live HD Bayer frame (8 bit, BGGR, 1280x800):
1. **Sensor state, read only**: lens-shading registers 0x0820-0x0833 and DENOISE_EN 0x0882
   (PAG7936; the fact-check found both active). No register is written.
2. Capture at a locked exposure (the ``capture_raw`` call pattern; never ``csi.framerate()``).
3. Plane split (viper) and the square-root LUT (8 bit → 8 bit, the study's D curve).
4. Grayscale JPEG per plane with the firmware encoder (``to_jpeg``) at several qualities —
   D-lin (no curve, the better variant on 8-bit OpenMV data in Phase 1) and D (sqrt).
5. The study's lossless packer (MED + adaptive Golomb-Rice, ``methods/packer.c``) ported to
   ``@micropython.viper``; the host checks it is bit-exact and byte-identical to the C one.
6. Today's path for comparison: software demosaic to RGB565 + colour JPEG of the frame.
7. Two ~5 s repeat loops (JPEG planes, packer) bracketed by ``#M`` marks, for energy.

Output lines: ``#R <tag> <json>`` results, ``#B <name> <offset> <crc32> <base64>`` chunks and
``#B <name> end <bytes> <sha256>``, ``#M <mark>`` timing marks, ``#E <msg>`` errors. Nothing
is written to /flash (a 1 MB flash write stalls the N6, capture_service.py).
"""
import binascii
import gc
import hashlib
import json
import time

import csi
import image
import micropython

JPEG_Q = (30, 50, 70, 85, 95)
EMIT_Q = 70
LOOP_MS = 5000
CHUNK = 192  # bytes per console line (256 characters of base64)
COPIES = 3
PACE_MS = 2


def log(tag, obj):
    print("#R", tag, json.dumps(obj))


def sha(buf):
    return binascii.hexlify(hashlib.sha256(buf).digest()).decode()


def emit(name, data):
    """Short base64 lines with a CRC32 each, every chunk sent ``COPIES`` times: the console
    drops ~512-byte blocks out of long lines under sustained output (N6, 2026-10-01: 8 % of
    1 KB lines damaged, unchanged by 5x slower pacing), so the host keeps the first copy whose
    CRC matches."""
    mv = memoryview(data)
    for i in range(0, len(data), CHUNK):
        part = mv[i:i + CHUNK]
        line = "#B %s %d %08x %s" % (name, i, binascii.crc32(part) & 0xFFFFFFFF,
                                     binascii.b2a_base64(part).decode().strip())
        for _ in range(COPIES):
            print(line)
        time.sleep_ms(PACE_MS)
    print("#B", name, "end", len(data), sha(data))
    time.sleep_ms(PACE_MS)


def mem(tag):
    gc.collect()
    log("mem", {"at": tag, "free": gc.mem_free(), "alloc": gc.mem_alloc()})


@micropython.viper
def split_plane(src: ptr8, dst: ptr8, w: int, h: int, dy: int, dx: int):  # noqa: F821
    pw = w >> 1
    ph = h >> 1
    for y in range(ph):
        s = (2 * y + dy) * w + dx
        d = y * pw
        for x in range(pw):
            dst[d + x] = src[s + 2 * x]


@micropython.viper
def apply_lut(src: ptr8, dst: ptr8, lut: ptr8, n: int):  # noqa: F821
    for i in range(n):
        dst[i] = lut[src[i]]


@micropython.viper
def pack_plane(src: ptr8, w: int, h: int, b: int, out: ptr8,  # noqa: F821
               cap: int) -> int:
    """methods/packer.c bitstream: MED predictor, residual mod 2^b → zigzag, adaptive
    Golomb-Rice (A, N reset per row; halve at N = 64), LIMIT-bounded escape, MSB first.
    Returns the byte count, or -1 if ``out`` (``cap`` bytes) would overflow."""
    full = 1 << b
    half = full >> 1
    mask = full - 1
    mb = 8
    if b > 8:
        mb = b
    limit = 2 * (b + mb)
    esc = limit - b - 1
    a0 = (full + 32) >> 6
    if a0 < 2:
        a0 = 2
    acc = 0
    nacc = 0
    pos = 0
    for y in range(h):
        A = a0
        N = 1
        row = y * w
        for x in range(w):
            i = row + x
            if y == 0:
                if x == 0:
                    pred = half
                else:
                    pred = int(src[i - 1])
            elif x == 0:
                pred = int(src[i - w])
            else:
                a = int(src[i - 1])
                bb = int(src[i - w])
                c = int(src[i - w - 1])
                mn = a
                mx = bb
                if bb < a:
                    mn = bb
                    mx = a
                if c >= mx:
                    pred = mn
                elif c <= mn:
                    pred = mx
                else:
                    pred = a + bb - c
            e = (int(src[i]) - pred) & mask
            if e >= half:
                e = e - full
            if e >= 0:
                m = e << 1
                ae = e
            else:
                m = ((0 - e) << 1) - 1
                ae = 0 - e
            k = 0
            while (N << k) < A:
                k += 1
            q = m >> k
            if q < esc:
                nz = q
                tail = m & ((1 << k) - 1)
                tlen = k
            else:
                nz = esc
                tail = m - 1
                tlen = b
            if pos + 8 + ((nz + tlen + 1) >> 3) >= cap:
                return -1
            while nz > 0:
                take = nz
                if take > 16:
                    take = 16
                acc = acc << take
                nacc += take
                nz -= take
                while nacc >= 8:
                    nacc -= 8
                    out[pos] = (acc >> nacc) & 0xFF
                    pos += 1
                acc = acc & ((1 << nacc) - 1)
            acc = (acc << 1) | 1
            nacc += 1
            acc = (acc << tlen) | tail
            nacc += tlen
            while nacc >= 8:
                nacc -= 8
                out[pos] = (acc >> nacc) & 0xFF
                pos += 1
            acc = acc & ((1 << nacc) - 1)
            A += ae
            N += 1
            if N == 64:
                A = A >> 1
                N = N >> 1
    if nacc > 0:
        out[pos] = (acc << (8 - nacc)) & 0xFF
        pos += 1
    return pos


def sqrt_lut8():
    s = 255 / (255 ** 0.5)
    return bytearray(min(255, int((v ** 0.5) * s + 0.5)) for v in range(256))


def main():
    t_all = time.ticks_ms()
    mem("start")
    cam = csi.CSI()
    cam.reset()
    cam.pixformat(csi.BAYER)
    cam.framesize(csi.HD)
    regs = {}
    for addr in list(range(0x0820, 0x0834)) + [0x0882]:
        try:
            regs["0x%04X" % addr] = cam.__read_reg(addr)
        except Exception as exc:
            regs["0x%04X" % addr] = "ERR " + repr(exc)
    log("regs", regs)
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 1500:
        cam.snapshot()
    exp, gain = cam.exposure_us(), cam.gain_db()
    cam.auto_exposure(False, exposure_us=exp)
    cam.auto_gain(False, gain_db=gain)
    cam.auto_whitebal(False)
    for _ in range(3):
        cam.snapshot()
    t1 = time.ticks_us()
    img = cam.snapshot()
    t_snap = time.ticks_diff(time.ticks_us(), t1)
    W, H = img.width(), img.height()
    frame = img.bytearray()
    log("capture", {"w": W, "h": H, "bytes": len(frame), "exposure_us": cam.exposure_us(),
                    "gain_db": cam.gain_db(), "snapshot_us": t_snap, "sha256": sha(frame)})
    mem("captured")

    # 1) plane split (BGGR → R at (1,1), G1 (0,1), G2 (1,0), B (0,0))
    pw, ph = W // 2, H // 2
    offs = (("R", 1, 1), ("G1", 0, 1), ("G2", 1, 0), ("B", 0, 0))
    planes = {}
    t1 = time.ticks_us()
    for name, dy, dx in offs:
        p = image.Image(pw, ph, image.GRAYSCALE)
        split_plane(frame, p.bytearray(), W, H, dy, dx)
        planes[name] = p
    t_split = time.ticks_diff(time.ticks_us(), t1)
    log("planes", {"split_us": t_split, "w": pw, "h": ph,
                   "sha256": {k: sha(v.bytearray()) for k, v in planes.items()}})
    mem("planes")

    # 2) sqrt LUT
    lut = sqrt_lut8()
    sq = {}
    t1 = time.ticks_us()
    for name in planes:
        p = image.Image(pw, ph, image.GRAYSCALE)
        apply_lut(planes[name].bytearray(), p.bytearray(), lut, pw * ph)
        sq[name] = p
    log("lut", {"us": time.ticks_diff(time.ticks_us(), t1)})
    mem("lut")

    # 3) grayscale JPEG per plane
    sweep = []
    for tag, src in (("D-lin", planes), ("D", sq)):
        for q in JPEG_Q:
            sizes, us = {}, {}
            for name in ("R", "G1", "G2", "B"):
                t1 = time.ticks_us()
                j = src[name].to_jpeg(quality=q, copy=True)
                us[name] = time.ticks_diff(time.ticks_us(), t1)
                data = j.bytearray()
                sizes[name] = len(data)
                if q == EMIT_Q:
                    emit("%s_q%d_%s" % (tag, q, name), data)
                del j
            sweep.append({"method": tag, "q": q, "bytes": sizes, "us": us,
                          "total_bytes": sum(sizes.values()), "total_us": sum(us.values())})
            gc.collect()
    log("jpeg_planes", sweep)
    mem("jpeg")

    # 4) lossless packer (viper) on the linear planes = method C
    cap = pw * ph + 4096
    out = bytearray(cap)
    packed = {}
    for name in ("R", "G1", "G2", "B"):
        t1 = time.ticks_us()
        n = pack_plane(planes[name].bytearray(), pw, ph, 8, out, cap)
        dt = time.ticks_diff(time.ticks_us(), t1)
        if n < 0:
            print("#E packer overflow on", name)
            continue
        packed[name] = {"bytes": n, "us": dt}
        emit("C_" + name, memoryview(out)[:n])
    log("packer", {"planes": packed, "out_buffer_bytes": cap,
                   "row_state_bytes": 2 * pw * 2 + 32})
    mem("packer")

    # 5) today's path, on-device: demosaic to RGB565 + colour JPEG
    try:
        t1 = time.ticks_us()
        rgb = img.to_rgb565(copy=True)
        t_dem = time.ticks_diff(time.ticks_us(), t1)
        res = []
        for q in JPEG_Q:
            t1 = time.ticks_us()
            j = rgb.to_jpeg(quality=q, copy=True)
            res.append({"q": q, "bytes": len(j.bytearray()),
                        "us": time.ticks_diff(time.ticks_us(), t1)})
            if q == EMIT_Q:
                emit("M1_q%d" % q, j.bytearray())
            del j
        log("rgb_jpeg", {"demosaic_us": t_dem, "sweep": res})
        del rgb
    except Exception as exc:
        print("#E rgb_jpeg", repr(exc))
    mem("rgb")

    # 6) energy windows: repeat each encoder for ~LOOP_MS
    for tag in ("idle", "jpeg_planes", "packer"):
        n = 0
        print("#M start", tag)
        t1 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t1) < LOOP_MS:
            if tag == "idle":
                time.sleep_ms(50)
            elif tag == "jpeg_planes":
                for name in ("R", "G1", "G2", "B"):
                    planes[name].to_jpeg(quality=EMIT_Q, copy=True)
                gc.collect()
            else:
                for name in ("R", "G1", "G2", "B"):
                    pack_plane(planes[name].bytearray(), pw, ph, 8, out, cap)
            n += 1
        print("#M stop", tag, n, time.ticks_diff(time.ticks_ms(), t1))
    log("done", {"total_ms": time.ticks_diff(time.ticks_ms(), t_all)})


try:
    main()
except Exception as exc:
    print("#E", repr(exc))
