"""Single-plane (grayscale) codecs for the raw methods C, C-ref, C2, N, D, D-j, D2, W, H.

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


# ---------------------------------------------------------------- hydrium (JPEG XL in plain C)

HYD_SRC = Path(__file__).with_name("hyd.c")
HYD_BIN = Path(__file__).resolve().parents[1] / "bin" / "hyd"
HYD_LF = 4  # LF step divisor (desk-study variant hyd-256-lf4-lin)


def hyd_build() -> str:
    """Compile methods/hyd.c + the vendored hydrium with ``cc`` unless the binary is current.
    -ffp-contract=off: no fused multiply-add, so the floats (and bytes) match the board."""
    here = HYD_SRC.parent
    srcs = [HYD_SRC, here / "hyd_plane.h", *sorted((here / "hydrium").rglob("*.[ch]"))]
    if HYD_BIN.is_file() and HYD_BIN.stat().st_mtime >= max(p.stat().st_mtime for p in srcs):
        return str(HYD_BIN)
    HYD_BIN.parent.mkdir(parents=True, exist_ok=True)
    run(["cc", "-O2", "-std=c11", "-ffp-contract=off", "-w", f"-I{here / 'hydrium'}",
         f"-I{here}", "-o", str(HYD_BIN), str(HYD_SRC),
         *[str(p) for p in sorted((here / "hydrium").glob("*.c"))]], timeout=TIMEOUT)
    return str(HYD_BIN)


def hyd_params(k: float) -> tuple[int, int]:
    """Continuous knob k = HF multiplier × globalScale / 32768 → (HF multiplier, globalScale);
    bigger k → more bytes. The board probe uses the same mapping."""
    hf = max(1, int(np.floor(k)))
    gs = int(min(73728, max(1, int(32768 * k / hf + 0.5))))
    return hf, gs


def hyd_enc(codes: np.ndarray, nlut: int, black: int, white: int, hf: int, gs: int,
            lf: int = HYD_LF, measure_rss: bool = False):
    """One plane of integer sensor counts as linear light (code − black) / (white − black)."""
    h, w = codes.shape
    return run([hyd_build(), "enc", str(w), str(h), str(nlut), str(black), str(white), str(hf),
                str(gs), str(lf)], stdin=codes.astype("<u2").tobytes(), timeout=TIMEOUT,
               measure_rss=measure_rss)


def read_pfm(path: Path) -> np.ndarray:
    """float64 (H, W, C) from a PFM (rows bottom-up on disk)."""
    data = Path(path).read_bytes()
    lines, pos = [], 0
    while len(lines) < 3:
        end = data.index(b"\n", pos)
        lines.append(data[pos:end].strip())
        pos = end + 1
    w, h = (int(x) for x in lines[1].split())
    ch = 3 if lines[0] == b"PF" else 1
    dt = "<f4" if float(lines[2]) < 0 else ">f4"
    arr = np.frombuffer(data, dtype=dt, count=w * h * ch, offset=pos).reshape(h, w, ch)
    return arr[::-1].astype(np.float64)


def hyd_dec(data: bytes) -> np.ndarray:
    """Linear light (≈ (code − black) / (white − black)): djxl's float output is sRGB-encoded
    (hydrium tags the image sRGB); undo it, mirrored for the small negatives near black."""
    with _tmp() as d:
        src, dst = Path(d) / "in.jxl", Path(d) / "out.pfm"
        src.write_bytes(data)
        run([tool("djxl"), str(src), str(dst), "--num_threads=0"], timeout=TIMEOUT)
        v = read_pfm(dst).mean(axis=2)
    a = np.abs(v)
    lin = np.where(a <= 0.04045, a / 12.92, np.power((a + 0.055) / 1.055, 2.4))
    return np.sign(v) * lin


# knob direction: +1 = larger knob → more bytes; −1 = larger knob → fewer bytes
KNOBS = {"jpeg": (+1, 1, 100), "jpegli": (+1, 1, 100), "jxl": (-1, 0.05, 25.0),
         "wl53": (-1, 0.5, 20000.0), "hyd": (+1, 0.05, 40.0)}
ENCODERS = {"jpeg": jpeg_enc, "jpegli": jpegli_enc, "jxl": jxl_enc}
DECODERS = {"jpeg": jpeg_dec, "jpegli": jpegli_dec, "jxl": jxl_dec}
