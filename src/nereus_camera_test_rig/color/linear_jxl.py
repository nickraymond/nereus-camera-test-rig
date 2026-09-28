"""Linear JPEG XL transport — RAW-derived, invertible, small (prototype, Nick 2026-09-28).

The field camera sends a small, RAW-derived image; the cloud recovers **linear camera RGB**
from it and does all the colour work. The camera applies nothing that cannot be undone:

    u = clip(x + pedestal, 0) · gains          x: black-subtracted linear RGB (0..1 of white)
    y = sqrt(u)                                 noise-matched: shot noise ∝ sqrt(signal)
    code = round(y · (2**bits − 1))             10-bit by default → JPEG XL (cjxl)

``gains`` = the white balance (only to lift the weak red channel before quantization) scaled
so the brightest value maps to 1 — nothing clips. ``pedestal`` keeps the sensor's negative
noise around black (clipping it would bias dark means upward). The decoder inverts exactly::

    x = (code / (2**bits − 1))² / gains − pedestal

Everything needed to invert is in the JSON sidecar written next to the ``.jxl`` (with the
file's SHA-256, checked on decode). Measured with this code on TG-7 P9150344 (15.5 m, ISO 100,
centred 1600×900 crop binned to 800×450; ``jxl-check``, 2026-09-28): distance 1.0 → 42 KB
with the as-shot WB / 47 KB with the card-grey WB; 32 × 32 px region means move by a median
0.1 % and p99 1.8 % / 0.6 % (the card WB gives red the full code range, so it is more
accurate). Distance 0.5 → 83–103 KB, p99 0.3–0.9 %. Lossless → 709 KB.

The codec is libjxl's command-line tools (``cjxl`` / ``djxl``, BSD-3-Clause), called through
``subprocess`` with PNM files: no Python binding, nothing bundled. Missing tools fail loudly
with an install hint (``docs/hardware_setup.md``).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

FORMAT, VERSION = "nereus-linear-jxl", 1
BITS = 10
DISTANCE = 1.0  # cjxl -d: 0 = lossless; 1.0 fits bmcam001's ~55 KB budget (see above)
EFFORT = 7  # cjxl default
PEDESTAL = 0.005  # of white, before gains: ~2.5× the TG-7 red read noise at ISO 100
TIMEOUT_S = 120.0
INSTALL_HINT = "install libjxl's tools: `brew install jpeg-xl` (Mac) / `apt install libjxl-tools`"


class JxlError(RuntimeError):
    """A cjxl/djxl call failed, a tool is missing, or a file does not match its sidecar."""


def tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise JxlError(f"{name} not found on PATH — {INSTALL_HINT}")
    return path


def headroom_gains(rgb: np.ndarray, wb: Sequence[float], pedestal: float = PEDESTAL):
    """``wb`` scaled so the brightest ``(x + pedestal) · wb`` value is exactly 1."""
    wb = np.asarray(wb, dtype=np.float64)
    if wb.shape != (3,) or np.any(wb <= 0):
        raise ValueError(f"white balance must be 3 positive gains, got {wb.tolist()}")
    peak = float(np.max((np.maximum(np.asarray(rgb, np.float64) + pedestal, 0.0)) * wb))
    return wb / max(peak, 1e-12)


def to_codes(rgb: np.ndarray, gains, bits: int = BITS, pedestal: float = PEDESTAL):
    u = np.maximum(np.asarray(rgb, np.float64) + pedestal, 0.0) * np.asarray(gains)
    return np.round(np.sqrt(np.clip(u, 0.0, 1.0)) * (2 ** bits - 1)).astype(np.uint16)


def from_codes(codes: np.ndarray, gains, bits: int = BITS, pedestal: float = PEDESTAL):
    y = codes.astype(np.float64) / (2 ** bits - 1)
    return (y * y / np.asarray(gains) - pedestal).astype(np.float32)


def write_ppm(path: Path, codes: np.ndarray, bits: int) -> None:
    h, w, _ = codes.shape
    path.write_bytes(f"P6\n{w} {h}\n{2 ** bits - 1}\n".encode()
                     + codes.astype(">u2").tobytes())


def read_ppm(path: Path) -> tuple[np.ndarray, int]:
    """(codes, maxval) of a binary 16-bit P6 file as written by djxl."""
    data = path.read_bytes()
    magic, dims, maxval, body = data.split(b"\n", 3)
    w, h = (int(v) for v in dims.split())
    if magic != b"P6" or int(maxval) < 256:
        raise JxlError(f"{path}: expected a 16-bit P6 image, got {magic!r} maxval {maxval!r}")
    return np.frombuffer(body, ">u2", count=w * h * 3).reshape(h, w, 3), int(maxval)


def _run(cmd: list[str], timeout: float) -> None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise JxlError(f"{Path(cmd[0]).name} timed out after {timeout:.0f} s: {cmd}") from exc
    if r.returncode != 0:
        raise JxlError(f"{Path(cmd[0]).name} exit {r.returncode}: {r.stderr.strip()[-400:]}")


def sidecar_path(jxl_path: Path) -> Path:
    return Path(jxl_path).with_suffix(".json")


def encode(rgb: np.ndarray, wb: Sequence[float], out: Path, *, bits: int = BITS,
           distance: float = DISTANCE, effort: int = EFFORT, pedestal: float = PEDESTAL,
           meta: Optional[dict] = None, timeout: float = TIMEOUT_S) -> dict[str, Any]:
    """Write ``out`` (.jxl) and its sidecar (.json) from linear RGB (H, W, 3); return the
    sidecar. ``meta`` (exposure, gain, crop, …) is carried through untouched."""
    rgb = np.asarray(rgb)
    if rgb.ndim != 3 or rgb.shape[2] != 3 or not np.all(np.isfinite(rgb)):
        raise ValueError(f"need finite (H, W, 3) linear RGB, got {rgb.shape}")
    out = Path(out)
    gains = headroom_gains(rgb, wb, pedestal)
    codes = to_codes(rgb, gains, bits, pedestal)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.ppm"
        write_ppm(src, codes, bits)
        _run([tool("cjxl"), str(src), str(out), "-d", str(distance), "-e", str(effort),
              "--quiet"], timeout)
    side = {"format": FORMAT, "version": VERSION, "shape": list(rgb.shape), "bits": bits,
            "curve": "sqrt", "pedestal": pedestal, "gains": gains.tolist(),
            "white_balance": [float(v) for v in wb], "distance": distance, "effort": effort,
            "below_pedestal_fraction": float(np.mean(rgb + pedestal < 0)),
            "bytes": out.stat().st_size, "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
            "meta": meta or {}}
    sidecar_path(out).write_text(json.dumps(side, indent=1) + "\n")
    return side


def decode(jxl_path: Path, sidecar: Optional[dict] = None,
           timeout: float = TIMEOUT_S) -> np.ndarray:
    """Linear RGB (float32, H × W × 3) from a ``.jxl`` + its sidecar (read from ``.json`` next
    to it unless given). The checksum and format are checked first."""
    jxl_path = Path(jxl_path)
    side = sidecar or json.loads(sidecar_path(jxl_path).read_text())
    if side.get("format") != FORMAT or side.get("version") != VERSION:
        raise JxlError(f"{jxl_path}: not a {FORMAT} v{VERSION} sidecar: "
                       f"{side.get('format')} v{side.get('version')}")
    digest = hashlib.sha256(jxl_path.read_bytes()).hexdigest()
    if digest != side["sha256"]:
        raise JxlError(f"{jxl_path}: SHA-256 {digest[:12]}… does not match the sidecar's "
                       f"{side['sha256'][:12]}… (truncated or wrong file)")
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "out.ppm"
        _run([tool("djxl"), str(jxl_path), str(dst), "--quiet"], timeout)
        codes, maxval = read_ppm(dst)
    if maxval != 2 ** side["bits"] - 1 or list(codes.shape) != side["shape"]:
        raise JxlError(f"{jxl_path}: decoded {codes.shape} maxval {maxval}, sidecar says "
                       f"{side['shape']} at {side['bits']} bits")
    return from_codes(codes, side["gains"], side["bits"], side["pedestal"])


def block_mean_error(ref: np.ndarray, test: np.ndarray, block: int = 16,
                     min_signal: float = 1e-3) -> dict[str, float]:
    """Relative error of ``block`` × ``block`` means, per channel, over blocks whose reference
    mean is at least ``min_signal`` — how much a region's colour moves in transport."""
    h, w = (ref.shape[0] // block) * block, (ref.shape[1] // block) * block

    def means(a):
        return a[:h, :w].reshape(h // block, block, w // block, block, 3).mean(axis=(1, 3))

    r, t = means(np.asarray(ref, np.float64)), means(np.asarray(test, np.float64))
    ok = r >= min_signal
    rel = np.abs(t[ok] / r[ok] - 1)
    return {"n": int(ok.sum()), "median": float(np.median(rel)),
            "p99": float(np.percentile(rel, 99)), "max": float(rel.max())}


CROP = (1600, 900)  # bmcam001's field crop (native px), centred


def roundtrip_check(frame, out_dir: Path, distances: Sequence[float] = (0.5, 1.0),
                    crop: Optional[tuple[int, int, int, int]] = None,
                    wb: Optional[Sequence[float]] = None) -> dict[str, Any]:
    """Smoke check on one RAW (a ``RawFrame``): crop (default: centred ``CROP``) → black
    subtract → 2×2 bin → encode at each distance (and lossless) → decode → compare block
    means with the input. Writes the ``.jxl`` + ``.json`` files and ``summary.json``."""
    from .raw_io import RawFrame, bin2x2, normalize

    h, w = frame.mosaic.shape
    if crop is None:
        cw, ch = min(CROP[0], w), min(CROP[1], h)
        crop = ((w - cw) // 4 * 2, (h - ch) // 4 * 2, cw, ch)
    x, y, cw, ch = crop
    if x % 2 or y % 2:
        raise ValueError(f"crop origin must be even (keeps the Bayer phase), got {crop}")
    sub = RawFrame(mosaic=frame.mosaic[y:y + ch, x:x + cw], cfa=frame.cfa,
                   black_level=frame.black_level, white_level=frame.white_level)
    linear, _, cfa = normalize(sub)
    rgb = bin2x2(linear, cfa)
    rgb = rgb[0] if isinstance(rgb, tuple) else rgb
    if wb is None:  # as-shot WB if the file has one, else grey world — only sets code spacing
        wb = (np.asarray(frame.as_shot_wb) if frame.as_shot_wb
              else rgb.reshape(-1, 3).mean(0)[1] / np.maximum(rgb.reshape(-1, 3).mean(0), 1e-9))
    meta = {"source": str(frame.source.get("path", "")), "cfa": frame.cfa, "crop": list(crop),
            "binning": "2x2", "black_level": list(frame.black_level),
            "white_level": frame.white_level, "exposure_s": frame.exposure_s,
            "iso": frame.iso, "fnumber": frame.fnumber}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    runs = {}
    for d in (0.0, *distances):
        name = "lossless" if d == 0 else f"d{d:g}"
        side = encode(rgb, wb, out_dir / f"{name}.jxl", distance=d, meta=meta)
        back = decode(out_dir / f"{name}.jxl")
        runs[name] = {"bytes": side["bytes"],
                      "block_mean_error": {k: round(v, 5) if isinstance(v, float) else v
                                           for k, v in block_mean_error(rgb, back).items()}}
    summary = {"shape": list(rgb.shape), "raw_bytes_packed": cw * ch * 12 // 8,
               "white_balance": [float(v) for v in wb], "runs": runs}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    return summary
