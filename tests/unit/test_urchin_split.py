"""Reef Check urchin study: sampling + held-out split (edge_ml/urchin/PREREG.md)."""

import pytest
from edge_ml.urchin import split as sp


def _stills(video, n, step=2.0):
    return [sp.Still(f"{video}_t{int(i * step * 10):06d}.jpg", video, i * step) for i in range(n)]


def test_proportional_counts_largest_remainder():
    assert sp.proportional_counts({"a": 50, "b": 30, "c": 20}, 10) == {"a": 5, "b": 3, "c": 2}
    assert sum(sp.proportional_counts({"a": 7, "b": 7, "c": 7}, 10).values()) == 10
    assert sp.proportional_counts({"a": 3, "b": 4}, 100) == {"a": 3, "b": 4}


def test_proportional_sample_is_seeded_and_proportional():
    stills = _stills("v1", 300) + _stills("v2", 100)
    s1 = sp.proportional_sample(stills, 100, seed=0)
    assert len(s1) == 100 and sum(s.video == "v1" for s in s1) == 75
    assert s1 == sp.proportional_sample(stills, 100, seed=0)
    assert s1 != sp.proportional_sample(stills, 100, seed=1)


def test_evenly_spaced_screen():
    assert len(sp.evenly_spaced(_stills("v", 10), 40)) == 10
    scr = sp.evenly_spaced(_stills("v", 400), 40)
    assert len(scr) == 40 and scr[0].t_s == 0 and scr[-1].t_s == 399 * 2.0


def test_time_split_discards_gap():
    first, last = sp.time_split(_stills("v", 101, step=1.0), 0.7)  # t = 0..100 s, cut at 70
    assert max(s.t_s for s in first) < 40 and min(s.t_s for s in last) == 70


def _groups(n_groups, per=50):
    stills, groups = [], {}
    for g in range(n_groups):
        v = f"vid{g}"
        stills += _stills(v, per)
        groups[v] = f"site{g}"
    return stills, groups


def test_four_plus_groups_hold_out_a_third_by_hash_and_never_leak():
    stills, groups = _groups(6)
    (fold,) = sp.make_folds(stills, groups, set(groups.values()))
    assert fold.mode == "held_out_groups" and len(fold.test_groups) == 2
    expected = sorted(groups.values(), key=sp.name_hash)[:2]
    assert fold.test_groups == expected
    tg = {groups[s.video] for s in fold.test}
    assert tg == set(expected)
    assert not tg & {groups[s.video] for s in fold.train + fold.val}
    # 4 training groups → validation is one whole group (lowest hash of the training groups)
    assert len({groups[s.video] for s in fold.val}) == 1


def test_ineligible_groups_only_train():
    stills, groups = _groups(5)
    elig = {"site0", "site1", "site2", "site3"}
    (fold,) = sp.make_folds(stills, groups, elig)
    assert "site4" not in fold.test_groups
    assert any(groups[s.video] == "site4" for s in fold.train + fold.val)


def test_two_groups_leave_one_out_with_time_val():
    stills, groups = _groups(2)
    folds = sp.make_folds(stills, groups, {"site0", "site1"})
    assert [f.mode for f in folds] == ["leave_one_group_out"] * 2
    for f in folds:
        (g,) = f.test_groups
        assert all(groups[s.video] != g for s in f.train + f.val)
        assert f.val and min(s.t_s for s in f.val) - max(s.t_s for s in f.train) >= sp.GAP_S


def test_one_group_is_same_dive_split():
    stills, groups = _groups(1, per=200)
    (fold,) = sp.make_folds(stills, groups, {"site0"})
    assert fold.mode == "same_dive"
    assert min(s.t_s for s in fold.test) - max(s.t_s for s in fold.train + fold.val) >= sp.GAP_S


def test_no_eligible_group_fails_loudly():
    stills, groups = _groups(2)
    with pytest.raises(ValueError, match="no eligible"):
        sp.make_folds(stills, groups, set())
