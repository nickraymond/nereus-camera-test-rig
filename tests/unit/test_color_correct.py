"""S2a ``fit`` helpers + ``correct`` (SPEC §4 Phase 8 S2a): grey-ramp haze, leave-one-dive-out
depth table, the per-pixel map, and equal patch sets within a comparison class."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.correct import (
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
    fit = ramp_fit(raw, set(raw), RHO, CARD.roles.ramp)
    np.testing.assert_allclose(fit["A"], LIGHT, atol=1e-9)
    np.testing.assert_allclose(fit["H"], HAZE, atol=1e-9)
    assert "gray_black" not in fit["greys"]
    assert ramp_fit(raw, {"gray_dark"}, RHO, CARD.roles.ramp) is None  # one grey: no line


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
            "ramp": ramp_fit({k: {"mean_norm": v} for k, v in means.items()}, set(means), RHO,
                             CARD.roles.ramp),
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
    assert set(CARD.grey_ids) >= {p.id for p in CARD.group("grey")}


def test_grvi_column_is_card_anchored_and_falls_back_to_the_camera_jpeg():
    design = {p.id: list(p.truth) for p in CARD.patches}
    j = job(card_means())
    scores = score({**j, "grvi_means": design}, {}, CARD, np.eye(3))["grvi_cheeca_v3"]
    assert scores["de2000_median"] < 0.5 and scores["n_psi"] == 0 and scores["n_de"] == 12
    assert scores["grvi_no_card"] is False
    grey = {pid: [128, 128, 128] for pid in design}
    fallback = score({**j, "grvi_no_card": True, "jpeg_means": grey}, {}, CARD, np.eye(3))
    assert fallback["grvi_cheeca_v3"]["grvi_no_card"] is True
    assert fallback["grvi_cheeca_v3"]["de2000"] == fallback["camera_jpeg"]["de2000"]


def test_preset_frames_pair_with_the_nearest_a_mode_frame_of_their_dive():
    from nereus_camera_test_rig.color.correct import nearest_a_mode

    rows = {s: {"category": c, "dive_id": d, "time_utc": f"2026-09-16T01:{m:02d}:00+00:00",
                "depth_m": dep}
            for s, c, d, m, dep in (("P", "2_underwater_preset", "1", 30, "10"),
                                    ("A1", "1_reference_A_iso100", "1", 20, "12"),
                                    ("A2", "1_reference_A_iso100", "1", 33, "9.5"),
                                    ("B", "1_reference_A_iso100", "2", 30, "10"),
                                    ("S", "3_scene_card_offcenter", "1", 30, "10"))}
    pair = nearest_a_mode("P", rows, list(rows), "1_reference_A_iso100")
    assert pair == {"stem": "A2", "dt_s": 180.0, "depth_diff_m": -0.5}
    assert nearest_a_mode("P", rows, ["B", "S"], "1_reference_A_iso100") is None


def test_leave_one_sweep_out_keeps_the_dive_but_drops_the_sweep(tmp_path):
    path = tmp_path / "wb_points.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["stem", "dive", "depth_m", "ln_rg", "ln_bg"])
        w.writeheader()
        for i, (dive, d, rg) in enumerate([("3", 4, -1.6), ("3", 8, -1.8), ("3", 12, -9.0),
                                           ("4", 4, -1.6), ("4", 8, -1.8)]):
            w.writerow({"stem": f"S{i}", "dive": dive, "depth_m": d, "ln_rg": rg, "ln_bg": 0})
    loso = depth_table(path, ["3", "4"], None, frozenset({"S2"}))  # S2 = the held-out sweep
    assert loso["n"] == 4 and loso["source_dives"] == ["3", "4"]
    assert loso["ln_rg"][1] == pytest.approx(-0.05)
    assert depth_table(path, ["3", "4"], None)["n"] == 5


def test_v03_columns_use_the_frames_depth_matrix():
    means = card_means()
    image = np.zeros((50, 50, 3)) + HAZE + 1e-6
    j = {**job(means), "ccm": np.eye(3).tolist()}
    maps, _ = frame_maps(j, image)
    assert {"raw_card_wb_ccm", "raw_depth_wb_haze_ccm"} <= set(maps)
    scores = score(j, maps, CARD, np.eye(3))
    assert scores["raw_card_wb_ccm"]["de2000"] == scores["raw_card_wb"]["de2000"]
    desat = 0.7 * np.eye(3) + 0.1  # rows sum to 1: a desaturating depth matrix
    worse = score({**j, "ccm": desat.tolist()}, frame_maps({**j, "ccm": desat.tolist()},
                                                          image)[0], CARD, np.eye(3))
    assert worse["raw_card_wb_ccm"]["de2000_median"] > scores["raw_card_wb"]["de2000_median"]


def test_blend_columns_carry_the_blended_matrix():
    j = {**job(card_means()), "ccm": np.eye(3).tolist(), "ccm_blend": (2 * np.eye(3)).tolist()}
    maps, _ = frame_maps(j, np.zeros((50, 50, 3)) + HAZE + 1e-6)
    for base in ("raw_card_wb", "raw_depth_wb_haze"):
        assert maps[f"{base}_ccm_blend"][:2] == maps[base][:2]
        np.testing.assert_array_equal(maps[f"{base}_ccm_blend"][2], 2 * np.eye(3))
    assert "raw_card_wb_ccm_blend" not in frame_maps({**j, "ccm_blend": None},
                                                     np.zeros((50, 50, 3)) + HAZE)[0]


def test_card_affine_column_scores_leave_one_patch_out():
    from nereus_camera_test_rig.color.correct import card_job

    means = card_means()  # an exactly affine card: haze + light
    q = {"patches": {pid: {"usable": True} for pid in means}, "card_condition": "clean"}
    row = {"category": "1_reference_A_iso100", "depth_m": "8.0", "dive_id": "3"}
    patches = {"raw": {"exposure_factor": 1.0,
                       "patches": {pid: {"mean_norm": v, "std": [1e-4] * 3}
                                   for pid, v in means.items()}}}
    j = {**card_job("F", row, q, patches, None, CARD, RHO), "table": None}
    assert set(j["affine"]["loo"]) == {p.id for p in CARD.group("color")}
    maps, _ = frame_maps(j, np.zeros((50, 50, 3)) + HAZE + 1e-6)
    s = score(j, maps, CARD, np.eye(3))["raw_card_affine"]
    assert s["n_de"] == 12 and s["de2000_median"] < 1.0


def test_card_slope_wb_balances_the_light_not_the_haze():
    # strong blue-green haze: grey 128 is mostly backscatter, so balancing on it over-boosts red
    haze = np.array([0.002, 0.2, 0.15])
    means = card_means(haze=haze)
    j = job(means)
    maps, _ = frame_maps(j, np.zeros((50, 50, 3)) + haze + 1e-6)
    slope, anchor = np.asarray(maps["raw_card_slope_wb"][1]), np.asarray(maps["raw_card_wb"][1])
    # the slope gain neutralizes the light itself; green (brightness) matches card WB
    np.testing.assert_allclose(slope * LIGHT, slope[1] * LIGHT[1], rtol=1e-9)
    assert slope[1] == pytest.approx(anchor[1])
    assert slope[0] < anchor[0]  # less red gain than balancing on the hazy grey
    assert maps["raw_card_slope_wb"][0] == [0.0] * 3
    assert score(j, maps, CARD, np.eye(3))["raw_card_slope_wb"]["n_de"] == 12
    # no usable ramp, or only two greys → no slope column
    maps, _ = frame_maps({**j, "ramp": None}, np.zeros((50, 50, 3)) + haze)
    assert "raw_card_slope_wb" not in maps
    two = {**j["ramp"], "greys": j["ramp"]["greys"][:2]}
    maps, _ = frame_maps({**j, "ramp": two}, np.zeros((50, 50, 3)) + haze)
    assert "raw_card_slope_wb" not in maps
