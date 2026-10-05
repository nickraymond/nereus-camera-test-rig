"""Card truth from a reference chart (card V3, 2026-10-05): colour maths, the fit and its
held-out score, the reference CSV reader, and the YAML block round trip through card.py.
(The sampling path runs on real RAW frames; see docs/v3_card_truth_capture.md.)"""

import shutil
from pathlib import Path

import numpy as np
import pytest

from nereus_camera_test_rig.color import card_truth as T
from nereus_camera_test_rig.color.card import load_card

REPO = Path(__file__).resolve().parents[2]


def test_lab_xyz_round_trip_and_white():
    lab = np.array([[50.0, 20.0, -30.0], [96.5, -0.4, 1.2], [5.0, 1.0, -1.0]])
    assert np.allclose(T.xyz_to_lab(T.lab_to_xyz(lab)), lab, atol=1e-9)
    assert np.allclose(T.lab_to_xyz([100, 0, 0]), T.D50)


def test_d50_white_maps_to_srgb_white():
    v, oog = T.xyz50_to_srgb8(T.D50)
    assert np.allclose(v, 255, atol=0.5) and not oog


@pytest.mark.parametrize("model", ["linear3x3", "rootpoly2"])
def test_fit_recovers_a_camera_and_held_out_error_is_small(model):
    rng = np.random.default_rng(3)
    xyz = rng.uniform(0.03, 0.9, (24, 3))
    cam_from_xyz = np.array([[1.2, 0.1, 0.05], [0.2, 1.1, 0.1], [0.05, 0.3, 1.4]])  # rgb > 0
    rgb = xyz @ cam_from_xyz.T
    M = T.fit(rgb, xyz, model)
    assert np.allclose(T.apply(M, rgb, model), xyz, atol=1e-6 if model == "linear3x3" else 2e-2)
    assert T.stats(T.loo(rgb, xyz, model))["median"] < (0.01 if model == "linear3x3" else 1.0)


def test_rootpoly_is_exposure_invariant():
    rng = np.random.default_rng(4)
    rgb = rng.uniform(0.05, 0.8, (24, 3))
    xyz = rng.uniform(0.05, 0.8, (24, 3))
    M = T.fit(rgb, xyz, "rootpoly2")
    assert np.allclose(T.apply(M, 2 * rgb, "rootpoly2"), 2 * T.apply(M, rgb, "rootpoly2"))


def test_reference_csv_variants(tmp_path):
    a = tmp_path / "a.csv"
    a.write_text("# Spyder export\nPatch;L*;a*;b*\nA1;96.1;-0.2;1.9\nA2;50.0;1;2\n")
    ref = T.load_reference(a)
    assert list(ref) == ["A1", "A2"] and ref["A1"]["lab"] == (96.1, -0.2, 1.9)
    b = tmp_path / "b.csv"
    b.write_text("name,X,Y,Z\nw,96.422,100,82.521\n")
    assert np.allclose(T.load_reference(b)["w"]["lab"], (100, 0, 0), atol=1e-6)
    c = tmp_path / "c.csv"
    c.write_text("foo,bar\n1,2\n")
    with pytest.raises(ValueError):
        T.load_reference(c)


def test_measured_block_round_trips_through_the_card_loader(tmp_path):
    card_yaml = tmp_path / "nereus_v3_c1.yaml"
    shutil.copy(REPO / "configs/cards/nereus_v3_c1.yaml", card_yaml)
    card = load_card(card_yaml)
    res = {"values": {p.id: [120.0, 130.0, 140.0] for p in card.patches},
           "lab_d50": {p.id: [50.0, 1.0, -2.0] for p in card.patches},
           "out_of_srgb": [], "over_range": [], "reference_sha256": "ab" * 32,
           "flat_frames": ["f.dng"], "frames": ["a.dng", "b.dng"], "flat_spread_pct_p95_p5": 4.2,
           "chart_patches_used": 24,
           "fit": {"model": "linear3x3", "loo_de2000": {"linear3x3": {"median": 0.8, "p90": 1.5,
                                                                      "max": 2.1}}}}
    session = {"condition": "dry", "chart": {"config": "configs/charts/spydercheckr_24.yaml",
                                             "reference": "data/x.csv"},
               "light": {"name": "2x 5300 K", "cct_k": 5300, "cri": None}}
    block = T.measured_block(res, session, "2026-10-05T03:00Z")
    T.write_block(card_yaml, block)
    T.write_block(card_yaml, block)                       # replaces, never duplicates
    text = card_yaml.read_text()
    assert text.count("\nmeasured:") == 1
    c2 = load_card(card_yaml)
    assert c2.truth_source.startswith("measured_imx708_spydercheckr_24_2026-10-05_dry")
    assert c2.patch("gray_mid").truth == (120.0, 130.0, 140.0)
    wet = T.measured_block(res, {**session, "condition": "wet"}, "2026-10-06T03:00Z")
    T.write_block(card_yaml, wet)
    assert "\nmeasured_wet:" in card_yaml.read_text()
    assert load_card(card_yaml).patch("gray_mid").truth == (120.0, 130.0, 140.0)
