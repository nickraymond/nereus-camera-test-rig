"""``decide`` stage (SPEC §4 Phase 8 S2a, §20): paired comparisons within a class, sweep-level
bootstrap, the pre-registered rule, n tables, preset pairs, needs-V3 and the HTML page."""

from __future__ import annotations

import json

import numpy as np

from nereus_camera_test_rig.color.decision import (
    GRVI_FOUND,
    compare,
    decide,
    decide_numbers,
    n_table,
    sweep_bootstrap,
    with_grvi_found,
)
from nereus_camera_test_rig.color.stages import write_stage


def frame(dive="3", sweep="", cond="damaged", **methods):
    return {"dive_id": dive, "sweep_id": sweep, "card_condition": cond, "category": "cat",
            "methods": {m: {"de2000_median": de, "psi_median": psi, "n_de": 12, "n_psi": 3}
                        for m, (de, psi) in methods.items()}}


def synthetic(n=40, gap=20.0, seed=0):
    rng = np.random.default_rng(seed)
    return {f"F{i}": frame(dive=str(3 + i % 2), sweep=f"S{i // 4}",
                           cond="clean" if i < 8 else "damaged",
                           raw_depth_wb_haze=(19 + rng.normal(0, 2), 10 + rng.normal(0, 1)),
                           camera_jpeg=(19 + gap + rng.normal(0, 2), 40 + rng.normal(0, 1)))
            for i in range(n)}


def test_a_clear_win_passes_the_rule_and_a_tie_fails_it():
    win = compare(synthetic(), "raw_depth_wb_haze", "camera_jpeg", "psi_median")
    assert win["verdict"] == "pass" and win["win_rate"] == 1.0
    assert win["n_frames"] == 40 and win["n_sweeps"] == 10 and win["ci95"][1] < 0
    tie = compare(synthetic(gap=0.0), "raw_depth_wb_haze", "camera_jpeg", "de2000_median")
    assert tie["verdict"] == "fail" and tie["ci95"][0] < 0 < tie["ci95"][1]
    few = compare(synthetic(n=5), "raw_depth_wb_haze", "camera_jpeg", "psi_median")
    assert few["verdict"] == "too few pairs"


def test_bootstrap_resamples_whole_sweeps():
    # one sweep far off: a frame-level bootstrap would call the median tight, sweeps do not
    diff = np.r_[np.full(10, -1.0), np.full(10, -1.0), np.full(10, 5.0)]
    lo, hi = sweep_bootstrap(diff, np.repeat(["a", "b", "c"], 10))
    assert lo == -1.0 and hi == 5.0


def test_grvi_is_reported_as_run_and_where_it_found_the_card():
    s = synthetic(n=12)
    for i, v in enumerate(s.values()):
        v["methods"]["grvi_cheeca_v3"] = {"de2000_median": 30.0, "grvi_no_card": i % 3 == 0}
    found = with_grvi_found(s)
    assert sum(GRVI_FOUND in v["methods"] for v in found.values()) == 8
    assert "grvi_cheeca_v3" in found["F0"]["methods"]


def test_n_table_counts_frames_and_patches_per_dive_and_condition():
    n = n_table(synthetic(), ["camera_jpeg"])["camera_jpeg"]
    assert n["3"]["clean"] == [4, 60] and n["4"]["damaged"] == [16, 240]


def test_decide_stage_writes_numbers_and_page(tmp_path):
    root = tmp_path / "ds"
    correct_dir, qc_dir = root / "correct", root / "qc"
    scores = synthetic()
    for v in scores.values():
        v["methods"]["raw_card_wb"] = {"de2000_median": 18.0, "n_de": 12}
        v["methods"]["jpeg_card_wb"] = {"de2000_median": 37.0, "n_de": 12}
    scores["F0"]["category"] = "2_underwater_preset"
    scores["F0"]["methods"]["olympus_preset_jpeg"] = {"de2000_median": 45.0, "psi_median": 46.0}
    scores["F0"]["pair_a_mode"] = {"stem": "F1", "dt_s": 30.0, "depth_diff_m": 0.2}
    scores["FL"] = {**frame(), "flash": True}
    correct_dir.mkdir(parents=True)
    (correct_dir / "scores.json").write_text(json.dumps(scores))
    (correct_dir / "summary.json").write_text(json.dumps(
        {"card_haze_cap_bound_frames": "3 of 4", "grvi_no_card_frames": []}))
    qc_dir.mkdir()
    (qc_dir / "qc.json").write_text(json.dumps(
        {s: {"patches": {"gray_white": {"usable": False}, "cyan": {"usable": True}}}
         for s in scores}))
    write_stage(correct_dir, "correct")
    cfg = tmp_path / "dataset.yaml"
    cfg.write_text("fit:\n  reference_category: cat\n  wb_categories: [cat]\n"
                   "  table_source_dives: ['3', '4']\n  flagged_dives: {'1': sunset}\n")
    out = decide(correct_dir, cfg)
    result = json.loads((root / "decide" / "decision.json").read_text())
    anchored = {(c["nereus"], c["baseline"]): c
                for c in result["classes"]["card_anchored"]["comparisons"]}
    assert anchored[("raw_card_wb", "jpeg_card_wb")]["verdict"] == "pass"
    assert result["preset_pairs"]["n"] == 1 and list(result["flash"]) == ["FL"]
    assert any("gray_white" in x for x in out["needs_v3"])
    assert any("Dive 1" in x for x in out["needs_v3"])
    page = (root / "decide" / "index.html").read_text()
    assert "Card-anchored" in page and "Needs the V3 dataset" in page


def test_card_free_is_never_compared_with_card_anchored_columns():
    result = decide_numbers(synthetic())
    for c in result["classes"]["card_free"]["comparisons"]:
        assert c["baseline"] in ("camera_jpeg", "olympus_preset_jpeg")
