"""Reef Check urchin study: sampling + held-out split (edge_ml/urchin/PREREG.md)."""

import json

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


def test_validation_is_lowest_hash_eligible_training_group():
    stills, groups = _groups(6)
    train_groups = sorted(groups.values(), key=sp.name_hash)
    inelig = train_groups[0]  # the lowest hash overall has no urchins
    train = [s for s in stills]
    tr, val = sp.train_val(train, groups, set(groups.values()) - {inelig})
    assert {groups[s.video] for s in val} == {train_groups[1]}
    assert any(groups[s.video] == inelig for s in tr)


def test_validation_falls_back_to_time_split_without_eligible_training_group():
    stills, groups = _groups(3)
    tr, val = sp.train_val(stills, groups, set())
    assert len({groups[s.video] for s in val}) == 3  # last 20 % of every video


def test_fold_info_lists_train_and_val_groups():
    stills, groups = _groups(6)
    (fold,) = sp.make_folds(stills, groups, set(groups.values()))
    info = fold.info(groups)
    assert set(info["test_groups"]).isdisjoint(info["train_groups"] + info["val_groups"])
    assert info["n"]["test"] == len(fold.test)


def test_frozen_reefcheck_group_map_matches_prereg():
    import hashlib
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "edge_ml" / "urchin"
    raw = (root / "groups_reefcheck.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() in (root / "PREREG.md").read_text()
    gmap = json.loads(raw)
    assert len(gmap) == 29 and len(set(gmap.values())) == 7
    assert gmap["South Wreck-25-09-16-0935-TrueBarnes-f9"] == "South Wreck|2025-09-11"


def _stills_csv(tmp_path, n_videos=6, per=60):
    import csv

    path = tmp_path / "stills.csv"
    groups = {}
    with path.open("w", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["video", "index", "t_s", "sharpness", "dhash", "status", "still"]
        )
        w.writeheader()
        for v in range(n_videos):
            stem = f"Site{v % 3} clip{v}"
            groups[stem] = f"Site{v % 3}|2024-0{v // 3 + 1}-01"
            for i in range(per):
                w.writerow(
                    {
                        "video": f"{stem}.MP4",
                        "index": i * 4,
                        "t_s": i * 2.0,
                        "sharpness": 1,
                        "dhash": "0",
                        "status": "kept",
                        "still": f"{stem}_t{i * 20:06d}.jpg",
                    }
                )
            w.writerow(
                {
                    "video": f"{stem}.MP4",
                    "index": 1,
                    "t_s": 0.5,
                    "sharpness": 0,
                    "dhash": "0",
                    "status": "blur",
                    "still": "",
                }
            )
    (tmp_path / "groups.json").write_text(json.dumps(groups))
    return path, groups


def test_cli_screen_then_folds(tmp_path, monkeypatch):
    import csv

    stills_csv, groups = _stills_csv(tmp_path)
    assert sp.main(["q1-sample", str(stills_csv), str(tmp_path / "q1.txt")]) == 0
    q1 = (tmp_path / "q1.txt").read_text().splitlines()
    assert len(q1) == 100 and all("/" in x for x in q1)

    screen = tmp_path / "group_screen.csv"
    assert (
        sp.main(["screen-list", str(stills_csv), str(tmp_path / "groups.json"), str(screen)]) == 0
    )
    rows = list(csv.DictReader(screen.open()))
    order = [r["group"] for r in rows]
    assert order == sorted(order)  # alphabetical, not hash order
    assert len(rows) == 6 * 40

    out = tmp_path / "folds"
    with pytest.raises(ValueError, match="not the map frozen"):  # synthetic map ≠ PREREG map
        sp.main(["folds", str(stills_csv), str(tmp_path / "groups.json"), str(screen), str(out)])
    prereg = tmp_path / "PREREG.md"
    prereg.write_text(sp.sha256_file(tmp_path / "groups.json"))
    monkeypatch.setattr(sp, "PREREG_PATH", prereg)
    with pytest.raises(ValueError, match="want yes/no/unsure"):  # blank screen
        sp.main(["folds", str(stills_csv), str(tmp_path / "groups.json"), str(screen), str(out)])

    for r in rows:
        r["purple_urchin"] = "unsure" if r["group"].startswith("Site0") else "no"
    rows[-1]["purple_urchin"] = "yes"
    with screen.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["group", "still", "purple_urchin", "notes"])
        w.writeheader()
        w.writerows(rows)
    elig = sp.eligible_from_screen(screen, set(groups.values()))
    assert elig == {"Site0|2024-01-01", "Site0|2024-02-01", rows[-1]["group"]}
    assert (
        sp.main(["folds", str(stills_csv), str(tmp_path / "groups.json"), str(screen), str(out)])
        == 0
    )
    dirs = sorted(d for d in out.iterdir() if d.is_dir())
    assert len(dirs) == 3  # 3 eligible groups → leave-one-group-out
    for fold_dir in dirs:
        assert "|" not in fold_dir.name and " " not in fold_dir.name
        info = json.loads((fold_dir / "fold.json").read_text())
        assert info["mode"] == "leave_one_group_out"
        (tg,) = info["test_groups"]
        assert tg in elig and tg not in info["train_groups"] + info["val_groups"]
        assert len((fold_dir / "train.txt").read_text().splitlines()) <= sp.CAPS["train"]
        test = (fold_dir / "test.txt").read_text().splitlines()
        assert len(test) == info["n"]["test"] <= sp.CAPS["test"]
        assert all(groups[x.split("/")[0]] == tg for x in test)
        assert info["inputs_sha256"]["group_screen"] == sp.sha256_file(screen)


def test_two_eligible_training_groups_use_time_split_not_a_group():
    stills, groups = _groups(4)
    elig = set(sorted(groups.values())[:2])
    tr, val = sp.train_val(stills, groups, elig)
    assert {groups[s.video] for s in val} == set(groups.values())  # last 20 % of every video
    assert elig <= {groups[s.video] for s in tr}


def test_b_never_loses_all_urchin_groups_on_the_real_map():
    """QE #118: every eligible subset of the frozen 7-group map, every fold."""
    from itertools import combinations
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "edge_ml" / "urchin"
    gmap = json.loads((root / "groups_reefcheck.json").read_text())
    stills = [s for v in gmap for s in _stills(v, 120)]
    names = sorted(set(gmap.values()))
    checked = 0
    for k in range(1, len(names) + 1):
        for elig in map(set, combinations(names, k)):
            for fold in sp.make_folds(stills, gmap, elig):
                train_g = {gmap[s.video] for s in fold.train}
                avail = ({gmap[s.video] for s in fold.train + fold.val}) & elig
                if avail:
                    assert train_g & elig, (sorted(elig), fold.name)
                if len(avail) >= 3:
                    assert len(train_g & elig) >= 2, (sorted(elig), fold.name)
                if fold.mode == "same_dive":
                    assert {gmap[s.video] for s in fold.val} <= train_g  # time split
                checked += 1
    assert checked > 127


def test_write_folds_fails_when_a_held_out_group_has_no_test_stills(tmp_path):
    stills, groups = _groups(3, per=20)
    fold = sp.Fold(
        "f",
        train=[s for s in stills if s.video == "vid2"],
        test=[s for s in stills if s.video == "vid0"],  # site1 held out but has no stills
        test_groups=["site0", "site1"],
        mode="held_out_groups",
    )
    with pytest.raises(ValueError, match=r"\['site1'\] have 0 test stills"):
        sp.write_folds([fold], groups, tmp_path / "out")
    assert not (tmp_path / "out").exists()  # nothing written
