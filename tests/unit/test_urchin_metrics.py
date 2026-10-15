"""Reef Check urchin study: detection scoring (edge_ml/urchin/PREREG.md, "Metric")."""

import math

import numpy as np
import pytest
from edge_ml.urchin import metrics as m


def test_iou_matrix_known_values():
    a = np.array([[0, 0, 10, 10]])
    b = np.array([[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]])
    np.testing.assert_allclose(m.iou_matrix(a, b), [[1.0, 50 / 150, 0.0]])
    assert m.iou_matrix(np.zeros((0, 4)), b).shape == (0, 3)


def test_match_tp_fp_fn_and_duplicate_is_fp():
    gt = [[0, 0, 10, 10], [100, 100, 110, 110]]
    dets = [[0, 0, 10, 10], [1, 1, 10, 10], [50, 50, 60, 60]]
    r = m.match_image(dets, [0.9, 0.8, 0.7], gt)
    assert r.n_gt == 2
    assert r.tp.tolist() == [True, False, False]  # 2nd box duplicates a matched label → FP
    c = m.counts_at([r], 0.0)
    assert (c.tp, c.fp, c.fn) == (1, 2, 1)


def test_match_prefers_highest_score_and_best_unmatched_label():
    gt = [[0, 0, 10, 10], [6, 0, 16, 10]]
    # Low-score det fits gt0 perfectly; high-score det overlaps both, gt1 more.
    dets = [[0, 0, 10, 10], [5, 0, 15, 10]]
    r = m.match_image(dets, [0.3, 0.9], gt)
    assert r.scores.tolist() == [0.9, 0.3]
    assert r.tp.tolist() == [True, True]


def test_detection_on_ignore_box_is_not_counted():
    r = m.match_image([[0, 0, 10, 10], [50, 50, 60, 60]], [0.9, 0.8], [], [[0, 0, 10, 10]])
    assert r.n_gt == 0
    assert r.tp.tolist() == [False]  # only the det off the ignore box remains, as an FP


def test_counts_threshold_and_f1():
    r = m.match_image([[0, 0, 10, 10], [50, 50, 60, 60]], [0.9, 0.4], [[0, 0, 10, 10]])
    hi = m.counts_at([r], 0.5)
    assert (hi.tp, hi.fp, hi.fn) == (1, 0, 0) and hi.f1 == 1.0
    lo = m.counts_at([r], 0.0)
    assert lo.precision == 0.5 and lo.recall == 1.0
    assert math.isnan(m.Counts(0, 0, 0).precision)


def test_ap50_perfect_and_half():
    perfect = m.match_image([[0, 0, 10, 10]], [0.9], [[0, 0, 10, 10]])
    assert m.ap50([perfect]) == pytest.approx(1.0)
    # One TP at top, one label never found → recall tops out at 0.5.
    half = m.match_image([[0, 0, 10, 10]], [0.9], [[0, 0, 10, 10], [50, 50, 60, 60]])
    assert m.ap50([half]) == pytest.approx(0.5)
    # FP ranked above the only TP → precision 0.5 at recall 1.
    fp_first = m.match_image([[50, 50, 60, 60], [0, 0, 10, 10]], [0.9, 0.8], [[0, 0, 10, 10]])
    assert m.ap50([fp_first]) == pytest.approx(0.5)


def test_best_f1_threshold_picks_cut_and_highest_on_ties():
    # Scores 0.9 TP, 0.8 FP, 0.7 TP, 0.1 FP; 2 labels. F1 at cuts: .667, .5, .8, .667.
    r = m.match_image(
        [[0, 0, 10, 10], [200, 200, 210, 210], [100, 100, 110, 110], [300, 300, 310, 310]],
        [0.9, 0.8, 0.7, 0.1],
        [[0, 0, 10, 10], [100, 100, 110, 110]],
    )
    assert m.best_f1_threshold([r]) == pytest.approx(0.7)
    assert m.best_f1_threshold([m.ImageMatch(np.zeros(0), np.zeros(0, bool), 3)]) == math.inf


def test_block_bootstrap_is_paired_and_deterministic():
    blocks = [m.block_id("v1", t) for t in (0, 10, 40, 70)] + [m.block_id("v2", 5)]
    assert blocks[:2] == ["v1#0", "v1#0"]
    vals = np.array([1.0, 1.0, 2.0, 3.0, 4.0])
    pt, lo, hi = m.block_bootstrap(blocks, lambda idx: float(vals[idx].mean()), 500, seed=1)
    assert pt == pytest.approx(2.2)
    assert 1.0 <= lo <= pt <= hi <= 4.0
    assert m.block_bootstrap(blocks, lambda idx: float(vals[idx].mean()), 500, seed=1)[1] == lo
