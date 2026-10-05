"""Is truly raw Bayer reachable on the PAG7936? Denoise and lens-shading off (OQ-54).

Approved by Nick 2026-10-01 ("approved, keep going" on the register-write request). Writes ONLY:
- DENOISE_EN 0x0882 = 0x00 (the upstream driver's own QVGA value; HD default 0x03),
- the six lens-shading gain coefficients 0x0820-0x0825 = 0 (R/G/B linear + quadratic gains;
  the driver has no LSC enable bit, so "off" = zero gain is an ASSUMPTION this probe tests),
- SENSOR_UPDATE 0x00EB = 0x80 to commit (as ``capture_service._set_frame_time``).
Everything is restored by ``mpremote reset`` (``machine.reset`` re-runs the sensor init); the
runner reads the registers back after the reset.

Run: ``mpremote connect <port> run openmv/probes/raw_isp_probe_v5.py`` then ``mpremote ... reset``
(AE3: reset before and after, 35 s between sessions). Driven by
``compression_study/phase2/pi_run_probe.py --probe openmv/probes/raw_isp_probe_v5.py``.

Per condition (default → denoise off → denoise + LSC off), at one locked exposure, two frame
pairs; on-board viper statistics on the G sites of the G/B rows (only small JSON crosses the
console, which drops data under load — OQ-55):
- temporal noise vs radius: var(f1 − f2) / 2 and mean (f1 + f2) / 2 in 8 radial bins
  (lens shading is a radial digital gain: it lifts the noise toward the corners);
- same-colour neighbour correlation of the temporal noise (x + 2): ~0 for raw shot / read
  noise, clearly positive when a spatial denoise filter runs.
Output: ``#R <tag> <json>``, ``#E <msg>``.
"""
import gc
import json
import time

import csi
import micropython

NB = 8
LSC_GAINS = (0x0820, 0x0821, 0x0822, 0x0823, 0x0824, 0x0825)
DENOISE_EN = 0x0882
SENSOR_UPDATE, SU_FLAG = 0x00EB, 0x80
WATCH = (0x0882, 0x0820, 0x0821, 0x0822, 0x0823, 0x0824, 0x0825, 0x0826, 0x0829, 0x082E)


def log(tag, obj):
    print("#R", tag, json.dumps(obj))


@micropython.viper
def stats(f1: ptr8, f2: ptr8, w: int, h: int, nb: int, acc: ptr32):  # noqa: F821
    """acc[3b..3b+2] = n, sum(f1+f2), sum((f1-f2)^2) per radial bin b; acc[3nb] = sum d*d_right,
    acc[3nb+1] = sum d^2 over the same pairs. G sites of rows y even, x odd (BGGR)."""
    cx = w >> 1
    cy = h >> 1
    rmax2 = cx * cx + cy * cy
    y = 0
    while y < h:
        x = 1
        row = y * w
        dy2 = (y - cy) * (y - cy)
        while x < w - 2:
            i = row + x
            a1 = int(f1[i])
            a2 = int(f2[i])
            d = a1 - a2
            r2 = (x - cx) * (x - cx) + dy2
            b = (r2 * nb) // (rmax2 + 1)
            acc[3 * b] += 1
            acc[3 * b + 1] += a1 + a2
            acc[3 * b + 2] += d * d
            dn = int(f1[i + 2]) - int(f2[i + 2])
            acc[3 * nb] += d * dn
            acc[3 * nb + 1] += d * d
            x += 2
        y += 2


def regs(cam):
    out = {}
    for a in WATCH:
        try:
            out["0x%04X" % a] = cam.__read_reg(a)
        except Exception as exc:
            out["0x%04X" % a] = "ERR " + repr(exc)
    return out


def measure(cam, tag, pairs=2):
    for _ in range(3):  # flush frames buffered before a register change
        cam.snapshot()
    acc = bytearray(4 * (3 * NB + 2))
    means = []
    for _ in range(pairs):
        a = bytearray(cam.snapshot().bytearray())
        img = cam.snapshot()
        w, h = img.width(), img.height()
        stats(a, img.bytearray(), w, h, NB, acc)
        means.append(sum(a[i] for i in range(0, len(a), 997)) / (len(a) // 997 + 1))
        del a
        gc.collect()
    v = []
    for i in range(3 * NB + 2):
        x = int.from_bytes(acc[4 * i:4 * i + 4], "little")
        v.append(x - (1 << 32) if (i == 3 * NB and x >= (1 << 31)) else x)  # signed sum
    bins = []
    for k in range(NB):
        n, s, d2 = v[3 * k], v[3 * k + 1], v[3 * k + 2]
        if n:
            bins.append({"bin": k, "n": n, "mean": s / (2 * n), "var": d2 / (2 * n)})
    corr = v[3 * NB] / v[3 * NB + 1] if v[3 * NB + 1] else None
    log("cond", {"tag": tag, "regs": regs(cam), "exposure_us": cam.exposure_us(),
                 "gain_db": cam.gain_db(), "frame_mean": sum(means) / len(means),
                 "radial": bins, "neighbour_corr": corr})


def main():
    cam = csi.CSI()
    cam.reset()
    cam.pixformat(csi.BAYER)
    cam.framesize(csi.HD)
    log("defaults", regs(cam))
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 1500:
        cam.snapshot()
    cam.auto_exposure(False, exposure_us=cam.exposure_us())
    cam.auto_gain(False, gain_db=cam.gain_db())
    cam.auto_whitebal(False)
    measure(cam, "default")
    cam.__write_reg(DENOISE_EN, 0x00)
    cam.__write_reg(SENSOR_UPDATE, SU_FLAG)
    measure(cam, "denoise_off")
    for a in LSC_GAINS:
        cam.__write_reg(a, 0x00)
    cam.__write_reg(SENSOR_UPDATE, SU_FLAG)
    measure(cam, "denoise_off_lsc0")
    log("done", {"ok": True})


try:
    main()
except Exception as exc:
    print("#E", repr(exc))
