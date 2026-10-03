"""Step C prototype: "give the camera an ROI + a message cap, it picks the best quality".

    python3 byte_target.py --dng S.dng --metadata S.json --crop X,Y,W,H --cap 180 \
        [--effort 5] [--d-max 10.4] [--out result.json]

Runs ON the Pi next to bm #120's ``rc_raw_jxl.py`` (imports it; stdlib + numpy only) and
replaces the fixed distance rungs with a byte-target search of at most 3 encodes:

1. prior: bytes ≈ B_REF · (area / A_REF) · (d / D_REF)^-K (fitted on the 2026-10-03 sweep:
   1600×900 at d 3.8 ≈ 51 kB, K ≈ 0.8) → the distance that lands at AIM × the cap's bytes;
2. one-point correction with the prior slope K (log bytes vs log d), aimed at AIM again;
3. secant through the two measured points (log–log), aimed at AIM again.
Stop as soon as a result fits the cap and fills ≥ FILL_OK of it; otherwise keep the best
fitting result. Quality floor: a planned distance above ``--d-max`` (default 10.4, where the
card-area SSIM vs RAW meets today's pjpg on the 2026-10-03 frames) means the ROI is too large
for the cap: stop and report ``fallback`` (the caller sends pjpg or a smaller ROI). Production
distance range 0.1–15. Each encode is ``rc_raw_jxl.encode_rung`` under ``run_capped`` (the
250 MiB guard), sealed with the production container, so bytes are exactly what would be sent.
"""

import argparse
import json
import math
import os
import resource
import shutil
import sys
import tempfile
import time

import rc_raw_jxl as rc

B_REF, A_REF, D_REF, K = 51_000.0, 1600 * 900, 3.8, 0.8
AIM, FILL_OK, MSG_B, CHUNK = 0.97, 0.93, 288, 384
D_LO, D_HI = 0.1, 15.0


def clamp(d):
    return min(max(d, D_LO), D_HI)


def encode(crop, codes, colour, d, effort, xywh, work):
    payloads, runs = rc.encode_rung(codes, d, effort, work, cjxl=shutil.which("cjxl"),
                                    runner=rc.run_capped, timeout_s=120)
    h, w = crop["mosaic"].shape
    params = rc.build_params(crop_xywh=xywh, native_wh=(4608, 2592), crc=0, colour=colour,
                             distance=d, effort=effort)
    blob, _ = rc.seal_container(w=w, h=h, cfa=crop["cfa"], black=crop["black"],
                                white=crop["white"], params=params, payloads=payloads)
    return len(blob), max(r.get("peak_rss_kb", 0) for r in runs)


def search(crop, codes, colour, xywh, cap, effort, d_max, work):
    target = cap * MSG_B
    aim = AIM * target
    area = xywh[2] * xywh[3]
    tries = []
    d = clamp(D_REF * (aim / (B_REF * area / A_REF)) ** (-1 / K))
    for step in range(3):
        if d > d_max:
            return {"status": "fallback", "reason": f"needs d {d:.2f} > d_max {d_max}",
                    "tries": tries}
        t0 = time.monotonic()
        n, rss = encode(crop, codes, colour, round(d, 3), effort, xywh, work)
        tries.append({"d": round(d, 3), "bytes": n, "msgs": rc.message_count(n, CHUNK),
                      "s": round(time.monotonic() - t0, 2), "cjxl_rss_kb": rss})
        fit = [t for t in tries if t["bytes"] <= target]
        if fit and max(t["bytes"] for t in fit) >= FILL_OK * target:
            break
        if step == 0:   # one point: the prior slope
            d = clamp(d * (n / aim) ** (1 / K))
        else:           # secant in log–log through the last two points
            (d1, n1), (d2, n2) = [(t["d"], t["bytes"]) for t in tries[-2:]]
            if n1 == n2 or d1 == d2:
                d = clamp(d2 * (n2 / aim) ** (1 / K))
            else:
                k = -math.log(n2 / n1) / math.log(d2 / d1)
                k = min(max(k, 0.3), 2.0)
                d = clamp(d2 * (n2 / aim) ** (1 / k))
    fit = [t for t in tries if t["bytes"] <= target]
    if not fit:
        return {"status": "no_fit", "tries": tries}
    best = max(fit, key=lambda t: t["bytes"])
    return {"status": "ok", "best": best, "fill": round(best["bytes"] / target, 4),
            "tries": tries}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dng", required=True)
    ap.add_argument("--metadata", required=True)
    ap.add_argument("--crop", required=True)
    ap.add_argument("--cap", type=int, required=True)
    ap.add_argument("--effort", type=int, default=5)
    ap.add_argument("--d-max", type=float, default=10.4)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    xywh = tuple(int(v) for v in a.crop.split(","))
    meta = json.load(open(a.metadata))
    t0 = time.monotonic()
    crop = rc.read_dng_crop(a.dng, xywh)
    codes = rc.code_planes(crop)
    t_read = time.monotonic() - t0
    with tempfile.TemporaryDirectory(prefix="bt_") as work:
        r = search(crop, codes, rc.colour_params(meta), xywh, a.cap, a.effort, a.d_max, work)
    r.update({"crop": list(xywh), "cap": a.cap, "effort": a.effort, "d_max": a.d_max,
              "encodes": len(r["tries"]), "read_s": round(t_read, 2),
              "total_s": round(time.monotonic() - t0, 2),
              "py_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)})
    print(json.dumps(r))
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(r, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
