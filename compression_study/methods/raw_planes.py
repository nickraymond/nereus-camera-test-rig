"""The raw-plane methods: split the Bayer mosaic into planes, optionally apply a curve,
code each plane with a grayscale codec, and invert on decode.

| method | curve | bits | codec | lossy |
|---|---|---|---|---|
| C      | none   | native | packer (MED + adaptive Golomb-Rice) | no |
| C-jls  | none   | native | JPEG-LS (CharLS) | no |
| C-png  | none   | native | PNG | no |
| C2     | none   | native | JPEG XL ``-d 0`` (effort 3 / 7) | no |
| N      | sqrt   | 7–10   | packer | yes (rounding of the curve only) |
| D      | sqrt   | 8      | JPEG (libjpeg-turbo) | yes |
| D-j    | sqrt   | 8      | jpegli | yes |
| D2     | sqrt   | 12     | JPEG XL lossy | yes |
| D-lin / D2-lin | linear | 8 / native | JPEG / JPEG XL | yes (OpenMV ablation: no curve) |

Layouts: ``4pl`` (R, G1, G2, B separately), ``3pl`` (G = (G1 + G2 + 1) >> 1, duplicated on
decode), ``tiled`` (the four planes as one 2×2-tiled image). ``knobs`` maps plane name →
codec knob (``T`` for tiled).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from ..common import Header, Raw, forward, inverse, merge, pack, split, tile, unpack, untile
from . import plane_codecs as pc

CODEC_OF = {"C": "packer", "C-jls": "jls", "C-png": "png", "C2": "jxl", "N": "packer",
            "D": "jpeg", "D-j": "jpegli", "D2": "jxl", "D-lin": "jpeg", "D2-lin": "jxl"}
CURVES = ("none", "sqrt", "linear")
LAYOUTS = ("4pl", "3pl", "tiled")


@dataclass(frozen=True)
class RawSpec:
    method: str
    curve: str = "none"
    b: int = 0  # code bits; 0 = the frame's native bits
    layout: str = "4pl"
    mode: str = "vardct"  # JPEG XL mode (encoder only, not needed to decode)
    effort: int = 7
    pedestal: int = 0
    variant: str = "eq"  # label only (eq / red+1.5 / red+2 / 3pl / tiled)
    binned: bool = False  # 2×2 block-mean each plane before coding (IMX708 field row)

    @property
    def codec(self) -> str:
        return CODEC_OF[self.method]

    def label(self) -> str:
        parts = [self.method]
        if self.method == "N":
            parts.append(f"b{self.b}")
        if self.codec == "jxl" and self.method != "C2":
            parts.append(self.mode)
        if self.method == "C2":
            parts.append(f"e{self.effort}")
        if self.pedestal:
            parts.append(f"ped{self.pedestal}")
        if self.variant != "eq":
            parts.append(self.variant)
        if self.binned:
            parts.append("bin2")
        return "/".join(parts)


def bits_for(spec: RawSpec, raw: Raw) -> int:
    return spec.b or raw.bits


def code_planes(raw: Raw, spec: RawSpec) -> tuple[dict[str, np.ndarray], int]:
    """{plane: integer codes} as transmitted, and maxval."""
    b = bits_for(spec, raw)
    planes = split(raw.mosaic, raw.cfa)
    if spec.binned:
        planes = {k: np.floor(_bin2(v) + 0.5).astype(np.uint16) for k, v in planes.items()}
    if spec.layout == "3pl":
        g = (planes["G1"].astype(np.uint32) + planes["G2"] + 1) >> 1
        planes = {"R": planes["R"], "G": g.astype(np.uint16), "B": planes["B"]}
    if spec.curve == "none":
        maxval = raw.white if spec.codec in ("packer", "jls", "png") else 2 ** b - 1
        codes = planes
    else:
        maxval = 2 ** b - 1
        codes = {k: forward(v, spec.curve, raw.black, raw.white, b, spec.pedestal)
                 for k, v in planes.items()}
    if spec.layout == "tiled":
        codes = {"T": tile(codes)}
    return codes, maxval


def _bin2(p: np.ndarray) -> np.ndarray:
    h, w = p.shape[0] // 2 * 2, p.shape[1] // 2 * 2
    return p[:h, :w].astype(np.float64).reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))


def encode_plane(codes: np.ndarray, maxval: int, spec: RawSpec, knob=None,
                 measure_rss: bool = False):
    """(bytes, RunStats|None) for one plane."""
    c = spec.codec
    if c == "packer":
        return pc.packer_enc(codes, maxval)
    if c == "jls":
        return pc.jls_enc(codes, maxval)
    if c == "png":
        return pc.png_enc(codes, maxval)
    if c == "jxl":
        return pc.jxl_enc(codes, maxval, 0.0 if spec.method == "C2" else float(knob),
                          spec.mode, spec.effort, measure_rss)
    if c == "jpeg":
        return pc.jpeg_enc(codes, maxval, int(knob), measure_rss)
    if c == "jpegli":
        return pc.jpegli_enc(codes, maxval, int(knob), measure_rss)
    raise ValueError(f"unknown codec {c}")


def header_for(raw: Raw, spec: RawSpec) -> Header:
    flags = LAYOUTS.index(spec.layout) | (CURVES.index(spec.curve) << 2) | (16 * spec.binned)
    return Header(spec.method, raw.shape[1], raw.shape[0], raw.cfa, raw.black, raw.white,
                  bits_for(spec, raw), spec.pedestal, flags)


def assemble(raw: Raw, spec: RawSpec, payloads: dict[str, bytes]) -> bytes:
    order = {"4pl": ("R", "G1", "G2", "B"), "3pl": ("R", "G", "B"), "tiled": ("T",)}
    return pack(header_for(raw, spec), [payloads[k] for k in order[spec.layout]])


def encode(raw: Raw, spec: RawSpec, knobs: dict | None = None) -> bytes:
    codes, maxval = code_planes(raw, spec)
    knobs = knobs or {}
    payloads = {k: encode_plane(v, maxval, spec, knobs.get(k))[0] for k, v in codes.items()}
    return assemble(raw, spec, payloads)


def decode(blob: bytes) -> np.ndarray:
    """Bayer mosaic (float64, sensor counts with black restored) from the bytes alone."""
    h, payloads = unpack(blob)
    layout, curve = LAYOUTS[h.flags & 3], CURVES[(h.flags >> 2) & 3]
    codec = CODEC_OF[h.method]
    pw, ph = h.w // 2, h.h // 2
    if codec == "packer":
        names = {"4pl": ("R", "G1", "G2", "B"), "3pl": ("R", "G", "B")}[layout]
        dec = pc.packer_dec_factory(pw, ph, h.b)
        planes = {k: dec(p) for k, p in zip(names, payloads)}
    else:
        dec = {"jls": pc.jls_dec, "png": pc.png_dec, "jxl": pc.jxl_dec, "jpeg": pc.jpeg_dec,
               "jpegli": pc.jpegli_dec}[codec]
        if layout == "tiled":
            planes = untile(dec(payloads[0]))
        else:
            names = {"4pl": ("R", "G1", "G2", "B"), "3pl": ("R", "G", "B")}[layout]
            planes = {k: dec(p) for k, p in zip(names, payloads)}
    if h.flags & 16:  # binned: back to plane size by bilinear interpolation
        import cv2
        planes = {k: cv2.resize(v.astype(np.float32), (pw, ph), interpolation=cv2.INTER_LINEAR)
                  for k, v in planes.items()}
    if "G" in planes:
        planes = {"R": planes["R"], "G1": planes["G"], "G2": planes["G"], "B": planes["B"]}
    if curve == "none":
        vals = {k: v.astype(np.float64) for k, v in planes.items()}
    else:
        vals = {k: inverse(v, curve, h.black, h.white, h.b, h.pedestal)
                for k, v in planes.items()}
    return merge(vals, h.cfa)


def with_variant(spec: RawSpec, variant: str) -> RawSpec:
    layout = {"3pl": "3pl", "tiled": "tiled"}.get(variant, "4pl")
    return replace(spec, variant=variant, layout=layout)
