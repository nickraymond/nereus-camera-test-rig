"""Shared pieces of every method: the RAW input, Bayer plane split, the square-root curve,
the compact binary header, and the external-tool runner.

Every method's output is one byte string: ``header + payloads``. ``decode`` needs nothing
else — no sidecar, no hidden state — so the size counted is the size a backend receives.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

CHANNELS = ("R", "G1", "G2", "B")
PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")
REPO = Path(__file__).resolve().parents[1]
PRIMARY = REPO if (REPO / ".venv").exists() else REPO.parents[2]  # worktrees live 3 below
STUDY_LIB = PRIMARY / ".study-pylib"


# --------------------------------------------------------------------------- RAW input

@dataclass
class Raw:
    """A Bayer mosaic in sensor counts with one black level (all four positions equal)."""

    mosaic: np.ndarray  # (H, W) uint16
    cfa: str
    black: int
    white: int
    bits: int  # native stored bit depth (10 IMX708, 8 OpenMV)
    camera: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return self.mosaic.shape  # type: ignore[return-value]

    @property
    def n_px(self) -> int:
        return int(self.mosaic.size)


def from_rawframe(frame, camera: str) -> Raw:
    """``color.raw_io.RawFrame`` → ``Raw`` (refuses per-position black levels: none here)."""
    blacks = {float(b) for b in frame.black_level}
    if len(blacks) != 1 or frame.valid_crop is not None:
        raise ValueError(f"{camera}: need one black level and no crop, got "
                         f"{frame.black_level} crop {frame.valid_crop}")
    bits = int(frame.source.get("bits_per_sample") or 0)
    white = int(frame.white_level)
    native = 8 if white <= 255 else 10 if white <= 1023 else 12 if white <= 4095 else 16
    return Raw(mosaic=np.ascontiguousarray(frame.mosaic, dtype=np.uint16), cfa=frame.cfa,
               black=int(blacks.pop()), white=white, bits=native if bits in (0, 16) else bits,
               camera=camera, meta={"source": frame.source.get("path")})


# --------------------------------------------------------------------------- planes

def plane_offsets(cfa: str) -> dict[str, tuple[int, int]]:
    """{R, G1, G2, B: (dy, dx)} — G1 is the first green in raster order."""
    if cfa not in PATTERNS:
        raise ValueError(f"unknown CFA {cfa!r}")
    out, greens = {}, []
    for i, c in enumerate(cfa):
        pos = (i // 2, i % 2)
        if c == "G":
            greens.append(pos)
        else:
            out[c] = pos
    out["G1"], out["G2"] = greens
    return {k: out[k] for k in CHANNELS}


def split(mosaic: np.ndarray, cfa: str) -> dict[str, np.ndarray]:
    return {k: np.ascontiguousarray(mosaic[dy::2, dx::2])
            for k, (dy, dx) in plane_offsets(cfa).items()}


def merge(planes: dict[str, np.ndarray], cfa: str, dtype=np.float64) -> np.ndarray:
    h, w = planes["R"].shape
    out = np.empty((2 * h, 2 * w), dtype=dtype)
    for k, (dy, dx) in plane_offsets(cfa).items():
        out[dy::2, dx::2] = planes[k]
    return out


def tile(planes: dict[str, np.ndarray]) -> np.ndarray:
    """The four planes as one 2×2-tiled image (R G1 / G2 B)."""
    return np.block([[planes["R"], planes["G1"]], [planes["G2"], planes["B"]]])


def untile(img: np.ndarray) -> dict[str, np.ndarray]:
    h, w = img.shape[0] // 2, img.shape[1] // 2
    return {"R": img[:h, :w], "G1": img[:h, w:], "G2": img[h:, :w], "B": img[h:, w:]}


# --------------------------------------------------------------------------- curves

def sqrt_scale(black: int, white: int, b: int, pedestal: int = 0) -> float:
    return (2 ** b - 1) / np.sqrt(white - black + pedestal)


def sqrt_lut(black: int, white: int, b: int, pedestal: int = 0) -> np.ndarray:
    """Integer LUT raw count → b-bit code: floor(sqrt(v)·S + 0.5), v = clip(raw − black + p).

    Built in float64 once and shipped as integers, so a C/MCU port is a table lookup and
    reproduces it exactly (no float rounding-mode differences between implementations).
    """
    raw = np.arange(white + 1, dtype=np.float64)
    v = np.clip(raw - black + pedestal, 0, white - black + pedestal)
    return np.floor(np.sqrt(v) * sqrt_scale(black, white, b, pedestal) + 0.5).astype(np.uint16)


def sqrt_inverse(codes: np.ndarray, black: int, white: int, b: int,
                 pedestal: int = 0) -> np.ndarray:
    s = sqrt_scale(black, white, b, pedestal)
    return (np.asarray(codes, dtype=np.float64) / s) ** 2 - pedestal + black


def linear_lut(black: int, white: int, b: int) -> np.ndarray:
    raw = np.arange(white + 1, dtype=np.float64)
    v = np.clip(raw - black, 0, white - black)
    return np.floor(v * (2 ** b - 1) / (white - black) + 0.5).astype(np.uint16)


def linear_inverse(codes: np.ndarray, black: int, white: int, b: int) -> np.ndarray:
    return np.asarray(codes, dtype=np.float64) * (white - black) / (2 ** b - 1) + black


def forward(mosaic: np.ndarray, curve: str, black: int, white: int, b: int,
            pedestal: int = 0) -> np.ndarray:
    lut = sqrt_lut(black, white, b, pedestal) if curve == "sqrt" else linear_lut(black, white, b)
    return lut[np.minimum(mosaic, white)]


def inverse(codes: np.ndarray, curve: str, black: int, white: int, b: int,
            pedestal: int = 0) -> np.ndarray:
    if curve == "sqrt":
        return sqrt_inverse(codes, black, white, b, pedestal)
    return linear_inverse(codes, black, white, b)


# --------------------------------------------------------------------------- header

MAGIC = b"NR"
METHOD_IDS = {name: i for i, name in enumerate((
    "M1", "M1-444", "M1j", "M1-fix", "M2", "M2h", "C", "C-jls", "C-png", "C2", "N",
    "D", "D-j", "D2", "L", "D-lin", "D2-lin", "W", "H"), start=1)}
METHOD_NAMES = {v: k for k, v in METHOD_IDS.items()}


def _uvarint(n: int) -> bytes:
    if n < 0:
        raise ValueError(f"varint must be ≥ 0, got {n}")
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _read_uvarint(buf: bytes, pos: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        n |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return n, pos


def _zz(v: int) -> int:
    return (v << 1) ^ (v >> 63) if v >= 0 else ((-v) << 1) - 1


def _unzz(u: int) -> int:
    return (u >> 1) if not u & 1 else -((u + 1) >> 1)


@dataclass
class Header:
    method: str
    w: int
    h: int
    cfa: str
    black: int
    white: int
    b: int = 0  # code bits of the transmitted planes (0 when not applicable)
    pedestal: int = 0
    flags: int = 0  # method-specific (layout, curve, codec mode)
    params: list[int] = field(default_factory=list)  # signed ints, method-specific
    lengths: list[int] = field(default_factory=list)  # payload byte lengths

    def pack(self) -> bytes:
        out = bytearray(MAGIC)
        out += bytes([METHOD_IDS[self.method], self.flags & 0xFF, PATTERNS.index(self.cfa),
                      self.b])
        for n in (self.w, self.h, self.black, self.white, self.pedestal, len(self.params)):
            out += _uvarint(n)
        for p in self.params:
            out += _uvarint(_zz(int(p)))
        out += _uvarint(len(self.lengths))
        for n in self.lengths:
            out += _uvarint(n)
        return bytes(out)

    @classmethod
    def unpack(cls, blob: bytes) -> tuple["Header", int]:
        if blob[:2] != MAGIC:
            raise ValueError(f"bad magic {blob[:2]!r}")
        method, flags, cfa, b = blob[2], blob[3], blob[4], blob[5]
        pos = 6
        vals = []
        for _ in range(6):
            n, pos = _read_uvarint(blob, pos)
            vals.append(n)
        w, h, black, white, pedestal, n_params = vals
        params = []
        for _ in range(n_params):
            u, pos = _read_uvarint(blob, pos)
            params.append(_unzz(u))
        n_len, pos = _read_uvarint(blob, pos)
        lengths = []
        for _ in range(n_len):
            n, pos = _read_uvarint(blob, pos)
            lengths.append(n)
        return cls(METHOD_NAMES[method], w, h, PATTERNS[cfa], black, white, b, pedestal,
                   flags, params, lengths), pos


def pack(header: Header, payloads: list[bytes]) -> bytes:
    header.lengths = [len(p) for p in payloads]
    return header.pack() + b"".join(payloads)


def unpack(blob: bytes) -> tuple[Header, list[bytes]]:
    header, pos = Header.unpack(blob)
    payloads = []
    for n in header.lengths:
        payloads.append(blob[pos:pos + n])
        pos += n
    if pos != len(blob):
        raise ValueError(f"{len(blob) - pos} trailing bytes after {header.method} payloads")
    return header, payloads


# --------------------------------------------------------------------------- tools

class ToolError(RuntimeError):
    pass


def tool(name: str) -> str:
    """An external tool: the study's own build first (``.study-pylib/bin``), then PATH."""
    local = STUDY_LIB / "bin" / name
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    path = shutil.which(name)
    if not path:
        raise ToolError(f"{name} not found (looked in {local.parent} and PATH)")
    return path


def has_tool(name: str) -> bool:
    try:
        tool(name)
        return True
    except ToolError:
        return False


_RSS = re.compile(rb"(\d+)\s+maximum resident set size")
_RSS_LINUX = re.compile(rb"Maximum resident set size \(kbytes\):\s*(\d+)")


@dataclass
class RunStats:
    seconds: float
    max_rss_bytes: Optional[int] = None


def run(cmd: list[str], *, stdin: Optional[bytes] = None, timeout: float = 600,
        measure_rss: bool = False) -> tuple[bytes, RunStats]:
    """Run ``cmd``; return (stdout, timing). Fails loudly with the tool's stderr."""
    full = list(cmd)
    if measure_rss:
        full = ["/usr/bin/time", "-l" if sys.platform == "darwin" else "-v"] + full
    t0 = time.perf_counter()
    try:
        r = subprocess.run(full, input=stdin, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"{Path(cmd[0]).name} timed out after {timeout:.0f} s") from exc
    dt = time.perf_counter() - t0
    if r.returncode != 0:
        raise ToolError(f"{Path(cmd[0]).name} exit {r.returncode}: "
                        f"{r.stderr.decode(errors='replace').strip()[-500:]}  cmd={cmd}")
    rss = None
    if measure_rss:
        m = _RSS.search(r.stderr) or _RSS_LINUX.search(r.stderr)
        if m:
            rss = int(m.group(1)) * (1 if sys.platform == "darwin" else 1024)
    return r.stdout, RunStats(dt, rss)


# --------------------------------------------------------------------------- PNM

def write_pgm(path: Path, img: np.ndarray, maxval: int) -> None:
    """Binary P5; 8-bit when maxval < 256, else 16-bit big-endian (netpbm convention)."""
    h, w = img.shape
    dtype = np.uint8 if maxval < 256 else ">u2"
    Path(path).write_bytes(f"P5\n{w} {h}\n{maxval}\n".encode() + img.astype(dtype).tobytes())


def write_ppm(path: Path, img: np.ndarray, maxval: int = 255) -> None:
    h, w, _ = img.shape
    dtype = np.uint8 if maxval < 256 else ">u2"
    Path(path).write_bytes(f"P6\n{w} {h}\n{maxval}\n".encode() + img.astype(dtype).tobytes())


def read_pnm(path: Path) -> np.ndarray:
    """P5/P6 → uint16 array (H, W) or (H, W, 3); handles comments-free headers from tools."""
    data = Path(path).read_bytes()
    parts, pos = [], 0
    while len(parts) < 4:
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            pos = data.index(b"\n", pos) + 1
            continue
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        parts.append(data[pos:end])
        pos = end
    pos += 1  # single whitespace before the raster
    magic, w, h, maxval = parts[0], int(parts[1]), int(parts[2]), int(parts[3])
    ch = {b"P5": 1, b"P6": 3}[magic]
    dtype = np.uint8 if maxval < 256 else ">u2"
    arr = np.frombuffer(data, dtype=dtype, count=w * h * ch, offset=pos).astype(np.uint16)
    return arr.reshape((h, w) if ch == 1 else (h, w, 3))


# --------------------------------------------------------------------------- sRGB

def srgb_oetf(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def srgb_eotf(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    return np.where(v <= 0.04045, v / 12.92, np.power((v + 0.055) / 1.055, 2.4))
