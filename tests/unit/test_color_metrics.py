"""Colour metrics + scoring protocol (SPEC §4 Phase 8 S1.6, §20).

ΔE2000 is checked against the published CIEDE2000 test data of Sharma, Wu & Dalal (2005),
``tests/fixtures/color/ciede2000_sharma2005.txt`` (verbatim from
https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/dataNprograms/ciede2000testdata.txt;
columns L1 a1 b1 L2 a2 b2 ΔE00)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.metrics import (
    camera_to_linear,
    delta_ch,
    delta_e2000,
    linear_to_lab,
    psi_deg,
    red_signal,
    score_linear,
    score_srgb8,
    srgb8_to_linear,
)

REPO = Path(__file__).resolve().parents[2]
SHARMA = np.loadtxt(REPO / "tests" / "fixtures" / "color" / "ciede2000_sharma2005.txt")
CARD = load_card(REPO / "configs" / "cards" / "nereus_v2.yaml")
TRUTH8 = {p.id: p.truth for p in CARD.patches}


def test_delta_e2000_matches_all_34_published_pairs():
    assert SHARMA.shape == (34, 7)
    got = delta_e2000(SHARMA[:, :3], SHARMA[:, 3:6])
    np.testing.assert_allclose(got, SHARMA[:, 6], atol=1e-4)
    np.testing.assert_allclose(delta_e2000(SHARMA[:, 3:6], SHARMA[:, :3]), SHARMA[:, 6],
                               atol=1e-4)  # symmetric


def test_lab_of_srgb_white_and_mid_grey():
    np.testing.assert_allclose(linear_to_lab([1.0, 1.0, 1.0]), [100, 0, 0], atol=1e-9)
    lab = linear_to_lab(srgb8_to_linear([128, 128, 128]))
    assert lab[0] == pytest.approx(53.585, abs=0.01) and abs(lab[1]) < 1e-9


def test_delta_ch_components_close_the_cie76_triangle():
    ref, lab = np.array([60.0, 20.0, -10.0]), np.array([55.0, 5.0, 25.0])
    dc, dh = delta_ch(ref, lab)
    de76 = np.linalg.norm(lab - ref)
    assert de76 ** 2 == pytest.approx(5.0 ** 2 + dc ** 2 + dh ** 2)
    assert dc == pytest.approx(np.hypot(5, 25) - np.hypot(20, -10))


def test_psi():
    assert psi_deg([0.3, 0.3, 0.3]) == pytest.approx(0.0, abs=1e-6)
    assert psi_deg([1.0, 0.0, 0.0]) == pytest.approx(np.degrees(np.arccos(1 / np.sqrt(3))))
    assert psi_deg([0.2, 0.4, 0.5]) == pytest.approx(psi_deg([0.02, 0.04, 0.05]))


def test_perfect_card_scores_zero_even_when_underexposed():
    lin = {k: srgb8_to_linear(v) * 0.4 for k, v in TRUTH8.items()}  # exposure only
    s = score_linear(lin, CARD)
    assert s["n_de"] == 12 and s["de2000_median"] == pytest.approx(0, abs=1e-6)
    assert set(s["psi"]) == {"gray_white", "gray_light", "gray_dark"}  # grey 128 held out
    assert s["psi_gated"] == ["gray_black"] and s["psi_median"] == pytest.approx(0, abs=1e-6)


def test_a_colour_cast_is_scored():
    lin = {k: srgb8_to_linear(v) * [0.5, 1.0, 1.1] for k, v in TRUTH8.items()}
    s = score_linear(lin, CARD)
    assert s["psi_median"] > 10 and s["de2000_median"] > 5


def test_a_channel_driven_to_zero_counts_as_error_not_gated():
    """The TG-7 camera JPEG under water: red 0 on the greys (SPEC §20 evidence)."""
    means = {k: [0, v[1], v[2]] for k, v in TRUTH8.items()}
    stds = {k: [0.0, 2.0, 2.0] for k in TRUTH8}
    s = score_srgb8(means, CARD, stds=stds)
    assert s["psi"]["gray_white"] == pytest.approx(np.degrees(np.arccos(2 / np.sqrt(6))), abs=1e-3)
    assert "gray_white" not in s["psi_gated"]


def test_noisy_patch_is_gated():
    means = {k: srgb8_to_linear(v) for k, v in TRUTH8.items()}
    stds = {k: np.full(3, 1.0) for k in TRUTH8}  # SNR < 1 everywhere
    s = score_linear(means, CARD, stds=stds)
    assert s["n_psi"] == 0 and len(s["psi_gated"]) == 4


def test_excluded_anchor_and_the_right_half_fallback():
    lin = {k: srgb8_to_linear(v) for k, v in TRUTH8.items()}
    s = score_linear(lin, CARD, exclude=["gray_mid", "gray_white"])
    assert s["anchor"] is None and s["n_de"] == 0 and "gray_white" not in s["psi"]
    lin["gray_mid_right"] = lin["gray_mid"]
    s = score_linear(lin, CARD, exclude=["gray_mid"], anchor="gray_mid_right")
    assert s["anchor"] == "gray_mid_right" and s["n_de"] == 12


def test_camera_to_linear_and_red_signal():
    np.testing.assert_allclose(camera_to_linear([0.1, 0.2, 0.3], [2.0, 1.0, 0.5]),
                               [0.2, 0.2, 0.15])
    m = np.array([[1.2, -0.2, 0], [0, 1, 0], [0, -0.1, 1.1]])
    np.testing.assert_allclose(camera_to_linear([0.2, 0.2, 0.2], [1, 1, 1], m), [0.2] * 3)
    r = red_signal({"gray_white": {"mean": [0.04, 0.3, 0.3], "std": [0.01, 0.01, 0.01]}})
    assert r == {"gray_white": {"red_snr": 4.0, "red_full_scale": 0.04}}
