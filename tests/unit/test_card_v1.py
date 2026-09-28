"""configs/cards/nereus_v1.yaml is pinned to the V1 vector print master (tests/fixtures/
reference_card_v1/*.pdf): patch boxes, design fills and physical tag geometry are re-derived
from the PDF's drawing operators. Opt-in: NEREUS_V1_CARD_DNG=<rig DNG with the V1 card> checks
the layout on a real frame."""

from __future__ import annotations

import os
import re
import zlib
from pathlib import Path

import numpy as np
import pytest

from nereus_camera_test_rig.color.card import load_card

ROOT = Path(__file__).resolve().parents[2]
CARD = load_card(ROOT / "configs" / "cards" / "nereus_v1.yaml")
PDF = (ROOT / "tests/fixtures/reference_card_v1"
       / "Nereus_Reef_Reference_Card_V1_11x17_RGB_vector_crop_bleed.pdf")
CELL = 13.87559  # tag cell size in the PDF form, pt


def _form() -> str:
    data = PDF.read_bytes()
    streams = [zlib.decompress(m.group(1)).decode("latin1")
               for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S)]
    return max(streams, key=len)


def _pdf_geometry():
    fill, patches, cells = None, [], []
    for s in (line.strip() for line in _form().splitlines()):
        if m := re.match(r"^([\d.]+) ([\d.]+) ([\d.]+) rg$", s):
            fill = tuple(float(v) for v in m.groups())
        elif m := re.match(r"n ([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) re (B\*|f\*)", s):
            x, y, w, h = map(float, m.groups()[:4])
            if m.group(5) == "B*":
                patches.append((x, y, w, h, fill))
            elif fill == (0.0, 0.0, 0.0) and abs(w - CELL) < 0.01:
                cells.append((x, y, w, h))
    return patches[:18], np.array(cells)  # the last 4 B* rects are the scale bar


def test_boxes_and_design_fills_match_the_pdf():
    patches, cells = _pdf_geometry()
    W = 1417.323  # form width, pt
    xs = sorted({round(c[0], 2) for c in cells})
    left, right = [c for c in cells if c[0] < W / 2], [c for c in cells if c[0] > W / 2]
    tx0 = (min(c[0] for c in left) + max(c[0] + c[2] for c in left)) / 2
    tx1 = (min(c[0] for c in right) + max(c[0] + c[2] for c in right)) / 2
    top, bot = [c for c in cells if c[1] > 236], [c for c in cells if c[1] < 236]
    ty0 = (min(c[1] for c in top) + max(c[1] + c[3] for c in top)) / 2
    ty1 = (min(c[1] for c in bot) + max(c[1] + c[3] for c in bot)) / 2
    (c0x, c0y), (c1x, _), (_, c2y) = (CARD.tags[0].center, CARD.tags[1].center,
                                      CARD.tags[2].center)
    sx, sy = (c1x - c0x) / (tx1 - tx0), (c2y - c0y) / (ty0 - ty1)
    assert len(xs) > 8 and len(patches) == 18 == len(CARD.patches)
    for (x, y, w, h, fill), p in zip(patches, CARD.patches):
        box = [c0x + (x - tx0) * sx, c0y + (ty0 - (y + h)) * sy, w * sx, h * sy]
        np.testing.assert_allclose([p.box.x, p.box.y, p.box.w, p.box.h], box, atol=0.6)
        assert tuple(p.design) == tuple(round(v * 255) for v in fill), p.id


def test_physical_tag_geometry_matches_the_pdf_at_page_scale():
    assert "/Matrix[.82 0 0 .82" in PDF.read_bytes().decode("latin1")  # placed at 82 %
    mm = 25.4 / 72 * 0.82
    _, cells = _pdf_geometry()
    edge = (cells[:, 0] + cells[:, 2]).max() - cells[:, 0].min()  # span of all tags, pt
    spacing_x = (edge - 110.608) * mm
    assert CARD.physical_mm["tag_center_spacing_x"] == pytest.approx(spacing_x, abs=0.01)
    assert CARD.physical_mm["tag_edge"] == pytest.approx(110.608 * mm, abs=0.01)
    assert CARD.quad_ratio == pytest.approx(3.985, abs=0.001)  # the same tag frame as V2


def test_measured_truth_is_used_and_design_is_kept():
    assert CARD.truth_is_measured
    blue = CARD.patch("blue")
    assert tuple(blue.design) == (12, 102, 199) and blue.truth[0] == 0.0


REAL = os.environ.get("NEREUS_V1_CARD_DNG")


@pytest.mark.skipif(not REAL, reason="set NEREUS_V1_CARD_DNG to a rig DNG with the V1 card")
def test_v1_layout_fits_a_real_rig_frame():
    from nereus_camera_test_rig.color.linear_jxl import card_white_balance
    from nereus_camera_test_rig.color.raw_io import read_dng

    _, info = card_white_balance(read_dng(REAL), CARD)
    assert info["tags_found"] == [0, 1, 2, 3] and info["layout_cv"] < 0.1
    v2 = load_card(ROOT / "configs" / "cards" / "nereus_v2.yaml")
    with pytest.raises(ValueError, match="not on uniform patches"):
        card_white_balance(read_dng(REAL), v2)  # the V2 layout does not fit a V1 print
