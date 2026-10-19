"""E6: Nick's RF-DETR vs the pilot's calls (image-level comparison helpers)."""

import numpy as np
from edge_ml.urchin import compare_rfdetr as cr
from edge_ml.urchin import owl


def test_auc_mann_whitney_with_ties():
    assert cr.auc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert cr.auc([0.1], [0.9]) == 0.0
    assert cr.auc([0.5], [0.5]) == 0.5
    assert cr.auc([], [0.1]) is None


def test_tiles_match_owl_tiling():
    for length in (480, 1080, 2160, 3840, 5312):
        assert cr.tile_starts(length, 1080) == owl.tile_starts(length, 1080)


def test_nms_keeps_best_and_drops_overlaps():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 10, 10], [50, 50, 60, 60]], float)
    assert cr.nms(boxes, np.array([0.5, 0.9, 0.3])) == [1, 2]


def test_compare_groups_and_thresholds():
    calls = [
        {"still": "a", "urchin": "yes", "note": ""},
        {"still": "b", "urchin": "yes", "note": ""},
        {"still": "c", "urchin": "no", "note": "long-spined red type"},
        {"still": "d", "urchin": "no", "note": "rocks"},
        {"still": "e", "urchin": "unsure", "note": ""},
    ]
    dets = {
        "a": [{"score": 0.9}],
        "b": [{"score": 0.4}],
        "c": [{"score": 0.7}],
        "e": [{"score": 0.6}],
    }
    out = cr.compare(calls, {"m": dets})
    assert out["n"] == {"yes": 2, "no": 2, "no_red": 1, "no_other": 1, "unsure": 1}
    m = out["models"]["m"]
    assert m["flagged@0.5"]["yes"] == "1/2" and m["flagged@0.3"]["yes"] == "2/2"
    assert m["flagged@0.5"]["no_red"] == "1/1" and m["flagged@0.5"]["no_other"] == "0/1"
    assert m["auc_yes_vs_no_other"] == 1.0 and m["auc_yes_vs_no"] == 0.75
