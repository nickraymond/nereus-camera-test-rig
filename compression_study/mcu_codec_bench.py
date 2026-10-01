"""MCU codec desk study: which D2-class lossy plane encoder could run on the OpenMV N6 / AE3?

    python -m compression_study.mcu_codec_bench --data <primary>/data/s4_20260930 \
        --scratch <dir with libjxl-tiny/build and wl53/> [--fsets n6_cool_air,...] [--methods ...]

Scores each candidate exactly like ``run_study`` (same frames, ROIs, noise model, rate targets
T1 = 0.4 and T2 = 0.8 bpp over the full Bayer frame incl. all four planes + a 24-byte
header, ``metrics.evaluate``) and writes ``compression_study/work/mcu_codec_study.json``.

Candidates (all per Bayer plane, R G1 G2 B):
- ``tiny-srgb``: libjxl-tiny (VarDCT-only, fixed settings, input = linear-sRGB float PFM).
  sqrt-12 codes are fed as sRGB-encoded grey (R = G = B), which is what cjxl does with the
  D2 PGM; decoded with djxl to PFM and mapped back.
- ``tiny-lin``: libjxl-tiny on the linear plane (no sqrt LUT; XYB's cube root is the curve).
- ``jxl-<mode>-e<N>``: full libjxl (cjxl) on sqrt-12 planes at low effort (D2 at effort N).
- ``jls-sqrt8`` / ``jls-lin8``: JPEG-LS near-lossless (CharLS via imagecodecs), knob NEAR.
- ``j2k-sqrt12``: JPEG 2000 (OpenJPEG 9/7, one quality layer) — a reference wavelet coder.
- ``hyd-*``: hydrium (BSD-2 C streaming JPEG XL VarDCT encoder), built from source with three
  study patches (HF multiplier and globalScale as a rate knob; ``-lf4`` = LF step / 4 with
  rounding); float linear-light input. ``-lin`` = the linear plane (no LUT) as input.
- ``wl53`` / ``wl53-flat`` / ``wl53-lin8`` / ``wl53-norun``: prototype C codec (integer 5/3 DWT
  + dead-zone quantizer + adaptive Golomb-Rice with a JPEG-LS run mode; ``-norun`` = no run
  mode, packer-like), on sqrt-12 planes (``-lin8``: native 8-bit plane).

Scratch builds expected under ``--scratch``: ``libjxl-tiny/build/encoder/cjxl_tiny``,
``hydrium/hyd`` (patched source + ``drv.c``), ``wl53/wl53`` (``wl53.c``). None is shipped.

Study-only tooling (not shipped, not imported from ``src/``).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from compression_study import run_study as rs  # sets sys.path for src/ and .study-pylib

import numpy as np  # noqa: E402

from compression_study import rate, rois, sim  # noqa: E402
from compression_study.common import (RunStats, inverse, merge, read_pnm, run, split,  # noqa: E402
                                      srgb_eotf, srgb_oetf, tool, write_pgm)
from compression_study.methods import plane_codecs as pc  # noqa: E402

STUDY = rs.STUDY
HDR = 24  # bytes, as in run_study's rate search
OUT = STUDY / "work" / "mcu_codec_study.json"
TARGETS = {"T1": 0.4, "T2": 0.8}
KEYS = ("bpp", "red_err_noise", "stress_de_mean", "block_de_med")


# ------------------------------------------------------------------ PFM

def write_pfm3(path: Path, grey: np.ndarray | None = None, rgb: np.ndarray | None = None):
    img = np.repeat(grey[..., None], 3, axis=2) if rgb is None else rgb
    h, w, _ = img.shape
    data = np.ascontiguousarray(img[::-1].astype("<f4"))  # PFM rows run bottom-up
    Path(path).write_bytes(f"PF\n{w} {h}\n-1.0\n".encode() + data.tobytes())


def read_pfm(path: Path) -> np.ndarray:
    data = Path(path).read_bytes()
    parts, pos = [], 0
    while len(parts) < 4:
        end = data.index(b"\n", pos) if len(parts) != 1 else None
        if len(parts) == 1:  # "w h" may share one line
            end = data.index(b"\n", pos)
            parts += data[pos:end].split()
            pos = end + 1
            continue
        parts.append(data[pos:end])
        pos = end + 1
    kind, w, h, scale = parts[0], int(parts[1]), int(parts[2]), float(parts[3])
    ch = 3 if kind == b"PF" else 1
    dt = "<f4" if scale < 0 else ">f4"
    arr = np.frombuffer(data, dtype=dt, count=w * h * ch, offset=pos).reshape(h, w, ch)
    return arr[::-1].astype(np.float64)


# ------------------------------------------------------------------ methods

@dataclass
class Method:
    name: str
    fwd: Callable  # (plane uint16, raw) -> codes
    inv: Callable  # (decoded, raw) -> float plane in sensor counts
    enc: Callable  # (codes, knob) -> (bytes, RunStats)
    dec: Callable  # (bytes, shape) -> decoded
    knob: tuple  # (direction, lo, hi, integer, start)
    runs_on: str = ""


def _sqrt(b):
    return (lambda p, r: rs.rp.forward(p, "sqrt", r.black, r.white, b),
            lambda c, r: inverse(np.clip(c, 0, 2 ** b - 1), "sqrt", r.black, r.white, b))


def _lin8():
    return (lambda p, r: rs.rp.forward(p, "linear", r.black, r.white, 8),
            lambda c, r: inverse(np.clip(c, 0, 255), "linear", r.black, r.white, 8))


class Tiny:
    def __init__(self, scratch: Path):
        self.bin = scratch / "libjxl-tiny" / "build" / "encoder" / "cjxl_tiny"
        if not self.bin.exists():
            raise SystemExit(f"cjxl_tiny not built at {self.bin}")

    def enc_values(self, values: np.ndarray, d: float, measure_rss=False):
        with tempfile.TemporaryDirectory(prefix="tiny_") as t:
            src, dst = Path(t) / "in.pfm", Path(t) / "out.jxl"
            write_pfm3(src, values.astype(np.float32))
            _, st = run([str(self.bin), str(src), str(dst), "-d", f"{d:.4f}"],
                        timeout=900, measure_rss=measure_rss)
            return dst.read_bytes(), st

    @staticmethod
    def dec_values(data: bytes, shape) -> np.ndarray:
        with tempfile.TemporaryDirectory(prefix="tinyd_") as t:
            src, dst = Path(t) / "in.jxl", Path(t) / "out.pfm"
            src.write_bytes(data)
            run([tool("djxl"), str(src), str(dst), "--num_threads=0"], timeout=900)
            img = read_pfm(dst)
            return img.mean(axis=2)


def methods(scratch: Path) -> dict[str, Method]:
    import imagecodecs

    out: dict[str, Method] = {}
    tiny = Tiny(scratch)
    s12, s8, l8 = _sqrt(12), _sqrt(8), _lin8()
    # libjxl-tiny, sqrt-12 codes as sRGB-encoded grey (the D2 / cjxl PGM convention)
    out["tiny-srgb"] = Method(
        "tiny-srgb", s12[0],
        lambda v, r: s12[1](np.floor(srgb_oetf(v) * 4095 + 0.5), r),
        lambda c, d: tiny.enc_values(srgb_eotf(c / 4095.0), d),
        lambda b, shape: tiny.dec_values(b, shape),
        (-1, 0.05, 25.0, False, 2.0), "C++ (libjxl-tiny)")
    # libjxl-tiny on linear values (no LUT): XYB's own transfer is the perceptual curve
    out["tiny-lin"] = Method(
        "tiny-lin", lambda p, r: (p.astype(np.float64) - r.black) / (r.white - r.black),
        lambda v, r: np.clip(v, 0, 1) * (r.white - r.black) + r.black,
        lambda c, d: tiny.enc_values(c, d),
        lambda b, shape: tiny.dec_values(b, shape),
        (-1, 0.05, 25.0, False, 2.0), "C++ (libjxl-tiny)")
    # full libjxl at low effort (and e7 as a reproduction check of the D2 rows)
    for mode in ("modular", "vardct"):
        for e in (1, 2, 3, 7):
            out[f"jxl-{mode}-e{e}"] = Method(
                f"jxl-{mode}-e{e}", s12[0], lambda c, r: s12[1](c.astype(np.float64), r),
                lambda c, d, m=mode, e=e: pc.jxl_enc(c, 4095, d, m, e),
                lambda b, shape: pc.jxl_dec(b), (-1, 0.05, 25.0, False, 2.0), "C++ (libjxl)")

    def jls_enc(c, near):
        t = time.perf_counter()
        a = c.astype(np.uint8)
        try:
            b = imagecodecs.jpegls_encode(a, level=int(near))
        except Exception:  # CharLS output buffer sized for compressible data
            b = imagecodecs.jpegls_encode(a.astype(np.uint16), level=int(near))
        return b, RunStats(time.perf_counter() - t)

    jls_dec = lambda b, shape: imagecodecs.jpegls_decode(b).astype(np.float64)  # noqa: E731
    out["jls-sqrt8"] = Method("jls-sqrt8", s8[0], lambda c, r: s8[1](c, r), jls_enc, jls_dec,
                              (-1, 0, 40, True, None), "C (CharLS is C++; JPEG-LS is simple C)")
    out["jls-lin8"] = Method("jls-lin8", l8[0], lambda c, r: l8[1](c, r), jls_enc, jls_dec,
                             (-1, 0, 40, True, None), "C (CharLS is C++; JPEG-LS is simple C)")

    def j2k_enc(c, lvl):
        t = time.perf_counter()
        b = imagecodecs.jpeg2k_encode(c.astype(np.uint16), level=int(lvl), bitspersample=12,
                                      reversible=False, numthreads=1)
        return bytes(b), RunStats(time.perf_counter() - t)

    out["j2k-sqrt12"] = Method(
        "j2k-sqrt12", s12[0], lambda c, r: s12[1](c.astype(np.float64), r), j2k_enc,
        lambda b, shape: imagecodecs.jpeg2k_decode(b).astype(np.float64),
        (+1, 20, 80, True, None), "C (OpenJPEG)")

    # hydrium (BSD-2 C streaming JPEG XL encoder), study patch: the fixed HF multiplier
    # (hf_mult = 5) made a knob. Grey fed as R = G = B via pixel stride 1. Input is float32
    # linear light = sRGB-EOTF(sqrt-12 code / 4095) — hydrium's uint16 path (16-bit linear
    # LUT + polynomial sRGB decode) biases block means by ~-0.4 % of the code (measured), the
    # float path is unbiased. The header always says sRGB transfer, so djxl's PFM is code/4095.
    # ``-lfN``: second study patch, LF step / N with rounding (stock truncates LF values).
    hyd = scratch / "hydrium" / "hyd"
    for name, shift, lf in (("hyd-256", 0, 1), ("hyd-1frame", -1, 1), ("hyd-256-lf4", 0, 4)):
        def hyd_enc(c, k, shift=shift, lf=lf, measure_rss=False):
            # continuous knob k = HfMul × globalScale / 32768 (third study patch: globalScale
            # scales LF and HF steps together; HfMul is an integer >= 1)
            h, w = c.shape
            hf = max(1, int(math.floor(k)))
            gs = int(np.clip(round(32768 * k / hf), 1, 73728))
            v = srgb_eotf(c.astype(np.float64) / 4095.0).astype("<f4")
            return run([str(hyd), str(w), str(h), str(hf), str(shift), "2", str(lf), str(gs)],
                       stdin=v.tobytes(), measure_rss=measure_rss)

        def hyd_dec(b, shape):
            with tempfile.TemporaryDirectory(prefix="hydd_") as t:
                src, dst = Path(t) / "in.jxl", Path(t) / "out.pfm"
                src.write_bytes(b)
                run([tool("djxl"), str(src), str(dst), "--num_threads=0"], timeout=900)
                return read_pfm(dst).mean(axis=2)

        out[name] = Method(name, s12[0],
                           lambda v, r: s12[1](np.floor(np.clip(v, 0, 1) * 4095 + 0.5), r),
                           hyd_enc, hyd_dec, (+1, 0.1, 40.0, False, 1.0), "C (hydrium, BSD-2)")
        # ``-lin``: the linear plane as linear light (no sqrt LUT), like tiny-lin
        lin_fwd = lambda p, r: (p.astype(np.float64) - r.black) / (r.white - r.black)  # noqa: E731

        def hyd_enc_lin(v, k, shift=shift, lf=lf, measure_rss=False):
            h, w = v.shape
            hf = max(1, int(math.floor(k)))
            gs = int(np.clip(round(32768 * k / hf), 1, 73728))
            return run([str(hyd), str(w), str(h), str(hf), str(shift), "2", str(lf), str(gs)],
                       stdin=v.astype("<f4").tobytes(), measure_rss=measure_rss)

        out[name + "-lin"] = Method(
            name + "-lin", lin_fwd,
            lambda v, r: srgb_eotf(np.clip(v, 0, 1)) * (r.white - r.black) + r.black,
            hyd_enc_lin, hyd_dec, (+1, 0.1, 40.0, False, 1.0), "C (hydrium, BSD-2)")

    wl = scratch / "wl53" / "wl53"
    # wl53: L = 5, dead-zone rounding 0.15, tilt 0.4 (coarser levels get finer steps), chosen
    # on n6_cool air only (36-point grid); wl53-flat = MSE-flat steps (tilt 1, rounding 0.3).
    for name, mode, rnd, tilt in (("wl53", 1, 0.15, 0.4), ("wl53-flat", 1, 0.3, 1.0),
                                  ("wl53-norun", 0, 0.15, 0.4)):
        def wl_enc(c, q, mode=mode, rnd=rnd, tilt=tilt, measure_rss=False):
            h, w = c.shape
            o, st = run([str(wl), "enc", str(w), str(h), "5", f"{q:.4f}", str(rnd), str(mode),
                         str(tilt)], stdin=c.astype("<u2").tobytes(), measure_rss=measure_rss)
            return o, st

        def wl_dec(b, shape):
            h, w = shape
            o, _ = run([str(wl), "dec", str(w), str(h)], stdin=b)
            return np.frombuffer(o, "<i4").reshape(h, w).astype(np.float64)

        out[name] = Method(name, s12[0], lambda c, r: s12[1](c, r), wl_enc, wl_dec,
                           (-1, 0.5, 20000.0, False, 60.0), "C (prototype, ~400 lines)")
        if name == "wl53":  # the same coder on the native 8-bit linear plane (no curve)
            out["wl53-lin8"] = Method("wl53-lin8", l8[0], lambda c, r: l8[1](c, r), wl_enc,
                                      wl_dec, (-1, 0.05, 2000.0, False, 4.0),
                                      "C (prototype, ~400 lines)")
    return out


# ------------------------------------------------------------------ frame sets

def frameset(data: Path, cam: str, ill: str, cond: str, stop: int = -1):
    roi = rois.load(STUDY / "config" / "card_rois.yaml")[f"{cam}_{ill}"]
    vmin, dz = rs.CAMERAS[cam][3], rs.CAMERAS[cam][4]
    air = [rs.load(data, cam, ill, stop, i) for i in range(3)]
    air_ctx, air_noise = rs.context_for(air, roi, vmin)
    if cond == "uw":
        reps = [sim.thin(r, air_noise, seed=1000 + 100 * (stop + 2) + i, dead_zone=dz)
                for i, r in enumerate(air)]
        ctx, _ = rs.context_for(reps, roi, 2.0 if dz == 0 else 3.0)
        return reps[0], ctx
    return air[0], air_ctx


def score_method(fsid: str, raw, ctx, m: Method) -> list[dict]:
    planes = split(raw.mosaic, raw.cfa)
    shape = planes["R"].shape
    codes = {k: m.fwd(v, raw) for k, v in planes.items()}
    curves = {k: rate.Curve(lambda q, c=c: m.enc(c, q)) for k, c in codes.items()}
    total = lambda q: sum(c.size(q) for c in curves.values()) + HDR  # noqa: E731
    d, lo, hi, integer, start = m.knob
    rows = []
    for tname, bpp in TARGETS.items():
        tbytes = bpp * raw.n_px / 8
        t0 = time.perf_counter()
        try:
            sol = rate.solve(total, tbytes, lo, hi, d, integer, start=start)
        except Exception as exc:  # noqa: BLE001 — a codec failure is a result
            rows.append({"fsid": fsid, "method": m.name, "target": tname,
                         "note": f"ERROR {type(exc).__name__}: {exc}"[:300]})
            continue
        for k in sol.knobs:
            payloads = {p: c.get(k)[0] for p, c in curves.items()}
            enc_s = sum(c.get(k)[1] or 0 for c in curves.values())
            n = sum(len(p) for p in payloads.values()) + HDR
            dec = {p: m.inv(m.dec(b, shape), raw) for p, b in payloads.items()}
            recon = merge(dec, raw.cfa)
            met = rs.metrics.evaluate(ctx, recon)
            rows.append({"fsid": fsid, "method": m.name, "target": tname, "target_bytes": tbytes,
                         "knob": k, "bytes": n, "bpp": n * 8 / raw.n_px,
                         "reachable": sol.reachable, "note": sol.note, "enc_s_4planes": enc_s,
                         "search_s": time.perf_counter() - t0, **met})
    return rows


# ------------------------------------------------------------------ summaries

def at_target(rows: list[dict], target: str, bpp: float) -> dict | None:
    """One row per target; two bracketing rows (integer knob) are interpolated in log(bpp)."""
    rr = [r for r in rows if r.get("target") == target and r.get("bpp")]
    if not rr:
        return None
    if len(rr) == 1:
        return {k: rr[0].get(k) for k in KEYS} | {"reachable": rr[0].get("reachable", True)}
    a, b = sorted(rr, key=lambda r: r["bpp"])[:2]
    if a["bpp"] == b["bpp"]:
        return {k: a.get(k) for k in KEYS}
    w = (math.log(bpp) - math.log(a["bpp"])) / (math.log(b["bpp"]) - math.log(a["bpp"]))
    out = {k: a[k] + w * (b[k] - a[k]) for k in KEYS}
    out["bpp"] = bpp
    out["interpolated"] = True
    return out


def baselines(fsid: str) -> dict:
    want = {"D2/modular": "D2/modular", "D2/vardct": "D2/vardct", "D": "D", "D-lin": "D-lin",
            "M1": "", "L": "L", "C": "C"}
    with open(STUDY / "results" / "results.csv") as f:
        rows = [r for r in csv.DictReader(f) if r["fsid"] == fsid]
    out = {}
    for label, variant in want.items():
        meth = label.split("/")[0]
        sel = [r for r in rows if r["method"] == meth and r["variant"] == variant]
        conv = [{**{k: float(r[k]) for k in KEYS if r.get(k)}, "target": r["target"]}
                for r in sel]
        if label == "C":
            if conv:
                out["C"] = {"lossless": conv[0]}
            continue
        out[label] = {t: at_target(conv, t, b) for t, b in TARGETS.items()}
    return out


# ------------------------------------------------------------------ cost on one plane

def plane_cost(raw, m: Method, knob, reps: int = 3) -> dict:
    """Encode time (min of reps) and peak RSS for one 640×400 plane (G1) at ``knob``."""
    c = m.fwd(split(raw.mosaic, raw.cfa)["G1"], raw)
    best, rss, size = None, None, None
    for _ in range(reps):
        if m.name.startswith("tiny"):
            vals = srgb_eotf(c / 4095.0) if m.name == "tiny-srgb" else c
            b, st = Tiny(SCRATCH).enc_values(vals, knob, measure_rss=True)
        elif m.name.startswith("jxl-"):
            mode, e = m.name.split("-")[1], int(m.name.split("-e")[1])
            b, st = pc.jxl_enc(c, 4095, knob, mode, e, measure_rss=True)
        elif m.name.startswith(("hyd", "wl53")):
            b, st = m.enc(c, knob, measure_rss=True)
        else:
            b, st = m.enc(c, knob)
        best = st.seconds if best is None else min(best, st.seconds)
        rss = st.max_rss_bytes
        size = len(b)
    return {"plane": list(c.shape[::-1]), "knob": knob, "bytes": size, "enc_s_min": best,
            "peak_rss_mb": None if rss is None else rss / 2 ** 20,
            "note": "wall time incl. process start + file I/O" if not m.name.startswith("jls")
            else "in-process (imagecodecs/CharLS), no RSS"}


SCRATCH = Path(".")


def main(argv=None) -> int:
    global SCRATCH
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--fsets", default="n6_cool_air,n6_warm_air,ae3_cool_air,ae3_warm_air")
    ap.add_argument("--methods", default="")
    ap.add_argument("--cost", action="store_true", help="also time one 640×400 plane")
    args = ap.parse_args(argv)
    SCRATCH = args.scratch
    ms = methods(args.scratch)
    names = args.methods.split(",") if args.methods else list(ms)
    res = json.loads(OUT.read_text()) if OUT.exists() else {"rows": [], "baselines": {},
                                                             "cost": {}}
    for fs in args.fsets.split(","):
        cam, ill, cond = fs.split("_")
        fsid = f"{cam}_{ill}_s-1_{cond}"
        raw, ctx = frameset(args.data, cam, ill, cond)
        res["baselines"][fsid] = baselines(fsid)
        for name in names:
            t = time.perf_counter()
            rows = score_method(fsid, raw, ctx, ms[name])
            res["rows"] = [r for r in res["rows"] if not (r["fsid"] == fsid and
                                                          r["method"] == name)] + rows
            s = {t_: at_target(rows, t_, b) for t_, b in TARGETS.items()}
            print(f"{fsid:22s} {name:16s} " + "  ".join(
                f"{t_}: " + (" ".join(f"{s[t_][k]:.3f}" for k in KEYS) if s[t_] else "n/a")
                for t_ in TARGETS) + f"  ({time.perf_counter() - t:.0f} s)", flush=True)
            if args.cost and fs == args.fsets.split(",")[0]:
                k = next((r["knob"] for r in rows if r.get("target") == "T2" and "knob" in r),
                         None)
                if k is not None:
                    res["cost"][name] = plane_cost(raw, ms[name], k)
                    print("   cost:", res["cost"][name], flush=True)
            OUT.write_text(json.dumps(res, indent=1, default=rs._json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
