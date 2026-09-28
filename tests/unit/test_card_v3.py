"""Card V3 as config (SPEC §20, docs/reference_card_v3.md): the four card YAMLs load, are
geometrically consistent, render to tags that decode as their own ID block, and the committed
print SVGs match what the YAMLs render today."""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

import pytest

from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.config import load_yaml

REPO = Path(__file__).resolve().parents[2]
CARDS = [REPO / "configs" / "cards" / f"nereus_v3_c{n}.yaml" for n in range(1, 5)]
PRINT_DIR = REPO / "tests" / "fixtures" / "reference_card_v3"


@pytest.fixture(scope="module", params=CARDS, ids=lambda p: p.stem)
def v3(request):
    return load_card(request.param), load_yaml(request.param)


def _overlap(a, b) -> bool:
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


def test_loads_with_expected_shape(v3):
    card, spec = v3
    assert card.tag_family == "tag25h9"
    assert (card.canonical_w, card.canonical_h) == (4200, 2700)
    assert spec["canonical"]["px_per_mm"] == 10
    assert [p.id for p in card.group("grey")] == ["gray_black", "gray_light", "gray_light2", "gray_mid"]
    assert len(card.group("color")) == 8
    assert card.physically_measured  # design values present (source says unmeasured)


def test_tag_blocks_are_disjoint_and_fit_tag25h9():
    blocks = [sorted(load_card(p).tags) for p in CARDS]
    assert blocks == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11], [12, 13, 14, 15]]
    assert max(max(b) for b in blocks) < 35  # tag25h9 has 35 codes


def test_canonical_frame_is_card_mm_times_ten(v3):
    card, spec = v3
    mm = spec["physical_mm"]
    assert card.canonical_w == mm["card_width"] * 10 and card.canonical_h == mm["card_height"] * 10
    tl, tr, bl = (card.tags[card.corner_map[k]].center for k in ("tl", "tr", "bl"))
    assert abs((tr[0] - tl[0]) / 10 - mm["tag_center_spacing_x"]) < 1e-6
    assert abs((bl[1] - tl[1]) / 10 - mm["tag_center_spacing_y"]) < 1e-6
    ratio = mm["tag_center_spacing_x"] / mm["tag_center_spacing_y"]
    assert abs(spec["apriltag"]["quad_ratio"] - ratio) < 0.001


def test_tag_centres_match_the_expand_geometry(v3):
    """Same rule as V2 and patches.canonical_tag_quad: centre +- ((W-1)/2) / expand."""
    card, _ = v3
    fx = (1 - 1 / card.expand_x) / 2 * (card.canonical_w - 1)
    fy = (1 - 1 / card.expand_y) / 2 * (card.canonical_h - 1)
    expected = {"tl": (fx, fy), "tr": (card.canonical_w - 1 - fx, fy),
                "bl": (fx, card.canonical_h - 1 - fy),
                "br": (card.canonical_w - 1 - fx, card.canonical_h - 1 - fy)}
    for corner, tag_id in card.corner_map.items():
        cx, cy = card.tags[tag_id].center
        assert abs(cx - expected[corner][0]) < 0.05 and abs(cy - expected[corner][1]) < 0.05, corner


def test_patches_and_tag_squares_do_not_overlap(v3):
    card, spec = v3
    from nereus_camera_test_rig.color.card import Box

    quiet = spec["layout"]["tag_quiet_cells"] / 7  # tag25h9: 7 cells across incl. border
    squares = []
    for tid, tag in card.tags.items():
        half = tag.edge[0] / 2 * (1 + 2 * quiet)
        squares.append(Box(round(tag.center[0] - half), round(tag.center[1] - half),
                           round(2 * half), round(2 * half)))
    boxes = [p.box for p in card.patches] + squares
    for a, b in combinations(boxes, 2):
        assert not _overlap(a, b), (a, b)
    # >= 4 mm (40 px) of surround between everything and the card edge, for the cut tolerance
    for b in boxes:
        assert b.x >= 40 and b.y >= 40
        assert b.x + b.w <= card.canonical_w - 40 and b.y + b.h <= card.canonical_h - 40


def test_roles_and_surround_are_consistent(v3):
    card, spec = v3
    ids = {p.id for p in card.patches}
    roles = spec["roles"]
    assert set(roles["wb_anchors"]) <= ids and set(roles["ramp"]) <= ids and roles["haze"] in ids
    assert len([g for g in roles["ramp"] if g != roles["haze"]]) >= 3  # review: >= 3 non-black greys
    assert list(card.patch("gray_mid").truth) == spec["layout"]["surround_rgb"]


def test_rendered_front_decodes_as_its_own_block_only(v3):
    cv2 = pytest.importorskip("cv2")
    from host_tools.render_card import back, front, to_raster
    from nereus_camera_test_rig.analysis.apriltag_detector import detect_tags

    card, spec = v3
    gray = cv2.cvtColor(to_raster(front(card, spec), 3.0), cv2.COLOR_RGB2GRAY)
    assert sorted(detect_tags(gray, family="DICT_APRILTAG_25h9", scales=(1,)).tags) == sorted(card.tags)
    assert not detect_tags(gray, family="DICT_APRILTAG_36h11", scales=(1,)).tags  # never read as V2

    b = spec["back"]
    board = cv2.aruco.CharucoBoard(tuple(b["squares"]), b["square_mm"], b["marker_mm"],
                                   cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, b["dictionary"])))
    back_gray = cv2.cvtColor(to_raster(back(card, spec), 3.0), cv2.COLOR_RGB2GRAY)
    _, corner_ids, _, _ = cv2.aruco.CharucoDetector(board).detectBoard(back_gray)
    nx, ny = b["squares"]
    assert corner_ids is not None and len(corner_ids) == (nx - 1) * (ny - 1)


def test_committed_print_svgs_match_the_yaml(v3):
    """The print masters in tests/fixtures/reference_card_v3/ are what the YAML renders today."""
    pytest.importorskip("cv2")
    from host_tools.render_card import back, front, to_svg

    card, spec = v3
    for name, side in (("front", front(card, spec)), ("back", back(card, spec))):
        committed = (PRINT_DIR / f"{card.card_id}_{name}.svg").read_text()
        assert committed == to_svg(side, f"{card.card_id} {name}"), (
            f"{card.card_id}_{name}.svg is stale: re-run python -m host_tools.render_card "
            f"configs/cards/nereus_v3_c*.yaml --out tests/fixtures/reference_card_v3")
