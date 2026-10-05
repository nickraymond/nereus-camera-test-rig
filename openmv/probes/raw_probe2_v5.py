"""OpenMV v5 RAW probe 2: locked frame, WB-gain effect on Bayer, dark frame (OQ-21).

Run with ``mpremote connect <port> run openmv/probes/raw_probe2_v5.py``, then
``mpremote connect <port> reset`` (mpremote leaves the rig service stopped). On the AE3:
reset before AND after, and wait >= 35 s between mpremote sessions (one camera session per
boot on v5, PR #70).

1. HD Bayer, autos on for 2 s, then lock exposure / gain / WB at the metered values
   (the call pattern from Nick's ``s28_board_burst.py``; 3 flush frames after every change,
   because a changed exposure can leave two stale frames buffered). Saves ``/flash/raw_a.bin``.
2. Same exposure with ``rgb_gain_db`` forced to (0, 0, 0): if the 2x2 means move, the ISP WB
   gains are baked into the "raw" Bayer data.
3. Shortest exposure, 0 dB gain: dark frame for the black level. Saves ``/flash/raw_d.bin``.

Retrieve the .bin files with the rig's ``get_file`` (``mpremote fs cp`` of 1 MB took > 90 s).
Never calls ``csi.framerate()`` (wedges the board, per Nick's workbench notes).
"""
import gc
import os
import time

import csi


def settle(c, ms):
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < ms:
        c.snapshot()


def flush(c):
    for _ in range(3):
        c.snapshot()


def readback(c, tag):
    out = {}
    for fn in ("exposure_us", "gain_db", "rgb_gain_db"):
        try:
            out[fn] = getattr(c, fn)()
        except Exception as e:
            out[fn] = "ERR " + repr(e)
    print("#R", tag, out)


def stats(img, tag):
    """Per-2x2-position means + min/max on every 4th 2x2 cell, and a low-value histogram."""
    b = img.bytearray()
    W, H = img.width(), img.height()
    sums = [0, 0, 0, 0]
    mn = [255] * 4
    mx = [0] * 4
    low = [0] * 32
    n = 0
    for y in range(0, H - 1, 8):
        row = y * W
        for x in range(0, W - 1, 8):
            for k, off in enumerate((0, 1, W, W + 1)):
                v = b[row + x + off]
                sums[k] += v
                if v < mn[k]:
                    mn[k] = v
                if v > mx[k]:
                    mx[k] = v
                if v < 32:
                    low[v] += 1
            n += 1
    print("#S", tag, W, H, len(b), "means TL TR BL BR", [round(s / n, 2) for s in sums],
          "min", mn, "max", mx)
    print("#Q", tag, "hist<32", low)
    return b


def save(b, path):
    with open(path, "wb") as f:
        f.write(b)
    print("#F", path, os.stat(path)[6])


for p in ("/flash/raw_probe.bin", "/flash/raw_a.bin", "/flash/raw_d.bin"):
    try:
        os.remove(p)
    except OSError:
        pass

c = csi.CSI()
c.reset()
c.pixformat(csi.BAYER)
c.framesize(csi.HD)
settle(c, 2000)
readback(c, "auto")
for fn in ("auto_blc", "blc_regs"):
    try:
        print("#B", fn, getattr(c, fn)())
    except Exception as e:
        print("#W", fn, repr(e))

# 1. Lock at the metered values.
e = c.exposure_us()
g = c.gain_db()
rgb = c.rgb_gain_db()
c.auto_exposure(False, exposure_us=e)
c.auto_gain(False, gain_db=g)
c.auto_whitebal(False)
flush(c)
readback(c, "locked")
save(stats(c.snapshot(), "A_locked"), "/flash/raw_a.bin")
gc.collect()

# 2. Same exposure, WB gains forced neutral.
try:
    c.auto_whitebal(False, rgb_gain_db=(0.0, 0.0, 0.0))
    flush(c)
    readback(c, "wb0")
    stats(c.snapshot(), "B_wb0")
except Exception as e2:
    print("#W wb0", repr(e2))
try:
    c.auto_whitebal(False, rgb_gain_db=rgb)
    flush(c)
    readback(c, "wb_restored")
    stats(c.snapshot(), "C_wb_restored")
except Exception as e3:
    print("#W wb_restore", repr(e3))
gc.collect()

# 3. Dark frame: shortest exposure, 0 dB.
c.auto_gain(False, gain_db=0.0)
c.auto_exposure(False, exposure_us=1)
flush(c)
readback(c, "dark")
save(stats(c.snapshot(), "D_dark"), "/flash/raw_d.bin")
gc.collect()
s = os.statvfs("/flash")
print("#H free", gc.mem_free(), "flash", s[0] * s[3])
print("#D done")
