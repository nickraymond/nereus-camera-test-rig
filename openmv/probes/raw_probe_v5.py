"""OpenMV v5 RAW (Bayer) probe — run with ``mpremote connect <port> run openmv/probes/raw_probe_v5.py``.

Read-only facts for S3 / OQ-21: the ``csi`` constants and CSI methods, whether auto
exposure / gain / white balance lock, read-back exposure and gain, one HD Bayer frame's size,
bytes per pixel and per-2x2-position means, free heap and flash. Saves the frame to
``/flash/raw_probe.bin`` — delete it afterwards. Retrieve it with the rig's ``get_file``,
not ``mpremote fs cp`` (1 MB took > 90 s over mpremote on the N6, 2026-09-28).

Cautions: ``mpremote`` interrupts the rig service and leaves it stopped — ``mpremote ... reset``
after. On the AE3 this is a camera session: reset the board before AND after (one camera session
per boot on v5, PR #70), and wait >= 35 s between mpremote sessions.
"""
# OpenMV v5 RAW probe (read-only; run with mpremote). Prints facts, saves one raw frame to /flash.
import csi, gc, time, os
print("#C", [c for c in dir(csi) if c.isupper()])
c = csi.CSI()
print("#M", [m for m in dir(c) if not m.startswith("_")])
c.reset()
c.pixformat(csi.BAYER)
c.framesize(csi.HD)
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 1500:
    c.snapshot()
for fn in ("auto_exposure", "auto_gain", "auto_whitebal"):
    try:
        getattr(c, fn)(False)
        print("#L", fn, "locked")
    except Exception as e:
        print("#W", fn, repr(e))
for fn in ("exposure_us", "gain_db", "rgb_gain_db", "black_level"):
    try:
        print("#R", fn, getattr(c, fn)())
    except Exception as e:
        print("#W", fn, repr(e))
img = c.snapshot()
b = img.bytearray()
W, H = img.width(), img.height()
print("#G", W, H, len(b), "bytes/px", len(b) / (W * H), "format", img.format())
mn, mx = 255, 0
sums = [0, 0, 0, 0]; n = 0
for y in range(0, H - 1, 8):
    row = y * W
    for x in range(0, W - 1, 8):
        for k, (dy, dx) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1))):
            v = b[row + dy * W + x + dx]
            sums[k] += v
            mn = min(mn, v); mx = max(mx, v)
        n += 1
print("#S min", mn, "max", mx, "means TL TR BL BR", [round(s / n, 1) for s in sums])
with open("/flash/raw_probe.bin", "wb") as f:
    f.write(b)
gc.collect()
print("#H free", gc.mem_free(), "flash", os.statvfs("/flash")[0] * os.statvfs("/flash")[3])
print("#D done")
