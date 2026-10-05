"""Lossless Bayer-plane packer — MED prediction + adaptive Golomb-Rice (LOCO-I / JPEG-LS style).

The reference for later MCU work (Cortex-M55): one Bayer plane (2-D ``uint16``, values
< 2**b, b in 1..16) in, one byte string out. Two implementations write the **same bytes**:
this file's pure-Python reference (``impl="py"``) and ``packer.c`` (``impl="c"``, run as a
subprocess). The C core needs only two row buffers and a bit writer, so it ports to static
arrays.

Bitstream (fixed; ``packer.c`` documents the same):

- Samples in raster order. Predictor MED / LOCO-I with a = left, b = above, c = above-left::

      pred = min(a, b)   if c >= max(a, b)
             max(a, b)   if c <= min(a, b)
             a + b - c   otherwise

  Edges: pixel (0, 0) predicts 1 << (b - 1); the rest of row 0 predicts the left neighbour;
  column 0 of rows >= 1 predicts the pixel above.
- Residual e = x - pred, reduced modulo 2**b into [-2**(b-1), 2**(b-1)); zigzag
  m = 2e (e >= 0) or -2e - 1 (e < 0), so 0 <= m < 2**b.
- Adaptive Rice parameter, state reset at the start of **every row**: A = max(2, (2**b + 32)
  >> 6), N = 1. Per sample: k = smallest k >= 0 with (N << k) >= A; code m; then A += |e|,
  N += 1, and when N reaches 64: A >>= 1, N >>= 1.
- Limited-length code (JPEG-LS): qbpp = b, LIMIT = 2 * (b + max(8, b)), qmax = LIMIT - qbpp - 1.
  q = m >> k. If q < qmax: q zero bits, a one bit, the k low bits of m. Otherwise (escape):
  qmax zero bits, a one bit, m - 1 in qbpp bits (m >= 1 here, since m = 0 gives q = 0).
- Bits MSB-first within bytes; the plane's stream is zero-padded to a byte boundary. The
  caller concatenates planes and records their lengths in its own header.

Bound: an escape is exactly LIMIT bits and a regular code is at most LIMIT - 1 bits (A / N
never exceeds max(A0, 2**(b-1)), so k <= b), hence ``max_plane_bytes(w, h, b)`` =
ceil(w·h·LIMIT / 8) is a hard bound on the output (LIMIT <= 64 for b <= 16).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np

HERE = Path(__file__).resolve().parent
C_SOURCE = HERE / "packer.c"
C_SOURCES = (C_SOURCE, HERE / "packer_core.h")  # the CLI and the shared codec core
C_BINARY = HERE.parent / "bin" / "packer"  # git-ignored (compression_study/.gitignore)
TIMEOUT_S = 300.0
STATE_BYTES = 32  # coder state on the MCU, see static_memory_bytes()


class PackerError(RuntimeError):
    """Bad input, a corrupt / truncated stream, or the C packer failed or is missing."""


# --------------------------------------------------------------------------- parameters

def limit_bits(b: int) -> int:
    """JPEG-LS LIMIT: the longest code for one sample, in bits."""
    return 2 * (b + max(8, b))


def max_plane_bytes(w: int, h: int, b: int) -> int:
    """Hard bound on one plane's stream: every sample costs at most LIMIT bits."""
    return (w * h * limit_bits(b) + 7) // 8


def static_memory_bytes(w: int, b: int) -> int:
    """Encoder working memory on the MCU, excluding the output buffer.

    Two ``uint16`` row buffers (previous + current row) of ``w`` samples each — ``uint16``
    for every b, so ``b`` does not change the figure — plus ``STATE_BYTES`` for the coder
    state: A, N, the 32-bit bit accumulator and its fill count, the output position and the
    per-plane constants (b, LIMIT, A0), each a 32-bit word, rounded up to 32 bytes. The
    output needs ``max_plane_bytes`` for a whole plane, or ceil(w·LIMIT / 8) + 4 bytes when
    the caller drains it after every row (as the CLI in ``packer.c`` does).
    """
    _check_depth(b)
    return 2 * w * 2 + STATE_BYTES


def _check_depth(b: int) -> None:
    if not isinstance(b, (int, np.integer)) or not 1 <= b <= 16:
        raise PackerError(f"bit depth b must be an integer in 1..16, got {b!r}")


def _check_plane(plane: np.ndarray, b: int) -> np.ndarray:
    _check_depth(b)
    plane = np.asarray(plane)
    if plane.ndim != 2 or plane.size == 0 or not np.issubdtype(plane.dtype, np.integer):
        raise PackerError(f"plane must be a non-empty 2-D integer array, got {plane.dtype} "
                          f"{plane.shape}")
    if plane.min() < 0 or plane.max() >= (1 << b):
        raise PackerError(f"plane values {plane.min()}..{plane.max()} outside 0..{(1 << b) - 1}"
                          f" for b={b}")
    return plane.astype(np.int64)


# --------------------------------------------------------------------------- public API

def encode_plane(plane: np.ndarray, b: int, impl: str = "auto") -> bytes:
    """Losslessly code one Bayer plane (values < 2**b) into one byte string."""
    values = _check_plane(plane, b)
    if _choose(impl) == "c":
        h, w = values.shape
        raw = values.astype("<u2").tobytes()
        return _run_c(["enc", str(w), str(h), str(b)], raw)
    return _encode_py(values, b)


def decode_plane(data: bytes, w: int, h: int, b: int, impl: str = "auto") -> np.ndarray:
    """Inverse of ``encode_plane``: (h, w) ``uint16``. A corrupt stream raises PackerError."""
    _check_depth(b)
    if w < 1 or h < 1:
        raise PackerError(f"plane size must be at least 1x1, got {w}x{h}")
    if _choose(impl) == "c":
        raw = _run_c(["dec", str(w), str(h), str(b)], bytes(data))
        if len(raw) != 2 * w * h:
            raise PackerError(f"C decoder returned {len(raw)} B, expected {2 * w * h}")
        return np.frombuffer(raw, dtype="<u2").reshape(h, w).astype(np.uint16)
    return _decode_py(bytes(data), w, h, b)


def build_c(out_dir: Optional[Path] = None) -> Path:
    """Compile ``packer.c`` (``cc -O2 -std=c99``) unless the binary is already newer."""
    binary = Path(out_dir) / "packer" if out_dir is not None else C_BINARY
    if binary.is_file() and binary.stat().st_mtime >= _newest_source():
        return binary
    cc = shutil.which("cc")
    if not cc:
        raise PackerError("C compiler `cc` not found on PATH — install the Xcode command-line "
                          "tools (`xcode-select --install`) or build-essential")
    binary.parent.mkdir(parents=True, exist_ok=True)
    cmd = [cc, "-O2", "-std=c99", "-Wall", "-Wextra", "-o", str(binary), str(C_SOURCE)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise PackerError(f"{' '.join(cmd)} failed ({proc.returncode}):\n{proc.stderr}")
    return binary


def c_available() -> bool:
    """True when the compiled binary exists and is not older than its sources."""
    return C_BINARY.is_file() and C_BINARY.stat().st_mtime >= _newest_source()


def _newest_source() -> float:
    return max(p.stat().st_mtime for p in C_SOURCES)


def _choose(impl: str) -> str:
    if impl == "auto":
        return "c" if c_available() else "py"
    if impl not in ("py", "c"):
        raise PackerError(f"impl must be 'py', 'c' or 'auto', got {impl!r}")
    return impl


def _run_c(args: list[str], stdin: bytes) -> bytes:
    if not C_BINARY.is_file():
        raise PackerError(f"{C_BINARY} not built — call packer.build_c() first")
    proc = subprocess.run([str(C_BINARY), *args], input=stdin, capture_output=True,
                          timeout=TIMEOUT_S)
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip()
        raise PackerError(f"packer {' '.join(args)} failed ({proc.returncode}): {err}")
    return proc.stdout


# --------------------------------------------------------------------------- Python reference

def _predict(x: np.ndarray, b: int) -> np.ndarray:
    """MED prediction for every pixel at once (the encoder knows the whole plane)."""
    pred = np.empty_like(x)
    pred[0, 0] = 1 << (b - 1)
    pred[0, 1:] = x[0, :-1]  # row 0: left
    pred[1:, 0] = x[:-1, 0]  # column 0: above
    left, up, ul = x[1:, :-1], x[:-1, 1:], x[:-1, :-1]
    lo, hi = np.minimum(left, up), np.maximum(left, up)
    pred[1:, 1:] = np.where(ul >= hi, lo, np.where(ul <= lo, hi, left + up - ul))
    return pred


def _residuals(x: np.ndarray, b: int) -> tuple[np.ndarray, np.ndarray]:
    """Zigzag codes m and magnitudes |e| of the modulo-reduced residuals."""
    half, mask = 1 << (b - 1), (1 << b) - 1
    e = ((x - _predict(x, b) + half) & mask) - half
    return np.where(e >= 0, 2 * e, -2 * e - 1), np.abs(e)


def _encode_py(x: np.ndarray, b: int) -> bytes:
    h, w = x.shape
    m_all, mag_all = _residuals(x, b)
    qmax = limit_bits(b) - b - 1
    a0 = max(2, ((1 << b) + 32) >> 6)
    parts: list[str] = []  # the stream as '0'/'1' text, packed at the end
    for y in range(h):
        A, N = a0, 1  # reset every row
        for m, mag in zip(m_all[y].tolist(), mag_all[y].tolist()):
            k = 0
            while (N << k) < A:
                k += 1
            q = m >> k
            if q < qmax:
                parts.append("0" * q + "1")
                if k:
                    parts.append(format(m & ((1 << k) - 1), f"0{k}b"))
            else:  # escape: fixed LIMIT bits
                parts.append("0" * qmax + "1" + format(m - 1, f"0{b}b"))
            A += mag
            N += 1
            if N == 64:
                A >>= 1
                N >>= 1
    bits = "".join(parts)
    bits += "0" * (-len(bits) % 8)
    return np.packbits(np.frombuffer(bits.encode("ascii"), np.uint8) - 48).tobytes()


def _decode_py(data: bytes, w: int, h: int, b: int) -> np.ndarray:
    s = (np.unpackbits(np.frombuffer(data, np.uint8)) + 48).tobytes().decode("ascii")
    total = len(s)
    qmax = limit_bits(b) - b - 1
    a0 = max(2, ((1 << b) + 32) >> 6)
    mask = (1 << b) - 1
    out = np.empty((h, w), np.uint16)
    prev: list[int] = []
    pos = 0
    for y in range(h):
        A, N = a0, 1
        row = [0] * w
        for x in range(w):
            k = 0
            while (N << k) < A:
                k += 1
            one = s.find("1", pos)
            if one < 0:
                raise PackerError(f"stream truncated at row {y}, column {x} (bit {pos})")
            z = one - pos
            pos = one + 1
            if z < qmax:
                m = (z << k) | (int(s[pos:pos + k], 2) if k else 0)
                pos += k
            elif z == qmax:
                m = int(s[pos:pos + b], 2) + 1
                pos += b
            else:
                raise PackerError(f"corrupt stream at row {y}, column {x}: {z} zero bits "
                                  f"(max {qmax})")
            if pos > total:
                raise PackerError(f"stream truncated at row {y}, column {x}")
            e = m >> 1 if m & 1 == 0 else -((m + 1) >> 1)
            if y == 0:
                pred = row[x - 1] if x else 1 << (b - 1)
            elif x == 0:
                pred = prev[0]
            else:
                a, c, up = row[x - 1], prev[x - 1], prev[x]
                lo, hi = (a, up) if a < up else (up, a)
                pred = lo if c >= hi else hi if c <= lo else a + up - c
            row[x] = (pred + e) & mask
            A += -e if e < 0 else e
            N += 1
            if N == 64:
                A >>= 1
                N >>= 1
        out[y] = row
        prev = row
    used = (pos + 7) // 8
    if used != len(data) or "1" in s[pos:]:
        raise PackerError(f"stream has {len(data)} B but the plane used {used} B "
                          "(trailing data or non-zero padding)")
    return out
