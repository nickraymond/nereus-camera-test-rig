"""Reef Check urchin study: evaluation + PREREG bar on a synthetic fold."""

import json

import pytest
from edge_ml.urchin import evaluate as ev
from edge_ml.urchin.review import to_coco
from edge_ml.urchin.train_b import RECIPE


def _write_fold(tmp_path, n_per_video=30, mode="held_out_groups", b_quality="perfect"):
    fold = tmp_path / "fold"
    groups = {"vA1": "g1", "vB1": "g2"}
    stills, labels = [], {}
    for video in groups:
        for i in range(n_per_video):
            name = f"{video}_t{i * 20:06d}.jpg"  # one still per 2 s
            stills.append((name, 640, 480))
            labels[name] = [{"box": [10, 10, 50, 50]}, {"box": [200, 200, 240, 240]}]
    coco = to_coco(stills, labels)
    fold.mkdir(parents=True)
    (fold / "fold.json").write_text(
        json.dumps({"name": "f", "mode": mode, "test_groups": ["g1", "g2"], "train_groups": ["g0"]})
    )
    (fold / "labels_val.json").write_text(json.dumps(coco))
    (fold / "labels_test.json").write_text(json.dumps(coco))

    def preds(kind):
        out = {}
        for name, _, _ in stills:
            if kind == "perfect":
                out[name] = [
                    {"box": [10, 10, 50, 50], "score": 0.9},
                    {"box": [200, 200, 240, 240], "score": 0.8},
                ]
            else:  # half: finds one urchin, plus one false positive
                out[name] = [
                    {"box": [10, 10, 50, 50], "score": 0.6},
                    {"box": [400, 400, 440, 440], "score": 0.5},
                ]
        return {"detections": out, "min_score": 0.01}

    subs = [("a", "half")] + [(f"b/seed{k}", b_quality) for k in range(3)]
    for sub, kind in subs:
        (fold / sub).mkdir(parents=True)
        for split in ("val", "test"):
            (fold / sub / f"pred_labels_{split}.json").write_text(json.dumps(preds(kind)))
        if sub.startswith("b/"):
            run = {"epochs_run": 24, "recipe": RECIPE, "seed": int(sub[-1])}
            (fold / sub / "run.json").write_text(json.dumps(run))
    return fold, groups


def test_still_video_time():
    assert ev.still_video_time("GX010042_t000015.jpg") == ("GX010042", 1.5)
    with pytest.raises(ValueError):
        ev.still_video_time("bad.jpg")


def test_helped_when_b_clearly_beats_a(tmp_path):
    fold, groups = _write_fold(tmp_path)
    r = ev.evaluate_fold(fold, groups)
    assert r["n_test_boxes"] == 120 and r["n_test_videos"] == 2
    # Max-F1 threshold on validation (0.6) cuts A's false positive: F1 = 120 / 180.
    assert r["A"]["threshold"] == pytest.approx(0.6)
    assert r["A"]["f1"] == pytest.approx(2 / 3)
    b = r["B"]["seed0"]
    assert b["f1"] == pytest.approx(1.0)
    assert b["delta_f1"]["point"] == pytest.approx(1 / 3)
    assert b["delta_f1"]["ci95"][0] > 0 and b["delta_f1"]["n_nonfinite_dropped"] == 0
    assert all(b["bar"].values())
    assert ev.outcome([r])[0] == "helped"


def test_not_shown_when_b_equals_a(tmp_path):
    fold, groups = _write_fold(tmp_path, b_quality="half")
    r = ev.evaluate_fold(fold, groups)
    assert ev.outcome([r])[0] == "not shown"


def test_inconclusive_when_too_few_boxes_or_same_dive(tmp_path):
    fold, groups = _write_fold(tmp_path, n_per_video=10)  # 40 boxes < 100
    assert ev.outcome([ev.evaluate_fold(fold, groups)])[0].startswith("inconclusive — need")
    fold2, groups2 = _write_fold(tmp_path / "x", mode="same_dive")
    assert ev.outcome([ev.evaluate_fold(fold2, groups2)])[0] == "inconclusive (same dive)"


def test_main_writes_reports(tmp_path):
    fold, groups = _write_fold(tmp_path)
    (tmp_path / "groups.json").write_text(json.dumps(groups))
    out = tmp_path / "out"
    assert (
        ev.main(["--fold", str(fold), "--groups", str(tmp_path / "groups.json"), "--out", str(out)])
        == 0
    )
    data = json.loads((out / "eval.json").read_text())
    assert data["outcome"] == "helped"
    assert "_counts" not in json.dumps(data)
    assert "Outcome: helped" in (out / "eval.md").read_text()


def test_empty_validation_fails_loudly(tmp_path):
    fold, groups = _write_fold(tmp_path)
    coco = json.loads((fold / "labels_val.json").read_text())
    coco["annotations"] = []
    (fold / "labels_val.json").write_text(json.dumps(coco))
    with pytest.raises(ValueError, match="validation has no label boxes"):
        ev.evaluate_fold(fold, groups)


def test_claim_scope_when_no_new_site_is_held_out(tmp_path):
    fold, groups = _write_fold(tmp_path)
    groups = {"vA1": "S1|2024-01-01", "vB1": "S2|2024-01-01"}
    info = {
        "name": "f",
        "mode": "held_out_groups",
        "test_groups": sorted(groups.values()),
        "train_groups": ["S1|2023-01-01", "S2|2023-01-01"],
    }
    (fold / "fold.json").write_text(json.dumps(info))
    r = ev.evaluate_fold(fold, groups)
    assert r["secondary_new_vs_same_site"]["new_site"]["groups"] == []
    assert r["secondary_new_vs_same_site"]["same_site"]["n_boxes"] == 120
    assert ev.outcome([r])[0] == "helped on other dives at sites in training"
    info["train_groups"] = ["S1|2023-01-01"]  # S2 is now a new site → plain "helped"
    (fold / "fold.json").write_text(json.dumps(info))
    r = ev.evaluate_fold(fold, groups)
    assert r["secondary_new_vs_same_site"]["new_site"]["groups"] == ["S2|2024-01-01"]
    assert ev.outcome([r])[0] == "helped"


def test_fold_json_without_train_groups_fails_closed(tmp_path):
    fold, groups = _write_fold(tmp_path)
    info = json.loads((fold / "fold.json").read_text())
    del info["train_groups"]
    (fold / "fold.json").write_text(json.dumps(info))
    with pytest.raises(KeyError, match="train_groups"):
        ev.evaluate_fold(fold, groups)


def test_incomplete_unless_exactly_three_full_seed_runs(tmp_path):
    import shutil

    fold, groups = _write_fold(tmp_path)
    shutil.rmtree(fold / "b" / "seed2")  # a crashed seed
    r = ev.evaluate_fold(fold, groups)
    assert ev.outcome([r]) == ("incomplete", "f: seed2: missing run.json")
    fold, groups = _write_fold(tmp_path / "smoke")
    run = json.loads((fold / "b/seed1/run.json").read_text())
    run["epochs_run"] = 2  # a smoke run
    (fold / "b/seed1/run.json").write_text(json.dumps(run))
    r = ev.evaluate_fold(fold, groups)
    assert ev.outcome([r])[0] == "incomplete" and "seed1: epochs_run 2" in ev.outcome([r])[1]
    assert "seed1" not in r["B"]


def test_scoring_floor_is_pinned(tmp_path):
    fold, groups = _write_fold(tmp_path)
    d = json.loads((fold / "a/pred_labels_test.json").read_text())
    d["min_score"] = 0.05  # owl.py's pre-label default, not the scoring floor
    (fold / "a/pred_labels_test.json").write_text(json.dumps(d))
    with pytest.raises(ValueError, match="min_score"):
        ev.evaluate_fold(fold, groups)
