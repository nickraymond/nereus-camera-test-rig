"""Processed-image baselines: a shared, documented ISP → 8-bit sRGB → an image codec, and the
exact inverse of the known ISP steps ("M1-recovered raw").

Shared ISP (spec 4.2): subtract black, normalize to 0..1 of white, bilinear demosaic
(``color.raw_io.demosaic_bilinear``, which keeps every site's own value), white balance,
(M1-fix only: a 3×3 colour matrix to linear sRGB), sRGB OETF, round to 8 bits (clip 0..1).

Inverse: decode → inverse OETF → (inverse matrix) → ÷ WB → remosaic by sampling each Bayer
site's own channel → × (white − black) + black. With no codec in between, the error is only
the 8-bit rounding and the clip — the ISP floor, reported as ``M1-q∞``.

| method | WB | matrix | codec |
|---|---|---|---|
| M1     | card grey of this frame | — | JPEG 4:2:0, libjpeg-turbo |
| M1-444 | same | — | JPEG 4:4:4 |
| M1j    | same | — | jpegli 4:2:0 |
| M1-fix | fixed (the in-air frame's grey) | camera → sRGB (CCM fitted in air) | JPEG 4:2:0 |
| M2     | same as M1 | — | H.264 intra, 1 frame, Main, 4:2:0 (x264) |
| M2h    | same as M1 | — | HEIC 4:2:0 (libheif / x265) |

WB gains and the matrix travel in the header as Q12 integers; encode uses the same
quantized values, so decode inverts exactly what was applied.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from nereus_camera_test_rig.color.raw_io import demosaic_bilinear

from ..common import (
    Header,
    Raw,
    merge,
    pack,
    read_pnm,
    run,
    split,
    srgb_eotf,
    srgb_oetf,
    tool,
    unpack,
    write_ppm,
)

Q = 4096  # Q12
CHAN = {"R": 0, "G1": 1, "G2": 1, "B": 2}
TIMEOUT = 900


def q12(values) -> list[int]:
    return [int(round(float(v) * Q)) for v in np.ravel(values)]


def render(raw: Raw, wb, matrix: Optional[np.ndarray] = None) -> np.ndarray:
    """(H, W, 3) uint8 sRGB through the shared ISP. ``wb`` (R, G, B) multiplies linear."""
    lin = (raw.mosaic.astype(np.float32) - raw.black) / np.float32(raw.white - raw.black)
    rgb = demosaic_bilinear(lin, raw.cfa).astype(np.float64)
    rgb *= np.asarray(wb, dtype=np.float64)
    if matrix is not None:
        rgb = apply3(rgb, matrix)
    return np.floor(srgb_oetf(rgb) * 255 + 0.5).astype(np.uint8)


def clip_fractions(raw: Raw, wb, matrix: Optional[np.ndarray] = None) -> dict[str, float]:
    """Fraction of pixels the ISP clips before 8-bit coding: below 0 (the colour matrix
    pushing weak red negative — the TG-7 failure) and above 1, per channel."""
    lin = (raw.mosaic.astype(np.float32) - raw.black) / np.float32(raw.white - raw.black)
    rgb = demosaic_bilinear(lin, raw.cfa).astype(np.float64) * np.asarray(wb, dtype=np.float64)
    if matrix is not None:
        rgb = apply3(rgb, matrix)
    out = {}
    for i, ch in enumerate("RGB"):
        out[f"isp_clip0_{ch}"] = float(np.mean(rgb[..., i] < 0))
        out[f"isp_clip1_{ch}"] = float(np.mean(rgb[..., i] > 1))
    return out


def apply3(img: np.ndarray, matrix) -> np.ndarray:
    """img (..., 3) @ matrix.T on a contiguous (N, 3) copy. numpy 2.5.1 segfaults in matmul on
    the strided array ``demosaic_bilinear`` returns (reproduced 2026-09-30)."""
    flat = np.ascontiguousarray(img, dtype=np.float64).reshape(-1, 3)
    m = np.ascontiguousarray(np.asarray(matrix, dtype=np.float64).T)
    return (flat @ m).reshape(img.shape)


def unrender(rgb8: np.ndarray, cfa: str, black: int, white: int, wb,
             matrix: Optional[np.ndarray] = None) -> np.ndarray:
    """Inverse of ``render`` sampled back onto the Bayer grid (float64 sensor counts)."""
    lin = srgb_eotf(rgb8.astype(np.float64) / 255)
    if matrix is not None:
        lin = apply3(lin, np.linalg.inv(np.asarray(matrix, dtype=np.float64)))
    lin /= np.asarray(wb, dtype=np.float64)
    planes = {k: lin[..., c] for k, c in CHAN.items()}
    sites = {k: v for k, v in split_rgb(planes, cfa).items()}
    return merge(sites, cfa) * (white - black) + black


def split_rgb(full: dict[str, np.ndarray], cfa: str) -> dict[str, np.ndarray]:
    """Per Bayer site, its own channel of a full-resolution image."""
    return {k: split(full[k], cfa)[k] for k in ("R", "G1", "G2", "B")}


# ------------------------------------------------------------------ RGB codecs

def _jpeg_rgb(rgb8, quality, sample, measure_rss=False):
    with tempfile.TemporaryDirectory(prefix="nrcs_") as d:
        src, dst = Path(d) / "in.ppm", Path(d) / "o.jpg"
        write_ppm(src, rgb8)
        _, st = run([tool("cjpeg"), "-quality", str(int(quality)), "-sample", sample,
                     "-optimize", "-outfile", str(dst), str(src)], timeout=TIMEOUT,
                    measure_rss=measure_rss)
        return dst.read_bytes(), st


def _jpeg_rgb_dec(data):
    out, _ = run([tool("djpeg"), "-pnm"], stdin=data, timeout=TIMEOUT)
    with tempfile.TemporaryDirectory(prefix="nrcs_") as d:
        p = Path(d) / "o.ppm"
        p.write_bytes(out)
        return read_pnm(p).astype(np.uint8)


def _jpegli_rgb(rgb8, quality, measure_rss=False):
    with tempfile.TemporaryDirectory(prefix="nrcs_") as d:
        src, dst = Path(d) / "in.ppm", Path(d) / "o.jpg"
        write_ppm(src, rgb8)
        _, st = run([tool("cjpegli"), str(src), str(dst), "-q", str(int(quality)),
                     "--chroma_subsampling=420"], timeout=TIMEOUT,
                    measure_rss=measure_rss)
        return dst.read_bytes(), st


def _jpegli_rgb_dec(data):
    with tempfile.TemporaryDirectory(prefix="nrcs_") as d:
        src, dst = Path(d) / "i.jpg", Path(d) / "o.ppm"
        src.write_bytes(data)
        run([tool("djpegli"), str(src), str(dst)], timeout=TIMEOUT)
        return read_pnm(dst).astype(np.uint8)


def _h264(rgb8, crf, measure_rss=False):
    h, w, _ = rgb8.shape
    cmd = [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-i", "-", "-frames:v", "1",
           "-c:v", "libx264", "-profile:v", "main", "-preset", "medium", "-tune", "psnr",
           "-crf", f"{float(crf):.3f}", "-pix_fmt", "yuv420p", "-threads", "1",
           "-bsf:v", "filter_units=remove_types=6", "-f", "h264", "-"]
    return run(cmd, stdin=rgb8.tobytes(), timeout=TIMEOUT, measure_rss=measure_rss)


def _h264_dec(data, w, h):
    out, _ = run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "h264",
                  "-i", "-", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdin=data,
                 timeout=TIMEOUT)
    return np.frombuffer(out, np.uint8)[: w * h * 3].reshape(h, w, 3)


def _heic(rgb8, quality, measure_rss=False):
    import pillow_heif  # study-only (GPL via x265): internal benchmark, never shipped
    from PIL import Image

    pillow_heif.register_heif_opener()
    buf = io.BytesIO()
    import time
    t0 = time.perf_counter()
    Image.fromarray(rgb8).save(buf, format="HEIF", quality=int(quality), chroma=420)
    from ..common import RunStats
    return buf.getvalue(), RunStats(time.perf_counter() - t0)


def _heic_dec(data):
    import pillow_heif
    hf = pillow_heif.open_heif(io.BytesIO(data), convert_hdr_to_8bit=True)
    return np.asarray(hf[0].to_pillow().convert("RGB"))


CODECS = {
    "M1": (lambda img, k, m=False: _jpeg_rgb(img, k, "2x2", m), lambda d, w, h: _jpeg_rgb_dec(d)),
    "M1-444": (lambda img, k, m=False: _jpeg_rgb(img, k, "1x1", m),
               lambda d, w, h: _jpeg_rgb_dec(d)),
    "M1j": (_jpegli_rgb, lambda d, w, h: _jpegli_rgb_dec(d)),
    "M1-fix": (lambda img, k, m=False: _jpeg_rgb(img, k, "2x2", m),
               lambda d, w, h: _jpeg_rgb_dec(d)),
    "M2": (_h264, _h264_dec),
    "M2h": (_heic, lambda d, w, h: _heic_dec(d)),
}
# knob: (direction, lo, hi, integer?) — direction +1: bigger knob → more bytes
KNOBS = {"M1": (+1, 1, 100, True), "M1-444": (+1, 1, 100, True), "M1j": (+1, 1, 100, True),
         "M1-fix": (+1, 1, 100, True), "M2": (-1, 1.0, 51.0, False),
         "M2h": (+1, 0, 100, True)}


def encode_image(rgb8: np.ndarray, raw: Raw, method: str, knob, wb_q: list[int],
                 m_q: Optional[list[int]] = None, measure_rss: bool = False,
                 resized: bool = False):
    """(bytes, RunStats) for an already-rendered image (renders are cached by the caller).
    ``resized``: the image was downscaled (IMX708 field row); decode scales it back up."""
    data, st = CODECS[method][0](rgb8, knob, measure_rss)
    h = Header(method, raw.shape[1], raw.shape[0], raw.cfa, raw.black, raw.white, 8, 0,
               int(resized), params=list(wb_q) + list(m_q or []))
    return pack(h, [data]), st


def quantized(wb, matrix=None) -> tuple[list[int], Optional[list[int]], np.ndarray,
                                           Optional[np.ndarray]]:
    wb_q = q12(wb)
    m_q = q12(matrix) if matrix is not None else None
    return (wb_q, m_q, np.asarray(wb_q, float) / Q,
            None if m_q is None else np.asarray(m_q, float).reshape(3, 3) / Q)


def decode(blob: bytes) -> np.ndarray:
    h, (data,) = unpack(blob)
    if h.flags & 1:  # field row: decoded at the sent size, scaled back to the sensor crop
        if h.method not in ("M1", "M1-444", "M1-fix"):
            raise ValueError(f"resized {h.method} not supported")
        small = CODECS[h.method][1](data, 0, 0)  # JPEG decoders read the size from the stream
        rgb8 = cv2.resize(small.astype(np.float32), (h.w, h.h), interpolation=cv2.INTER_LINEAR)
        rgb8 = np.clip(rgb8, 0, 255)
    else:
        rgb8 = CODECS[h.method][1](data, h.w, h.h)
    wb = np.asarray(h.params[:3], float) / Q
    m = np.asarray(h.params[3:12], float).reshape(3, 3) / Q if len(h.params) >= 12 else None
    return unrender(rgb8, h.cfa, h.black, h.white, wb, m)


def resize_area(img: np.ndarray, w: int, h: int) -> np.ndarray:
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
