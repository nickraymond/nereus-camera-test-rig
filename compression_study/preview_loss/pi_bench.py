"""Camera-side cost of each method on the Pi Zero 2 W (one frame, one encode per method).

    NRJXL_BM_DIR=<bm #120 export> python -m compression_study.preview_loss.pi_bench \
        --dng <imx708 dng> --crop 1504,846,1600,900 --d 5.66 --out pi_bench.json

Times are wall-clock; peak RSS is the largest child (cjxl) and this process.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import time

import cv2
import numpy as np

from compression_study import common
from compression_study.preview_loss import codec as C
from compression_study.preview_loss import fec

PLANES = ("R", "G1", "G2", "B")


def timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, round(time.perf_counter() - t0, 3)


def child_rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dng", required=True)
    ap.add_argument("--crop", required=True)
    ap.add_argument("--d", type=float, default=5.66)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    xywh = [int(v) for v in a.crop.split(",")]
    res = {"host": os.uname().nodename, "d": a.d}
    fr, res["read_and_code_s"] = timed(lambda: C.load_frame(a.dng, xywh))
    p = fr["codes"]
    pl, res["production_4_planes_s"] = timed(lambda: [C.encode(p[n], 4095, a.d) for n in PLANES])
    res["production_bytes"] = sum(len(b) for b in pl)
    res["cjxl_peak_rss_mb_after_production"] = child_rss_mb()
    t = common.tile(p)
    b, res["prog_tiled_s"] = timed(lambda: C.encode(t, 4095, a.d))
    res["prog_bytes"] = len(b)
    tiles = [common.tile({n: p[n][dy::2, dx::2] for n in PLANES}) for dy in (0, 1) for dx in (0, 1)]
    bl, res["mdc_4_desc_s"] = timed(lambda: [C.encode(x, 4095, a.d) for x in tiles])
    res["mdc_bytes"] = sum(len(x) for x in bl)

    def prev():
        R = C.Renderer(fr)
        src = cv2.resize(R.ref, (320, 180), interpolation=cv2.INTER_AREA).astype(np.uint16)
        return C.encode(src, 255, 7.0, modular=False)
    pv, res["preview_render_and_encode_s"] = timed(prev)
    res["preview_bytes"] = len(pv)
    blob = b"".join(pl)
    data = [blob[i:i + C.MSG_B] for i in range(0, len(blob), C.MSG_B)]
    for m in (18, 27):
        par, s = timed(lambda: fec.encode(data, m, C.MSG_B))
        res[f"rs_encode_k{len(data)}_m{m}_s"] = s
        rx = {i: c for i, c in enumerate(data + par) if not (40 <= i < 48 or 100 <= i < 100 + m - 8)}
        out, s = timed(lambda: fec.decode(rx, len(data), m, C.MSG_B))
        res[f"rs_decode_k{len(data)}_m{m}_s"] = s
        res[f"rs_roundtrip_m{m}_ok"] = [o[:len(d)] for o, d in zip(out, data)] == data
    res["cjxl_peak_rss_mb"] = child_rss_mb()
    res["self_peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    print(json.dumps(res, indent=1))
    with open(a.out, "w") as fh:
        json.dump(res, fh, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
