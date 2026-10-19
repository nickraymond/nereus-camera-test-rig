"""E6 full-run labels: decisions → SAM boxes → misses → COCO (PREREG Labels), with a fake SAM."""

import json

import pytest
from edge_ml.urchin import labels as lb
from edge_ml.urchin import review

Image = pytest.importorskip("PIL.Image")


class FakeSam:
    """refine_box shrinks each box by 1 px per side; point_box makes a 40 px box (or fails)."""

    def __init__(self, fail_points=()):
        self.fail = set(fail_points)
        self.calls = 0

    def refine_box(self, rgb, box):
        self.calls += 1
        x1, y1, x2, y2 = box
        return {"box": [x1 + 1, y1 + 1, x2 - 1, y2 - 1], "sam_iou": 0.9, "sam_ok": True}

    def point_box(self, rgb, x, y):
        if (x, y) in self.fail:
            return {"box": None, "sam_iou": 0.1, "sam_ok": False}
        return {"box": [x - 20, y - 20, x + 20, y + 20], "sam_iou": 0.9, "sam_ok": True}


def _setup(tmp_path):
    root = tmp_path / "stills"
    (root / "v").mkdir(parents=True)
    for n in ("a", "b", "c"):
        Image.new("RGB", (640, 480), (20, 40, 60)).save(root / "v" / f"{n}.jpg")
    pre = {"model": "rfdetr", "min_score": 0.05, "detections": {
        "v/a.jpg": [{"box": [100, 100, 160, 160], "score": 0.9},   # 0 u
                    {"box": [102, 98, 162, 158], "score": 0.4},    # 1 u, duplicate of 0
                    {"box": [300, 300, 312, 340], "score": 0.3}],  # 2 u, 10 px wide after SAM
        "v/b.jpg": [{"box": [10, 10, 80, 80], "score": 0.8},       # 3 i
                    {"box": [12, 12, 82, 82], "score": 0.2}],      # 4 u, overlaps the ignore
        "v/c.jpg": [{"box": [5, 5, 50, 50], "score": 0.6}]}}  # 5 n (a negative still)  # fmt: skip
    sheets = tmp_path / "sheets"
    review.make_sheets(pre, root, sheets, per_sheet=4)  # ids 0-3, 4-5
    (sheets / "sheet_000.txt").write_text("u: 0 1 2\ni: 3\n")
    (sheets / "sheet_001.txt").write_text("u: 4  # n: 5 by omission\n")
    return root, pre, sheets


def test_assemble_rules(tmp_path):
    root, pre, sheets = _setup(tmp_path)
    sam = FakeSam()
    res = lb.assemble(pre, sheets, root, sam)
    assert res["decisions"] == {"u": 4, "i": 1, "n": 1} and sam.calls == 5
    st = res["stills"]
    assert set(st) == {"v/a.jpg", "v/b.jpg", "v/c.jpg"} and st["v/c.jpg"]["boxes"] == []
    assert (st["v/a.jpg"]["width"], st["v/a.jpg"]["height"]) == (640, 480)
    a = st["v/a.jpg"]["boxes"]
    assert [b["cand_id"] for b in a] == [0, 2]  # 1 merged into 0 (higher score kept)
    assert a[0]["box"] == [101, 101, 159, 159] and not a[0]["ignore"]
    assert a[1]["ignore"] and a[1]["small"]  # 10 px short side after SAM → ignore (PREREG)
    b = st["v/b.jpg"]["boxes"]
    assert [(x["cand_id"], x["ignore"]) for x in b] == [(4, False)]  # u wins over the ignore


def test_assemble_refuses_unreviewed_or_partial_sheets(tmp_path):
    root, pre, sheets = _setup(tmp_path)
    (sheets / "sheet_001.txt").unlink()
    with pytest.raises(ValueError, match="1 sheets not reviewed"):
        lb.assemble(pre, sheets, root, FakeSam())
    (sheets / "sheet_001.txt").write_text("u: 4\n")
    pre["detections"]["v/c.jpg"].append({"box": [1, 1, 30, 30], "score": 0.06})  # not on a sheet
    with pytest.raises(ValueError, match="cover 6 of 7"):
        lb.assemble(pre, sheets, root, FakeSam())


def test_misses_add_sam_boxes_and_list_failures(tmp_path):
    root, pre, sheets = _setup(tmp_path)
    labels = lb.assemble(pre, sheets, root, FakeSam())
    pts = review.parse_misses("v/c.jpg 200 200\nv/c.jpg 400 100 i\nv/a.jpg 500 400\n")
    out, failed = lb.add_misses(labels, pts, root, FakeSam(fail_points={(500.0, 400.0)}))
    c = out["stills"]["v/c.jpg"]["boxes"]
    assert [(b["box"], b["ignore"], b["source"]) for b in c] == [
        ([180, 180, 220, 220], False, "miss+sam"),
        ([380, 80, 420, 120], True, "miss+sam"),
    ]
    assert failed == ["v/a.jpg 500 400"] and out["misses"]["added"] == 2
    assert labels["stills"]["v/c.jpg"]["boxes"] == []  # input not modified
    with pytest.raises(ValueError, match="not in the labels"):
        lb.add_misses(labels, [("v/zz.jpg", 1.0, 1.0, False)], root, FakeSam())


def test_coco_per_fold_and_overlays(tmp_path):
    root, pre, sheets = _setup(tmp_path)
    labels = lb.assemble(pre, sheets, root, FakeSam())
    fold = tmp_path / "fold"
    fold.mkdir()
    (fold / "train.txt").write_text("v/a.jpg\n")
    (fold / "val.txt").write_text("v/b.jpg\n")
    (fold / "test.txt").write_text("v/c.jpg\n")
    assert lb.write_coco(labels, fold) == {"train": 2, "val": 1, "test": 0}
    coco = json.loads((fold / "labels_train.json").read_text())
    assert [a["ignore"] for a in coco["annotations"]] == [0, 1]
    assert coco["images"][0] == {"id": 1, "file_name": "v/a.jpg", "width": 640, "height": 480}
    (fold / "test.txt").write_text("v/zz.jpg\n")
    with pytest.raises(ValueError, match="have no labels"):
        lb.write_coco(labels, fold)
    n = lb.write_overlays(labels, ["v/a.jpg", "v/c.jpg"], root, tmp_path / "ov")
    assert n == 8 and json.loads((tmp_path / "ov" / "index.json").read_text())[1] == "v/c.jpg"
