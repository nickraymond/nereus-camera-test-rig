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


def card_levels(frame: RawFrame, card) -> dict[str, Any]:
    """Locate ``card`` on ``frame``'s RAW and return its patch means (binned linear camera RGB,
    0 = black, 1 = white level) plus clip fractions. Raises ``ValueError`` if not located."""
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
