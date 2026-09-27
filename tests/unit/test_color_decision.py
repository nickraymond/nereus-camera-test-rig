"""``decide`` stage (SPEC §4 Phase 8 S2a, §20): paired comparisons within a class, sweep-level
bootstrap, the pre-registered rule, n tables, preset pairs, needs-V3 and the HTML page."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import cv2
import numpy as np
import pytest

from nereus_camera_test_rig.color.correct import COLUMNS
from nereus_camera_test_rig.color.decision import (
    GRVI_FOUND,
    compare,
    decide,
    decide_numbers,
    n_table,
    sweep_bootstrap,
    with_grvi_found,
)
from nereus_camera_test_rig.color.decision_sheets import SEED, nereus_side, score_blind
from nereus_camera_test_rig.color.stages import verify_fresh, write_stage

CARD_PATH = Path(__file__).resolve().parents[2] / "configs" / "cards" / "nereus_v2.yaml"


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


def _stage_fixture(tmp_path):
    """A §20 results tree: ingest manifest + dataset JPEGs, patches, qc, correct + images."""
    root, ds = tmp_path / "ds", tmp_path / "data"
    correct_dir, qc_dir = root / "correct", root / "qc"
    scores = synthetic()
    for v in scores.values():
        v["methods"]["raw_card_wb"] = {"de2000_median": 18.0, "n_de": 12}
        v["methods"]["jpeg_card_wb"] = {"de2000_median": 37.0, "n_de": 12}
        v["methods"]["grvi_cheeca_v3"] = {"de2000_median": 30.0, "grvi_no_card": False}
        v["anchor"] = "gray_mid"
    scores["F0"]["category"] = "2_underwater_preset"
    scores["F0"]["methods"]["olympus_preset_jpeg"] = {"de2000_median": 45.0, "psi_median": 46.0}
    scores["F0"]["pair_a_mode"] = {"stem": "F1", "dt_s": 30.0, "depth_diff_m": 0.2}
    scores["FL"] = {**frame(), "flash": True}
    stems = list(scores) + ["N1"]  # N1: a no-card frame (rendered, not scored)
    (ds / "cat").mkdir(parents=True)
    img = np.full((60, 80, 3), (90, 140, 30), np.uint8)
    for d in ("raw_card_wb", "raw_card_wb_haze", "raw_depth_wb_haze"):
        (correct_dir / "images" / d).mkdir(parents=True, exist_ok=True)
    (root / "grvi" / "images").mkdir(parents=True)
    rows = []
    for i, s in enumerate(stems):
        cv2.imwrite(str(ds / "cat" / f"{s}.jpg"), img)
        for d in ("raw_card_wb", "raw_card_wb_haze", "raw_depth_wb_haze"):
            cv2.imwrite(str(correct_dir / "images" / d / f"{s}.jpg"), img[..., ::-1])
        cv2.imwrite(str(root / "grvi" / "images" / f"{s}.jpg"), img)
        v = scores.get(s, frame())
        rows.append({"stem": s, "jpeg": f"cat/{s}.jpg", "sweep_id": v["sweep_id"],
                     "category": "4_no_card" if s == "N1" else v["category"],
                     "dive_id": v["dive_id"], "depth_m": "8.0",
                     "time_utc": f"2026-09-16T02:{i:02d}:00+00:00"})
    (root / "ingest").mkdir()
    with (root / "ingest" / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    write_stage(root / "ingest", "ingest", params={"dataset_dir": str(ds)})
    (root / "patches").mkdir()
    (root / "patches" / "patches.json").write_text(json.dumps(
        {s: {"jpeg": {"patches": {"gray_mid": {"mean": [100, 120, 110]}}}} for s in scores}))
    (correct_dir / "scores.json").write_text(json.dumps(scores))
    (correct_dir / "frames.json").write_text(json.dumps(
        {s: {"depth_m": 1.0 if s == "F2" else 8.0} for s in scores}))
    (root / "fit").mkdir()
    (root / "fit" / "wb_points.csv").write_text("stem,dive,depth_m\nA,3,5.0\nB,4,16.0\n")
    (correct_dir / "summary.json").write_text(json.dumps(
        {"card_haze_cap_bound_frames": "3 of 4", "grvi_no_card_frames": []}))
    qc_dir.mkdir()
    (qc_dir / "qc.json").write_text(json.dumps(
        {s: {"patches": {"gray_white": {"usable": False}, "cyan": {"usable": True}}}
         for s in scores}))
    write_stage(correct_dir, "correct")
    cfg = tmp_path / "dataset.yaml"
    cfg.write_text("fit:\n  reference_category: cat\n  wb_categories: [cat]\n"
                   "  table_source_dives: ['3', '4']\n  flagged_dives: {'1': sunset}\n"
                   "blind_answers: answers.json\n")
    return root, correct_dir, cfg


def test_decide_stage_writes_numbers_and_pages(tmp_path):
    root, correct_dir, cfg = _stage_fixture(tmp_path)
    out = decide(correct_dir, cfg, CARD_PATH)
    result = json.loads((root / "decide" / "decision.json").read_text())
    anchored = {(c["nereus"], c["baseline"]): c
                for c in result["classes"]["card_anchored"]["comparisons"]}
    assert anchored[("raw_card_wb", "jpeg_card_wb")]["verdict"] == "pass"
    assert result["preset_pairs"]["n"] == 1 and list(result["flash"]) == ["FL"]
    assert any("gray_white" in x for x in out["needs_v3"])
    assert any("Dive 1" in x for x in out["needs_v3"])
    assert any("fitted on 5–16 m; 1 scored frames lie outside" in x for x in out["needs_v3"])
    page = (root / "decide" / "index.html").read_text()
    assert "Card-anchored" in page and "Blind review not done yet" in page
    # 10 sweep middles + the no-card frame (card-free), 10 sweep middles (card-anchored)
    assert out["blind_pairs"] == 21 and out["cut_frames"] == 12
    blind = re.sub(r"base64,[^\"]+", "", (root / "decide" / "blind.html").read_text())
    assert blind.count("<fieldset>") == 21
    for label in COLUMNS.values():  # method names never appear on the blind page
        assert label not in blind
    assert "GRVI" not in blind and "RAW" not in blind
    assert (root / "decide" / "cutsheet.html").read_text().count("<tr>") == 13


def test_saved_blind_answers_are_scored_against_the_key(tmp_path):
    root, correct_dir, cfg = _stage_fixture(tmp_path)
    stems = [f"F{i}" for i in range(0, 40, 4)]
    answers = {f"card_free:{s}": nereus_side("card_free", s) for s in stems}
    answers.update({f"card_anchored:{s}": "=" for s in stems[:5]})
    answers[f"card_anchored:{stems[5]}"] = "B" if nereus_side("card_anchored", stems[5]) == "A" \
        else "A"
    (tmp_path / "answers.json").write_text(json.dumps({"seed": SEED, "answers": answers}))
    out = decide(correct_dir, cfg, CARD_PATH)
    assert out["blind"]["card_free"] == {"nereus": 10, "baseline": 0, "tie": 0,
                                         "prefer_nereus": 1.0, "rule_pass": True}
    assert out["blind"]["card_anchored"]["baseline"] == 1
    assert out["blind"]["card_anchored"]["rule_pass"] is False
    assert str((tmp_path / "answers.json").resolve()) in verify_fresh(root / "decide")["configs"]
    with pytest.raises(ValueError, match="seed"):
        score_blind({"seed": "other", "answers": {}})


def test_blind_sides_are_stable_and_balanced():
    sides = [nereus_side("card_free", f"P{i}") for i in range(400)]
    assert sides == [nereus_side("card_free", f"P{i}") for i in range(400)]
    assert 0.4 < sides.count("A") / 400 < 0.6


def test_card_free_is_never_compared_with_card_anchored_columns():
    result = decide_numbers(synthetic())
    for c in result["classes"]["card_free"]["comparisons"]:
        assert c["baseline"] in ("camera_jpeg", "olympus_preset_jpeg")
