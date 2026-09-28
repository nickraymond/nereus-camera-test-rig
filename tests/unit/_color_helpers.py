"""Shared synthetic-RAW helpers for the Phase 8 color tests (like ``_capture_helpers``)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile

LONG, SHORT, BYTE, RATIONAL, SRATIONAL = 4, 3, 1, 5, 10
FLAT = (0.2, 0.45, 0.1)
CH = {"R": 0, "G": 1, "B": 2}


def flat_mosaic(cfa="GRBG", shape=(12, 16), black=(256, 257, 258, 259), white=4095):
    out = np.zeros(shape, dtype=np.uint16)
    for i, color in enumerate(cfa):
        r, c = divmod(i, 2)
        out[r::2, c::2] = round(black[i] + FLAT[CH[color]] * (white - black[i]))
    return out


def dng_tags(cfa_codes=(1, 0, 2, 1), black=None, active=None, extra=()):
    tags = [
        (33421, SHORT, 2, (2, 2), True),
        (33422, BYTE, 4, bytes(cfa_codes), True),
        (50713, SHORT, 2, (2, 2), True),
        (50714, LONG, 4, black or (256, 257, 258, 259), True),
        (50717, LONG, 1, 4095, True),
        (50728, RATIONAL, 3, (1, 2, 1, 1, 2, 3), True),  # AsShotNeutral 0.5, 1, 0.667
        (50721, SRATIONAL, 9, (1, 1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1), True),
        (50778, SHORT, 1, 21, True),
        (33434, RATIONAL, 1, (1, 250), True),
        (33437, RATIONAL, 1, (18, 10), True),
        (34855, SHORT, 1, 200, True),
        (271, 2, 0, "TestMake", True),
        (272, 2, 0, "TestModel", True),
    ]
    if active:
        tags.append((50829, LONG, 4, active, True))
    return tags + list(extra)


def write_dng(path: Path, mosaic, tags, *, subifd=False, compression=None) -> Path:
    with tifffile.TiffWriter(path) as tw:
        if subifd:  # DNG layout with a thumbnail in IFD0 and the raw in a SubIFD
            tw.write(np.zeros((2, 2, 3), np.uint8), photometric="rgb", subfiletype=1, subifds=1)
        tw.write(mosaic, photometric=32803, extratags=tags, compression=compression)
    return path


def design_card_path() -> "Path":
    """The V2 card YAML without its ``measured`` block (design values as the truth), for tests
    whose fixtures were rendered from the design."""
    import tempfile

    import yaml

    repo = Path(__file__).resolve().parents[2]
    data = yaml.safe_load((repo / "configs" / "cards" / "nereus_v2.yaml").read_text())
    data.pop("measured", None)
    path = Path(tempfile.mkdtemp()) / "nereus_v2_design.yaml"
    path.write_text(yaml.safe_dump(data))
    return path
