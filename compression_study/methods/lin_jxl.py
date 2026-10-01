"""Method L — the repo's existing linear JPEG XL transport (``color/linear_jxl.py``, 2026-09-28)
as its own row: 2×2-binned linear RGB (R, mean(G1, G2), B) → white-balance gains scaled so
nothing clips → sqrt with a pedestal → 10-bit RGB JPEG XL.

Reuses the prototype's own ``headroom_gains`` / ``to_codes`` / ``from_codes``; only the
sidecar is replaced by this study's compact header (gains and pedestal as Q16 integers), so
the counted bytes are what a field unit would actually send. Decode puts the binned colours
back on the Bayer sites (both greens get the binned green, like the 3pl variant).
"""

from __future__ import annotations

import numpy as np

from nereus_camera_test_rig.color.linear_jxl import (
    BITS,
    PEDESTAL,
    from_codes,
    headroom_gains,
    to_codes,
)
from nereus_camera_test_rig.color.raw_io import bin2x2

from ..common import Header, Raw, merge, pack, unpack
from . import plane_codecs as pc

Q16 = 65536


def binned_linear(raw: Raw) -> np.ndarray:
    lin = (raw.mosaic.astype(np.float32) - raw.black) / np.float32(raw.white - raw.black)
    rgb, _ = bin2x2(lin, raw.cfa)
    return rgb.astype(np.float64)


def encode(raw: Raw, wb, distance: float, rgb: np.ndarray | None = None,
           measure_rss: bool = False):
    rgb = binned_linear(raw) if rgb is None else rgb
    gains = headroom_gains(rgb, wb, PEDESTAL)
    gq = [int(round(g * Q16)) for g in gains]
    gains = np.asarray(gq, float) / Q16
    codes = to_codes(rgb, gains, BITS, PEDESTAL)
    data, st = _jxl_rgb(codes, distance, measure_rss)
    h = Header("L", raw.shape[1], raw.shape[0], raw.cfa, raw.black, raw.white, BITS, 0, 0,
               params=gq + [int(round(PEDESTAL * Q16))])
    return pack(h, [data]), st


def _jxl_rgb(codes: np.ndarray, distance: float, measure_rss: bool):
    import tempfile
    from pathlib import Path

    from ..common import run, tool, write_ppm
    with tempfile.TemporaryDirectory(prefix="nrcs_") as d:
        src, dst = Path(d) / "in.ppm", Path(d) / "o.jxl"
        write_ppm(src, codes, 2 ** BITS - 1)
        _, st = run([tool("cjxl"), str(src), str(dst), "-d", f"{distance:.4f}", "-e", "7",
                     "--num_threads=0"], timeout=pc.TIMEOUT, measure_rss=measure_rss)
        return dst.read_bytes(), st


def decode(blob: bytes) -> np.ndarray:
    import tempfile
    from pathlib import Path

    from ..common import read_pnm, run, tool
    h, (data,) = unpack(blob)
    with tempfile.TemporaryDirectory(prefix="nrcs_") as d:
        src, dst = Path(d) / "i.jxl", Path(d) / "o.ppm"
        src.write_bytes(data)
        run([tool("djxl"), str(src), str(dst), "--num_threads=0"], timeout=pc.TIMEOUT)
        codes = read_pnm(dst)
    gains = np.asarray(h.params[:3], float) / Q16
    rgb = from_codes(codes, gains, h.b, h.params[3] / Q16)
    planes = {"R": rgb[..., 0], "G1": rgb[..., 1], "G2": rgb[..., 1], "B": rgb[..., 2]}
    return merge(planes, h.cfa) * (h.white - h.black) + h.black
