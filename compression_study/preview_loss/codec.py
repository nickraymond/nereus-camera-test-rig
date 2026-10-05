"""Frames, the production nrjxl planes, cjxl/djxl wrappers, rendering and metrics.

The production encoder is bm_cam_legacy #120 ``rc_raw_jxl`` (head e44dde5), imported from a
read-only export (``--bm`` dir with rc_raw_jxl.py, config_registry.py, config_validate.py):
``read_dng_crop`` -> ``code_planes`` (12-bit sqrt codes) -> ``cjxl_command`` flags (modular,
effort 5, one thread) -> ``seal_container``. Nothing here is imported from ``src/``'s capture path.
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

from compression_study import common
from compression_study.metrics import ssim

MSG_B = 288            # payload bytes per message (384 base64 chars)
CJXL, DJXL = common.tool("cjxl"), common.tool("djxl")
EFFORT = 5             # still.raw.effort (registry default)
_RC = None


def rc():
    global _RC
    if _RC is None:
        bm = os.environ.get("NRJXL_BM_DIR")
        if not bm:
            raise SystemExit("set NRJXL_BM_DIR to the bm #120 rc_raw_jxl export")
        sys.path.insert(0, bm)
        import rc_raw_jxl  # noqa: E402
        _RC = rc_raw_jxl
    return _RC


# ------------------------------------------------------------------ frames

def load_frame(dng: Path, xywh) -> dict:
    """1600x900 native crop -> production 12-bit sqrt code planes {R, G1, G2, B} (800x450)."""
    crop = rc().read_dng_crop(str(dng), tuple(int(v) for v in xywh))
    return {"crop": crop, "codes": rc().code_planes(crop), "xywh": list(xywh)}


def to_linear(codes: np.ndarray, crop: dict) -> np.ndarray:
    s = (2 ** 12 - 1) / math.sqrt(crop["white"] - crop["black"])
    return (np.asarray(codes, np.float64) / s) ** 2 / (crop["white"] - crop["black"])


class Renderer:
    """Planes -> 8-bit sRGB at plane resolution (R, mean G, B), one WB + exposure per frame,
    fixed from the original so every method is shown the same way."""

    def __init__(self, frame: dict):
        self.crop = frame["crop"]
        rgb = self._rgb(frame["codes"])
        m = np.median(rgb.reshape(-1, 3), 0)
        self.wb = (m[1] / np.maximum(m, 1e-6))
        self.scale = 0.9 / max(float(np.percentile(rgb[..., 1], 99.5)), 1e-6)
        self.ref = self.render(frame["codes"])

    def _rgb(self, p):
        lin = {k: to_linear(v, self.crop) for k, v in p.items()}
        return np.dstack([lin["R"], (lin["G1"] + lin["G2"]) / 2, lin["B"]])

    def render(self, planes) -> np.ndarray:
        rgb = self._rgb(planes) * self.wb * self.scale
        return np.round(common.srgb_oetf(rgb) * 255).astype(np.uint8)

    def render_grey(self, g: np.ndarray) -> np.ndarray:
        v = to_linear(g, self.crop) * self.scale
        return np.repeat(np.round(common.srgb_oetf(v) * 255).astype(np.uint8)[..., None], 3, 2)


def _luma(u8):
    return (u8.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32))


def _lab_blocks(u8, b=8):
    from nereus_camera_test_rig.color.metrics import linear_to_lab
    h, w = (u8.shape[0] // b) * b, (u8.shape[1] // b) * b
    lin = common.srgb_eotf(u8[:h, :w].astype(np.float64) / 255)
    m = lin.reshape(h // b, b, w // b, b, 3).mean((1, 3))
    return linear_to_lab(m)


def score(img: np.ndarray | None, ref: np.ndarray) -> dict:
    """SSIM on luma at the reference (800x450) resolution, and median / p90 CIEDE2000 of
    8x8-block means. img None -> nothing shown."""
    if img is None:
        return {"visible": False}
    from nereus_camera_test_rig.color.metrics import delta_e2000
    if img.shape[:2] != ref.shape[:2]:
        img = cv2.resize(img, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_LINEAR)
    de = delta_e2000(_lab_blocks(img), _lab_blocks(ref))
    return {"visible": True, "ssim": round(ssim(_luma(img), _luma(ref)), 4),
            "de_med": round(float(np.median(de)), 2), "de_p90": round(float(np.percentile(de, 90)), 2)}


# ------------------------------------------------------------------ cjxl / djxl

def _pnm(path: Path, img: np.ndarray, maxval: int):
    if img.ndim == 2:
        common.write_pgm(path, img, maxval)
    else:
        common.write_ppm(path, img, maxval)


def encode(img: np.ndarray, maxval: int, d: float, extra=(), effort: int = EFFORT,
           modular: bool = True) -> bytes:
    with tempfile.TemporaryDirectory() as t:
        src, dst = Path(t) / ("i.pgm" if img.ndim == 2 else "i.ppm"), Path(t) / "o.jxl"
        _pnm(src, img, maxval)
        cmd = [CJXL, str(src), str(dst), "-e", str(effort), "-d", f"{d:.4f}", "--num_threads=0"]
        if modular:
            cmd[3:3] = ["-m", "1"]
        subprocess.run(cmd + list(extra), check=True, capture_output=True)
        return dst.read_bytes()


def decode(blob: bytes, partial: bool = False) -> np.ndarray | None:
    with tempfile.TemporaryDirectory() as t:
        src, dst = Path(t) / "i.jxl", Path(t) / "o.pnm"
        src.write_bytes(blob)
        cmd = [DJXL, str(src), str(dst), "--num_threads=0"]
        if partial:
            cmd.append("--allow_partial_files")
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0 or not dst.exists():
            return None
        return common.read_pnm(dst)


def fit(make, target: int, lo: float = 0.05, hi: float = 25.0, iters: int = 14):
    """Largest output of make(d) with len <= target (bisection on log d). -> (d, blob)."""
    best = None
    a, b = math.log(lo), math.log(hi)
    for _ in range(iters):
        m = (a + b) / 2
        blob = make(math.exp(m))
        if len(blob) <= target:
            best = (round(math.exp(m), 4), blob)
            b = m
        else:
            a = m
    if best is None:
        raise ValueError(f"nothing fits {target} B")
    return best


def chunks(n_bytes: int) -> int:
    return -(-int(n_bytes) // MSG_B)
