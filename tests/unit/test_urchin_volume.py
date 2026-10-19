"""E6 volume rule (PREREG Labels, amended 2026-10-18; SPEC_e6_prelabel_rfdetr rev 3 must do 4)."""

import json
from datetime import datetime, timedelta

import pytest
from edge_ml.urchin import review
from edge_ml.urchin import volume as vol

Image = pytest.importorskip("PIL.Image")


def _pre(scores_per_still: dict[str, list[float]]) -> dict:
    dets = {
        s: [{"box": [10.0 + i, 10.0, 40.0 + i, 40.0], "score": sc} for i, sc in enumerate(v)]
        for s, v in scores_per_still.items()
    }
    return {"model": "m", "query": None, "min_score": 0.05, "detections": dets}


def test_timed_ids_every_kth_at_or_above_0p1_in_candidate_ids_order():
    # 3 stills; sorted still order b < c < d; scores alternate above/below 0.1
    pre = _pre({"d.jpg": [0.9, 0.05] * 100, "b.jpg": [0.1, 0.09] * 150, "c.jpg": [0.5] * 40})
    pool = [cid for cid, _, _, s in review.candidate_ids(pre) if s >= 0.1]
    assert len(pool) == 150 + 40 + 100  # 0.1 itself counts
    ids = vol.timed_ids(pre)
    k = len(pool) // 96  # = 3
    assert k == 3 and ids == pool[::3][:96] and len(ids) == 96
    picked = {s for cid, s, _, _ in review.candidate_ids(pre) if cid in set(ids)}
    assert ids[0] == pool[0] and len(picked) == 3  # spread over all stills


def test_timed_ids_takes_all_when_fewer_than_96():
    pre = _pre({"a.jpg": [0.2] * 50 + [0.06] * 10})
    assert vol.timed_ids(pre) == list(range(50))


def test_projection_and_the_one_switch():
    pre = _pre({"a.jpg": [0.5] * 1000 + [0.06] * 2000})  # 3000 at 0.05, 1000 at 0.1
    start = datetime(2026, 10, 20, 12, 0, tzinfo=vol.PT)  # 24 h before the deadline
    fast = vol.project(pre, 96, 96 / 200 * 3600, start)  # 200 crops/h: 15 h vs 5 h
    assert fast["rate_crops_per_h"] == 200.0
    assert fast["projections"]["0.05"] == {"candidates": 3000, "hours": 15.0,
        "ends": (start + timedelta(hours=15)).isoformat(), "fits": True}  # fmt: skip
    assert fast["floor"] == 0.05
    mid = vol.project(pre, 96, 96 / 100 * 3600, start)  # 30 h vs 10 h
    assert mid["floor"] == 0.1 and "ALL stills" in mid["decision"]
    slow = vol.project(pre, 96, 96 / 40 * 3600, start)  # 75 h vs 25 h
    assert slow["floor"] is None and "STOP" in slow["decision"]
    exact = vol.project(pre, 96, 96 / 125 * 3600, start)  # 0.05 ends exactly at the deadline
    assert (
        exact["projections"]["0.05"]["ends"] == vol.DEADLINE.isoformat() and exact["floor"] == 0.05
    )


def test_floor_filters_every_still_and_refuses_going_below_the_run(tmp_path):
    pre = _pre({"a.jpg": [0.5, 0.1, 0.09], "b.jpg": [0.05]})
    out = vol.apply_floor(pre, 0.1)
    assert [d["score"] for d in out["detections"]["a.jpg"]] == [0.5, 0.1]
    assert out["detections"]["b.jpg"] == [] and out["min_score"] == 0.1
    assert out["floor_from"] == 0.05 and pre["detections"]["a.jpg"][2]["score"] == 0.09
    with pytest.raises(ValueError, match="below the run"):
        vol.apply_floor(pre, 0.01)


def test_cli_timed_sheets_keep_full_set_ids(tmp_path):
    root = tmp_path / "stills"
    (root / "v").mkdir(parents=True)
    for n in ("a", "b"):
        Image.new("RGB", (200, 120), (30, 60, 90)).save(root / "v" / f"{n}.jpg")
    pre = _pre({"v/a.jpg": [0.3, 0.06] * 60, "v/b.jpg": [0.2] * 100})
    pj = tmp_path / "pre.json"
    pj.write_text(json.dumps(pre))
    out = tmp_path / "timed"
    assert vol.main(["timed-sheets", str(pj), str(root), str(out)]) == 0
    ids = json.loads((out / "timed_ids.json").read_text())
    on_sheets = sum((json.loads((out / f"sheet_{i:03d}.json").read_text()) for i in (0, 1)), [])
    assert len(ids) == 96 and on_sheets == ids  # 2 sheets of 48, numbered by full-set id
    assert not (out / "sheet_002.json").exists()
    res = tmp_path / "volume.json"
    assert vol.main(["project", str(pj), "1800", str(res), "--start",
                     "2026-10-21T11:00:00-07:00"]) == 0  # fmt: skip
    got = json.loads(res.read_text())
    assert got["rate_crops_per_h"] == 192.0 and got["projections"]["0.1"]["candidates"] == 160
    assert got["floor"] == 0.1  # 220 at 0.05 = 1.15 h > 1 h left; 160 at 0.1 = 0.83 h
