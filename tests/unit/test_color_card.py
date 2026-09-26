"""Card-as-config (SPEC §4 Phase 8 S0, §20): the V2 card YAML loads, matches the canonical
fixture geometry, and malformed cards fail loudly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from nereus_camera_test_rig.color.card import CardError, load_card

REPO = Path(__file__).resolve().parents[2]
CARD_YAML = REPO / "configs" / "cards" / "nereus_v2.yaml"
FIXTURE_LAYOUT = REPO / "tests" / "fixtures" / "reference_card" / "template_layout.json"


@pytest.fixture(scope="module")
def card():
    return load_card(CARD_YAML)


def test_v2_card_loads_with_expected_shape(card):
    assert card.card_id == "nereus_v2"
    assert card.truth_source == "design_svg_2026-09-01"
    assert len(card.group("grey")) == 5
    assert len(card.group("color")) == 12
    assert card.corner_map == {"tl": 0, "tr": 1, "bl": 2, "br": 3}
    assert (card.canonical_w, card.canonical_h) == (3000, 1000)


def test_geometry_is_pinned_to_the_fixture_layout(card):
    """Patch ids and boxes must match the canonical template the rectifier produces."""
    fixture = json.loads(FIXTURE_LAYOUT.read_text())
    assert (fixture["template_width_px"], fixture["template_height_px"]) == (
        card.canonical_w,
        card.canonical_h,
    )
    expected = [(p["id"], (p["x"], p["y"], p["w"], p["h"])) for p in fixture["patches"]]
    actual = [(p.id, (p.box.x, p.box.y, p.box.w, p.box.h)) for p in card.patches]
    assert actual == expected


def test_tag_centres_match_the_expand_geometry(card):
    """Tag centres sit where expanding the tag quad by expand_x/y about its centre puts them."""
    fx = (1 - 1 / card.expand_x) / 2 * (card.canonical_w - 1)
    fy = (1 - 1 / card.expand_y) / 2 * (card.canonical_h - 1)
    expected = {
        "tl": (fx, fy),
        "tr": (card.canonical_w - 1 - fx, fy),
        "bl": (fx, card.canonical_h - 1 - fy),
        "br": (card.canonical_w - 1 - fx, card.canonical_h - 1 - fy),
    }
    for corner, tag_id in card.corner_map.items():
        cx, cy = card.tags[tag_id].center
        assert abs(cx - expected[corner][0]) < 2 and abs(cy - expected[corner][1]) < 2, corner


def test_physical_size_unmeasured_until_oq32(card):
    assert not card.physically_measured
    assert card.nominal_mm["source"].endswith("assumption")


def test_sub_patches_split_grey_128(card):
    subs = {s.id: s for s in card.sub_patches}
    left, right = subs["gray_mid_left"], subs["gray_mid_right"]
    parent = card.patch("gray_mid").box
    assert left.box.x == parent.x and left.box.x + left.box.w == right.box.x
    assert right.box.x + right.box.w == parent.x + parent.w


def _write(tmp_path: Path, mutate) -> Path:
    data = yaml.safe_load(CARD_YAML.read_text())
    mutate(data)
    out = tmp_path / "card.yaml"
    out.write_text(yaml.safe_dump(data))
    return out


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.pop("truth"), "missing field 'truth'"),
        (lambda d: d["patches"][0].update(truth=[255, 255, 256]), "0-255"),
        (lambda d: d["patches"][0].update(box=[2900, 0, 200, 10]), "outside the canonical frame"),
        (lambda d: d["patches"][1].update(id=d["patches"][0]["id"]), "duplicate patch ids"),
        (lambda d: d["patches"][0].update(group="gray"), "group must be one of"),
        (lambda d: d["tags"].pop(3), "have no entry under 'tags'"),
        (lambda d: d["sub_patches"][0].update(box=[0, 0, 10, 10]), "outside parent"),
    ],
)
def test_malformed_cards_fail_loudly(tmp_path, mutate, message):
    with pytest.raises(CardError, match=message):
        load_card(_write(tmp_path, mutate))
