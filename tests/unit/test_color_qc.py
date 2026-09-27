"""``qc`` stage (SPEC §4 Phase 8 S1.7): exclude, never repair — frame filter, known damage
map, cell-ratio test against the clean baseline, and ``damage: unknown`` for small patches."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.qc import cell_ratio, qc
from nereus_camera_test_rig.color.stages import StaleInputError, verify_fresh, write_stage

REPO = Path(__file__).resolve().parents[2]
CARD_PATH = REPO / "configs" / "cards" / "nereus_v2.yaml"
CARD = load_card(CARD_PATH)
IDS = [p.id for p in CARD.patches] + [s.id for s in CARD.sub_patches]
RNG = np.random.default_rng(0)


def stats(level=0.3, spread=0.02, n=100, size=40.0, clip=0.0, in_frame=True, blotch=1.0):
    cells = level * (1 + spread * RNG.uniform(-1, 1, (3, 3, 3)))
    cells[1, 1] *= blotch
    return {"n_px": 9 * n, "size_px": [size, size], "in_frame": in_frame,
            "mean": cells.mean(axis=(0, 1)).tolist(), "std": [0.01] * 3,
            "clip_frac": [clip, 0.0, 0.0], "cell_n": [[n] * 3] * 3, "cells": cells.tolist()}


def frame(**overrides):
    return {"raw": {"patches": {pid: overrides.get(pid, stats()) for pid in IDS}}}


# stem: (minute, flash)
SHOTS = {"C1": 0, "C2": 1, "C3": 2, "C4": 3, "D1": 10, "D2": 11, "F1": 12, "T1": 13, "S1": 14}


def run(tmp_path, frames, config_extra=None):
    root = tmp_path / "ds1"
    (root / "ingest").mkdir(parents=True)
    with (root / "ingest" / "manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["stem", "time_utc", "flash_fired"])
        w.writeheader()
        for stem, minute in SHOTS.items():
            w.writerow({"stem": stem, "time_utc": f"2026-09-16T01:{minute:02d}:00+00:00",
                        "flash_fired": stem == "F1"})
    write_stage(root / "ingest", "ingest")
    pdir = root / "patches"
    pdir.mkdir()
    (pdir / "patches.json").write_text(json.dumps(frames))
    write_stage(pdir, "patches", upstream=[root / "ingest"])
    cfg = {"card_damage": {"from_stem": "D1",
                           "patches": ["gray_white", "gray_light", "gray_mid_left"],
                           "clean_reference": ["C1", "C4"]},
           "torch_frames": ["T1"], **(config_extra or {})}
    cfg_path = tmp_path / "dataset.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    summary = qc(pdir, cfg_path, CARD_PATH)
    return summary, json.loads((Path(summary["out_dir"]) / "qc.json").read_text()), cfg_path


def base_frames():
    return {s: frame() for s in SHOTS}


def test_cell_ratio():
    assert cell_ratio(stats(n=10)) is None  # cells too small to test
    r = cell_ratio(stats(spread=0.0, blotch=0.5))
    np.testing.assert_allclose(r, [2.0, 2.0, 2.0])


def test_known_damage_map_applies_from_the_first_damaged_frame(tmp_path):
    _, out, _ = run(tmp_path, base_frames())
    assert out["C1"]["card_condition"] == "clean" and out["D1"]["card_condition"] == "damaged"
    d = out["D1"]["patches"]
    for pid in ("gray_white", "gray_light", "gray_mid_left", "gray_mid"):  # parent goes too
        assert not d[pid]["usable"] and d[pid]["damage"] == "known", pid
    assert d["gray_mid_right"]["usable"] and d["gray_mid_right"]["damage"] == "clean"
    assert out["C2"]["patches"]["gray_white"]["usable"]


def test_blotched_patch_fails_the_cell_test_and_is_excluded_not_repaired(tmp_path):
    frames = base_frames()
    frames["D2"] = frame(blue=stats(blotch=0.6))
    summary, out, _ = run(tmp_path, frames)
    blue = out["D2"]["patches"]["blue"]
    assert not blue["usable"] and blue["damage"] == "cells"
    assert "cell ratio" in blue["reasons"][0]
    assert out["D2"]["usable"]  # the frame survives; only the patch goes


def test_small_patches_are_damage_unknown_never_clean(tmp_path):
    frames = base_frames()
    frames["D2"] = frame(cyan=stats(n=10))
    _, out, _ = run(tmp_path, frames)
    assert out["D2"]["patches"]["cyan"]["damage"] == "unknown"
    assert out["D2"]["patches"]["cyan"]["usable"]


def test_frame_filter(tmp_path):
    frames = base_frames()
    frames["S1"] = frame(gray_dark=stats(size=8.0))  # 8 binned px at 60 % → 27 RAW px
    frames["C3"] = frame(orange=stats(clip=0.05), yellow=stats(in_frame=False))
    summary, out, _ = run(tmp_path, frames)
    assert out["F1"]["reasons"] == ["flash fired"] and not out["F1"]["usable"]
    assert out["T1"]["reasons"] == ["diver torch in view"]
    assert out["S1"]["reasons"] == ["grey patch < 30 RAW px"]
    assert "clipped" in out["C3"]["patches"]["orange"]["reasons"][0]
    assert "partly outside the image" in out["C3"]["patches"]["yellow"]["reasons"]
    assert summary["clean_reference_frames"] == ["C1", "C2", "C3", "C4"]
    assert summary["by_card_condition"]["damaged"]["frames"] == 5


def test_stage_is_stale_when_the_damage_map_changes(tmp_path):
    summary, _, cfg = run(tmp_path, base_frames())
    verify_fresh(Path(summary["out_dir"]))
    cfg.write_text(cfg.read_text() + "# edited\n")
    with pytest.raises(StaleInputError):
        verify_fresh(Path(summary["out_dir"]))
