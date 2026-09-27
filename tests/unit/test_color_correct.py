"""S2a ``fit`` helpers + ``correct`` (SPEC §4 Phase 8 S2a): grey-ramp haze, leave-one-dive-out
depth table, the per-pixel map, and equal patch sets within a comparison class."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.correct import (
    ALL_GREYS,
    apply,
    apply_std,
    dark_floor,
    depth_table,
    frame_maps,
    score,
)
from nereus_camera_test_rig.color.metrics import srgb8_to_linear
from nereus_camera_test_rig.color.water_model import grey_reflectance, ramp_fit

REPO = Path(__file__).resolve().parents[2]
CARD = load_card(REPO / "configs" / "cards" / "nereus_v2.yaml")
RHO = grey_reflectance(CARD)
LIGHT, HAZE = np.array([0.05, 0.4, 0.35]), np.array([0.01, 0.06, 0.07])


def card_means(light=LIGHT, haze=HAZE):
    """Camera-space patch means of a card lit by ``light`` with additive ``haze`` (greys only
    follow the model exactly; colour patches use their design sRGB as reflectance)."""
    out = {}
    for p in CARD.patches:
        rho = srgb8_to_linear(p.truth) / srgb8_to_linear(255)
        out[p.id] = (rho * light + haze).tolist()
    out["gray_mid_right"] = out["gray_mid_left"] = out["gray_mid"]
    return out


def test_ramp_fit_recovers_light_and_haze():
    raw = {pid: {"mean_norm": v} for pid, v in card_means().items()}
    fit = ramp_fit(raw, set(raw), RHO)
    np.testing.assert_allclose(fit["A"], LIGHT, atol=1e-9)
    np.testing.assert_allclose(fit["H"], HAZE, atol=1e-9)
    assert "gray_black" not in fit["greys"]
    assert ramp_fit(raw, {"gray_dark"}, RHO) is None  # one grey: no line


def test_depth_table_leaves_the_frames_own_dive_out(tmp_path):
    path = tmp_path / "wb_points.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["dive", "depth_m", "ln_rg", "ln_bg"])
        w.writeheader()
        for dive, slope in (("3", -0.05), ("4", -0.03)):
            for d in (4, 8, 12, 16):
                w.writerow({"dive": dive, "depth_m": d, "ln_rg": -1.4 + slope * d, "ln_bg": 0})
    t3 = depth_table(path, ["3", "4"], "3")
    assert t3["source_dives"] == ["4"] and t3["ln_rg"][1] == pytest.approx(-0.03)
    assert depth_table(path, ["3", "4"], "1")["source_dives"] == ["3", "4"]


def test_map_and_std_propagation():
    m = np.eye(3)
    np.testing.assert_allclose(apply([0.2, 0.3, 0.4], [0.1] * 3, [2, 1, 1], m), [0.2, 0.2, 0.3])
    np.testing.assert_allclose(apply_std([0.01, 0.01, 0.01], [2, 1, 1], m), [0.02, 0.01, 0.01])


def test_dark_floor_ignores_the_vignetted_corners():
    img = np.full((100, 100, 3), 0.5)
    img[:15, :15] = 0.0  # dark corner outside the central 60 %
    np.testing.assert_allclose(dark_floor(img), [0.5] * 3)


def job(means):
    return {"anchor": "gray_mid", "anchor_truth": float(srgb8_to_linear(128)), "excluded": [],
            "raw_means": means, "raw_stds": {k: [1e-4] * 3 for k in means},
            "ramp": ramp_fit({k: {"mean_norm": v} for k, v in means.items()}, set(means), RHO),
            "depth_m": 8.0, "category": "1_reference_A_iso100",
            "table": {"ln_rg": [float(np.log(LIGHT[0] / LIGHT[1])), 0.0],
                      "ln_bg": [float(np.log(LIGHT[2] / LIGHT[1])), 0.0]}}


def test_card_haze_method_recovers_the_card_and_classes_share_patches():
    means = card_means()
    image = np.zeros((50, 50, 3)) + HAZE + 1e-6  # dark floor just above the haze
    j = job(means)
    maps, diag = frame_maps(j, image)
    assert diag["haze_source"] == "grey ramp" and not any(diag["haze_cap_bound"])
    scores = score(j, maps, CARD, np.eye(3))
    # haze removed + WB on grey 128 → the greys and colours come back to their design values
    assert scores["raw_card_wb_haze"]["de2000_median"] < 1.0
    assert scores["raw_card_wb"]["de2000_median"] > scores["raw_card_wb_haze"]["de2000_median"]
    # card-anchored methods are all scored on colour patches only (every grey held out)
    for m in ("raw_card_wb", "raw_card_wb_haze"):
        assert scores[m]["n_psi"] == 0 and scores[m]["n_de"] == 12
    assert set(ALL_GREYS) >= {p.id for p in CARD.group("grey")}
