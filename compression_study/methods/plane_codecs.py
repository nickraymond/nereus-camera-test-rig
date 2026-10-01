"""Single-plane (grayscale) codecs for the raw methods C, C-ref, C2, N, D, D-j, D2.

Each codec is a pair ``enc(codes, maxval, knob) -> bytes`` / ``dec(bytes) -> codes`` on a
2-D integer array whose values are ≤ ``maxval``. External tools run single-threaded on PNM
files in a temporary folder; the bytes returned are exactly what would be transmitted.

Knobs: JPEG / jpegli ``quality`` (int, higher = bigger); JPEG XL ``distance`` (float,
lower = bigger; 0 = lossless). ``KNOBS`` says which way each knob runs, for rate search.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import cv2
import numpy as np

from ..common import read_pnm, run, tool, write_pgm

TIMEOUT = 900


def _tmp() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="nrcs_")


# ---------------------------------------------------------------- libjpeg-turbo (cjpeg)

def jpeg_enc(codes: np.ndarray, maxval: int, quality: int, measure_rss: bool = False):
    if maxval > 255:
        raise ValueError("baseline JPEG takes 8-bit planes")
    with _tmp() as d:
        src, dst = Path(d) / "in.pgm", Path(d) / "out.jpg"
        write_pgm(src, codes, 255)
        _, st = run([tool("cjpeg"), "-grayscale", "-quality", str(int(quality)), "-optimize",
                     "-outfile", str(dst), str(src)], timeout=TIMEOUT, measure_rss=measure_rss)
        return dst.read_bytes(), st


def jpeg_dec(data: bytes) -> np.ndarray:
    out, _ = run([tool("djpeg"), "-pnm"], stdin=data, timeout=TIMEOUT)
    with _tmp() as d:
        p = Path(d) / "o.pgm"
        p.write_bytes(out)
        return read_pnm(p)


# ---------------------------------------------------------------- jpegli (cjpegli)

def jpegli_enc(codes: np.ndarray, maxval: int, quality: int, measure_rss: bool = False):
    if maxval > 255:
        raise ValueError("D-j takes 8-bit planes")
    with _tmp() as d:
        src, dst = Path(d) / "in.pgm", Path(d) / "out.jpg"
        write_pgm(src, codes, 255)
        _, st = run([tool("cjpegli"), str(src), str(dst), "-q", str(int(quality))],
                    timeout=TIMEOUT, measure_rss=measure_rss)
        return dst.read_bytes(), st


def jpegli_dec(data: bytes) -> np.ndarray:
    with _tmp() as d:
        src, dst = Path(d) / "in.jpg", Path(d) / "out.pgm"
        src.write_bytes(data)
        run([tool("djpegli"), str(src), str(dst)], timeout=TIMEOUT)
        return read_pnm(dst)


# ---------------------------------------------------------------- JPEG XL (cjxl)

JXL_MODES = {
    "vardct": ["-m", "0"],
    "modular": ["-m", "1"],
    "vardct_np": ["-m", "0", "--disable_perceptual_optimizations"],
    "modular_np": ["-m", "1", "--disable_perceptual_optimizations"],
}


def jxl_enc(codes: np.ndarray, maxval: int, distance: float, mode: str = "vardct",
            effort: int = 7, measure_rss: bool = False):
    """PGM in with maxval = 2^b − 1, untagged (cjxl assumes the sRGB transfer for grey
    input — fine for sqrt codes: the error is spread evenly in code space)."""
    with _tmp() as d:
        src, dst = Path(d) / "in.pgm", Path(d) / "out.jxl"
        write_pgm(src, codes, maxval)
        args = ["-d", "0"] if distance == 0 else ["-d", f"{distance:.4f}"] + JXL_MODES[mode]
        _, st = run([tool("cjxl"), str(src), str(dst), *args, "-e", str(effort),
                     "--num_threads=0"], timeout=TIMEOUT, measure_rss=measure_rss)
        return dst.read_bytes(), st


def jxl_dec(data: bytes) -> np.ndarray:
    with _tmp() as d:
        src, dst = Path(d) / "in.jxl", Path(d) / "out.pgm"
        src.write_bytes(data)
        run([tool("djxl"), str(src), str(dst), "--num_threads=0"], timeout=TIMEOUT)
        return read_pnm(dst)


# ---------------------------------------------------------------- lossless references

def jls_enc(codes: np.ndarray, maxval: int, measure_rss: bool = False):
    import imagecodecs  # study-only dependency (.study-pylib); CharLS inside, BSD-3

    arr = codes.astype(np.uint8 if maxval < 256 else np.uint16)
    try:
        return imagecodecs.jpegls_encode(arr), None
    except Exception:  # CharLS sizes its buffer for compressible data; give it 16-bit room
        return imagecodecs.jpegls_encode(arr.astype(np.uint16)), None


def jls_dec(data: bytes) -> np.ndarray:
    import imagecodecs

    return imagecodecs.jpegls_decode(data).astype(np.uint16)


def png_enc(codes: np.ndarray, maxval: int, measure_rss: bool = False):
    arr = codes.astype(np.uint8 if maxval < 256 else np.uint16)
    ok, buf = cv2.imencode(".png", arr, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    if not ok:
        raise RuntimeError("cv2.imencode(.png) failed")
    return buf.tobytes(), None


def png_dec(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    return img.astype(np.uint16)


# ---------------------------------------------------------------- packer

def packer_enc(codes: np.ndarray, maxval: int, measure_rss: bool = False):
    from . import packer

    b = int(maxval).bit_length()
    return packer.encode_plane(codes.astype(np.uint16), b), None


def packer_dec_factory(w: int, h: int, b: int):
    from . import packer

    return lambda data: packer.decode_plane(data, w, h, b)


# ---------------------------------------------------------------- wl53 (own wavelet codec)

WL53_SRC = Path(__file__).with_name("wl53.c")
WL53_BIN = Path(__file__).resolve().parents[1] / "bin" / "wl53"


def wl53_build() -> str:
    """Compile methods/wl53.c (+ wl53_core.h) with ``cc`` unless the binary is current."""
    srcs = (WL53_SRC, WL53_SRC.with_name("wl53_core.h"))
    if WL53_BIN.is_file() and WL53_BIN.stat().st_mtime >= max(p.stat().st_mtime for p in srcs):
        return str(WL53_BIN)
    WL53_BIN.parent.mkdir(parents=True, exist_ok=True)
    run(["cc", "-O2", "-std=c99", "-Wall", "-Wextra", "-o", str(WL53_BIN), str(WL53_SRC)],
        timeout=TIMEOUT)
    return str(WL53_BIN)


def wl53_enc(codes: np.ndarray, maxval: int, q: float, measure_rss: bool = False):
    """``q`` = quantizer scale (bigger → fewer bytes); sent as Q16 = round(q * 16)."""
    if maxval > 4095:
        raise ValueError("wl53 takes planes of <= 12 bits")
    h, w = codes.shape
    return run([wl53_build(), "enc", str(w), str(h), str(max(1, round(q * 16))), "1"],
               stdin=codes.astype("<u2").tobytes(), timeout=TIMEOUT, measure_rss=measure_rss)


def wl53_dec_factory(w: int, h: int):
    def dec(data: bytes) -> np.ndarray:
        out, _ = run([wl53_build(), "dec", str(w), str(h)], stdin=data, timeout=TIMEOUT)
        return np.clip(np.frombuffer(out, "<i2").reshape(h, w), 0, None).astype(np.uint16)
    return dec


# knob direction: +1 = larger knob → more bytes; −1 = larger knob → fewer bytes
KNOBS = {"jpeg": (+1, 1, 100), "jpegli": (+1, 1, 100), "jxl": (-1, 0.05, 25.0),
         "wl53": (-1, 0.5, 20000.0)}
ENCODERS = {"jpeg": jpeg_enc, "jpegli": jpegli_enc, "jxl": jxl_enc}
DECODERS = {"jpeg": jpeg_dec, "jpegli": jpegli_dec, "jxl": jxl_dec}
