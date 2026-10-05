"""Card-metered exposure for RAW captures — SPEC §4 Phase 8 S3 (locked-exposure recipe).

Scene metering puts the card wherever the rest of the frame allows: in air on ``nereus002``
(2026-09-28) the V1 card's white landed at 0.57–0.64 of full scale on the 8-bit OpenMV RAW
(mid grey ~42 counts) while a window clipped; under water a small card in dark water pushes
the other way and clips the white. The recipe meters once, finds the card on that RAW, and
scales the exposure so the card's brightest channel lands on a target fraction of full
scale. Linear RAW makes this one division — no iteration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .raw_io import RawFrame, bin2x2, normalize

DEFAULT_TARGET = 0.80  # brightest card channel, fraction of full scale: headroom for uneven light
WHITE_PATCHES = ("gray_white", "gray_light")  # brightest first; the first one found is used


# Mosaic px for metering. A 12 MP IMX708 DNG OOM-killed the Zero 2 W (415 MB RAM); most of the
# cost is OpenCV's tag search on locate's 2x pass for small frames (+60 MB at 3 MP, +100 MB at
# 5 MP, measured on nereus002). 1.1 MP leaves the 1 MP OpenMV frames as they are and takes
# the IMX708 to every 4th cell (0.75 MP): whole-locate peak ~146 MB with no card in view.
MAX_METER_PIXELS = 1_100_000


def decimate_cells(frame: RawFrame, max_pixels: int = MAX_METER_PIXELS) -> RawFrame:
    """Keep every k-th 2x2 CFA cell in each direction until the mosaic has at most
    ``max_pixels`` — same CFA phase and levels, k^2 x less memory. For metering only: a V1
    colour patch is ~140 px on the full-res IMX708, ~35 px at k = 4 (12 MP -> 0.75 MP)."""
    k = 1
    h, w = frame.mosaic.shape
    while (h // (2 * k) * 2) * (w // (2 * k) * 2) > max_pixels:
        k += 1
    if k == 1:
        return frame
    x, y, cw, ch = frame.valid_crop or (0, 0, w, h)
    m = frame.mosaic[y:y + ch // 2 * 2, x:x + cw // 2 * 2]
    cells = m.reshape(m.shape[0] // 2, 2, m.shape[1] // 2, 2)[::k, :, ::k, :]
    sub = cells.reshape(cells.shape[0] * 2, cells.shape[2] * 2)
    black = np.roll(np.asarray(frame.black_level).reshape(2, 2), (-(y % 2), -(x % 2)), (0, 1))
    return RawFrame(mosaic=np.ascontiguousarray(sub), cfa=frame.active()[1],
                    black_level=tuple(float(v) for v in black.ravel()),
                    white_level=frame.white_level, exposure_s=frame.exposure_s,
                    source={**frame.source, "decimated_cells": k})


def card_levels(frame: RawFrame, card) -> dict[str, Any]:
    """Locate ``card`` on ``frame``'s RAW and return its patch means (binned linear camera RGB,
    0 = black, 1 = white level) plus clip fractions. Raises ``ValueError`` if not located.
    Large frames are decimated first (``decimate_cells``); the quad is in decimated px."""
    frame = decimate_cells(frame)
    from .jpeg_geometry import JpegMap
    from .locate import locate_frame, tag_geometry, tag_spec
    from .patches import _boxes, homography, mosaic_to_binned, sample

    geometry = tag_geometry(card) if card.physically_measured else None
    rec = locate_frame(Path(str(frame.source.get("path", "frame"))), None, card.corner_map,
                       JpegMap.offset(0, 0), raw_reader=lambda _path: frame,
                       geometry=geometry, spec=tag_spec(card))
    if not rec["located"]:
        raise ValueError(f"card not found: {rec.get('reason')}")
    linear, saturated, cfa = normalize(frame)
    binned, clip = bin2x2(linear, cfa, saturated)
    H = mosaic_to_binned(frame.valid_crop) @ homography(card, np.asarray(rec["quad_raw"]))
    patches = {}
    for pid, box in _boxes(card).items():
        st = sample(binned, H, box, clip=clip)
        if st.get("mean"):
            patches[pid] = {"mean": [round(float(v), 5) for v in st["mean"]],
                            "clip_frac": st.get("clip_frac"), "n_px": st.get("n_px")}
    return {"tags_found": rec["tags_found"], "quad_raw": rec["quad_raw"], "patches": patches}


def exposure_for_target(exposure_us: float, level: float, target: float = DEFAULT_TARGET,
                        clipped: bool = False, lo_us: float = 80.0,
                        hi_us: float = 1_000_000.0) -> dict[str, Any]:
    """Exposure that moves a linear ``level`` (fraction of full scale, measured at
    ``exposure_us``) to ``target``. A clipped reference hides its true level, so the
    exposure is halved instead (one more meter shot is then needed). Clamped to
    [``lo_us``, ``hi_us``] — the OpenMV PAG7936 floor is 80 us (OQ-21); the sensor may clamp
    long exposures to its frame time, which the capture's read-back reveals."""
    if level <= 0:
        raise ValueError(f"card level {level} at {exposure_us} us: nothing to meter on")
    if clipped:
        want, rule = exposure_us / 2, "reference clipped: halve and re-meter"
    else:
        want, rule = exposure_us * target / level, "linear: exposure x target / level"
    got = float(min(max(want, lo_us), hi_us))
    return {"exposure_us": round(got), "wanted_us": round(want), "rule": rule,
            "clamped": got != want, "remeter": clipped}


def gain_priority(exposure_us: float, cap_us: float | None, min_gain: float,
                  max_gain: float) -> dict[str, Any]:
    """Nick's "ISO 100" rule (2026-10-05): lowest analogue gain first, lengthen the shutter up to
    ``cap_us`` (motion limit, e.g. 1/60 s), and only then raise the gain. ``exposure_us`` is the
    exposure that hits the target at ``min_gain`` (linear). Same total exposure (shutter × gain)
    either way, because the RAW is linear. Gains are linear factors; the sensor rounds to its
    own steps, which the capture's read-back reports."""
    if not cap_us or exposure_us <= cap_us:
        return {"exposure_us": round(exposure_us), "gain": min_gain, "capped": False,
                "rule": "gain floor, shutter only"}
    need = min_gain * exposure_us / cap_us
    gain = min(need, max_gain)
    return {"exposure_us": round(cap_us), "gain": gain, "capped": True,
            "wanted_us": round(exposure_us), "gain_needed": round(need, 4),
            "gain_clamped": gain < need,
            "rule": "shutter at the motion cap, the rest in gain"}


def card_reference(levels: dict[str, Any]) -> dict[str, Any]:
    """The brightest white-ish patch's brightest channel: its level and whether it clipped."""
    for pid in WHITE_PATCHES:
        p = levels["patches"].get(pid)
        if p:
            k = int(np.argmax(p["mean"]))
            clip = (p.get("clip_frac") or [0, 0, 0])[k]
            return {"patch": pid, "channel": "RGB"[k], "level": p["mean"][k],
                    "clipped": bool(clip and clip > 0.01), "clip_frac": clip}
    raise ValueError(f"none of {WHITE_PATCHES} sampled on the card")
