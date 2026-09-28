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
    assert card.design_source == "design_svg_2026-09-01"  # the reference is the measured print
    assert card.truth_is_measured and card.truth_source.startswith("measured_")
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


def test_physical_size_from_the_vector_print_master(card):
    """Values measured from the V2 PDF (docs/reference_card_v2.md); canonical geometry agrees."""
    assert card.physically_measured and card.physical_source.startswith("vector_print_master")
    mm = card.physical_mm
    assert mm["tag_center_spacing_x"] == pytest.approx(364.900)
    assert mm["tag_center_spacing_y"] == pytest.approx(91.566)
    assert mm["tag_edge"] == pytest.approx(31.980)
    assert mm["card_width"] / mm["card_height"] == pytest.approx(3.0, abs=1e-4)
    # The canonical tag edge is consistent with the physical one (canonical px not square).
    px_per_mm_x = (card.canonical_w - 1) / (card.expand_x * mm["tag_center_spacing_x"])
    px_per_mm_y = (card.canonical_h - 1) / (card.expand_y * mm["tag_center_spacing_y"])
    edge_x, edge_y = card.tags[0].edge
    assert edge_x == pytest.approx(mm["tag_edge"] * px_per_mm_x, rel=0.02)
    assert edge_y == pytest.approx(mm["tag_edge"] * px_per_mm_y, rel=0.02)


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
        (lambda d: d.pop("roles"), "missing field 'roles'"),
        (lambda d: d["roles"].update(ramp=["gray_white", ["gray_mid", "grey_nope"]]),
         r"unknown patch ids \['grey_nope'\]"),
    ],
)
def test_malformed_cards_fail_loudly(tmp_path, mutate, message):
    with pytest.raises(CardError, match=message):
        load_card(_write(tmp_path, mutate))


def test_v2_roles_reproduce_the_s2a_choices(card):
    assert card.roles.wb_anchors == ("gray_mid", "gray_mid_right", "gray_dark")
    assert card.roles.ramp == (("gray_white",), ("gray_light",), ("gray_dark",),
                               ("gray_mid", "gray_mid_right"))
    assert card.roles.haze == "gray_black"
    assert card.aruco_dictionary == "DICT_APRILTAG_36h11"
    assert card.quad_ratio == pytest.approx(364.9 / 91.566)
    assert set(card.grey_ids) == {"gray_white", "gray_light", "gray_mid", "gray_dark",
                                  "gray_black", "gray_mid_left", "gray_mid_right"}


# A card on the V3 schema (PR #45, nereus_v3_c1): tag25h9, 4 greys with no white patch, no
# sub-patches, explicit quad ratio.
V3_LIKE = {
    "card_id": "v3_like", "schema_version": 1,
    "apriltag": {"family": "tag25h9", "corner_map": {"tl": 0, "tr": 1, "bl": 2, "br": 3},
                 "quad_ratio": 1.829},
    "canonical": {"width": 4200, "height": 2700, "px_per_mm": 10, "expand_x": 1.268580,
                  "expand_y": 1.491160},
    "tags": {i: {"center": c, "edge": [630.0, 630.0], "edge_mm": 63.0} for i, c in
             enumerate([[444.5, 444.5], [3754.5, 444.5], [444.5, 2254.5], [3754.5, 2254.5]])},
    "physical_mm": {"source": "design", "tag_center_spacing_x": 331.0,
                    "tag_center_spacing_y": 181.0, "tag_edge": 63.0},
    "truth": {"source": "design_srgb_2026-09-27", "space": "srgb8"},
    "roles": {"wb_anchors": ["gray_light", "gray_light2", "gray_mid"],
              "ramp": ["gray_light", "gray_light2", "gray_mid"], "haze": "gray_black"},
    "patches": [
        {"id": "gray_black", "group": "grey", "box": [80, 890, 836, 920], "truth": [0, 0, 0]},
        {"id": "gray_light", "group": "grey", "box": [996, 890, 1140, 920],
         "truth": [200, 200, 200]},
        {"id": "gray_light2", "group": "grey", "box": [2216, 890, 912, 920],
         "truth": [160, 160, 160]},
        {"id": "gray_mid", "group": "grey", "box": [3208, 890, 912, 920],
         "truth": [118, 118, 118]},
        *({"id": n, "group": "color", "box": [890 + 625 * (i % 4), 40 if i < 4 else 1850, 545,
                                               810], "truth": t}
          for i, (n, t) in enumerate([("red", [200, 30, 45]), ("red_orange", [235, 80, 30]),
                                      ("orange", [245, 140, 30]), ("yellow", [250, 205, 30]),
                                      ("green", [40, 150, 75]), ("cyan", [0, 165, 205]),
                                      ("blue", [30, 80, 170]), ("magenta", [195, 45, 135])])),
    ],
}


def v3_like(tmp_path) -> Path:
    path = tmp_path / "v3_like.yaml"
    path.write_text(yaml.safe_dump(json.loads(json.dumps(V3_LIKE))))
    return path


def test_a_v3_schema_card_loads_with_its_roles(tmp_path):
    from nereus_camera_test_rig.color.water_model import grey_reflectance, ramp_fit

    card = load_card(v3_like(tmp_path))
    assert card.aruco_dictionary == "DICT_APRILTAG_25h9" and card.quad_ratio == 1.829
    assert card.grey_ids == ("gray_black", "gray_light", "gray_light2", "gray_mid")
    assert card.roles.ramp == (("gray_light",), ("gray_light2",), ("gray_mid",))
    rho = grey_reflectance(card)  # no white patch needed: paper white = 1
    assert rho["gray_light"] == pytest.approx(0.578, abs=1e-3)
    assert rho["gray_mid"] == pytest.approx(0.181, abs=1e-3)
    light, haze = [0.05, 0.4, 0.35], [0.01, 0.06, 0.07]
    raw = {g: {"mean_norm": [rho[g] * a + h for a, h in zip(light, haze)]} for g in rho}
    fit = ramp_fit(raw, set(raw), rho, card.roles.ramp)
    assert fit["greys"] == ["gray_light", "gray_light2", "gray_mid"]
    assert fit["A"] == pytest.approx(light) and fit["H"] == pytest.approx(haze)
