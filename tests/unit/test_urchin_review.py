"""Reef Check urchin study: pre-label tiling/NMS and label review helpers."""

import cv2
import numpy as np
import pytest
from edge_ml.urchin import owl, review


def test_tiles_cover_frame_with_min_overlap():
    t = owl.tiles(1920, 1080)
    assert t == [(0, 0, 1080), (840, 0, 1080)]  # overlap 240 px = 22 %
    t4k = owl.tiles(3840, 2160)
    sides = {s for _, _, s in t4k}
    assert sides == {1080}
    xs = sorted({x for x, _, _ in t4k})
    ys = sorted({y for _, y, _ in t4k})
    assert xs[0] == 0 and xs[-1] == 3840 - 1080 and ys[-1] == 2160 - 1080
    assert all(b - a <= 1080 * 0.8 for a, b in zip(xs, xs[1:], strict=False))
    assert owl.tiles(640, 480) == [(0, 0, 480), (160, 0, 480)]
    assert owl.tile_starts(1000, 1080) == [0]


def test_nms_keeps_best_and_drops_overlaps():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 10, 10], [50, 50, 60, 60]], float)
    keep = owl.nms(boxes, np.array([0.5, 0.9, 0.3]))
    assert keep.tolist() == [1, 2]


def _owl():
    return {
        "detections": {
            "b.jpg": [{"box": [10, 10, 30, 30], "score": 0.4}],
            "a.jpg": [
                {"box": [0, 0, 20, 20], "score": 0.9},
                {"box": [40, 40, 60, 70], "score": 0.2},
            ],
        }
    }


def test_candidate_ids_are_stable():
    ids = review.candidate_ids(_owl())
    assert [(c, s, sc) for c, s, _, sc in ids] == [
        (0, "a.jpg", 0.9),
        (1, "a.jpg", 0.2),
        (2, "b.jpg", 0.4),
    ]


def test_make_sheets_writes_sheet_and_ids(tmp_path):
    for n in ("a.jpg", "b.jpg"):
        cv2.imwrite(str(tmp_path / n), np.full((100, 120, 3), 128, np.uint8))
    out = tmp_path / "sheets"
    assert review.make_sheets(_owl(), tmp_path, out, per_sheet=2) == 2
    assert (out / "sheet_000.json").read_text() == "[0, 1]"
    img = cv2.imread(str(out / "sheet_001.jpg"))
    assert img is not None and img.shape[1] == review.COLS * review.CELL


def test_crop_cell_pads_at_image_edge():
    img = np.full((50, 50, 3), 200, np.uint8)
    cell = review.crop_cell(img, [0, 0, 40, 40])
    assert cell.shape == (review.CELL, review.CELL, 3)
    assert cell[2, 2].tolist() == [0, 0, 0]  # padded area outside the image


def test_parse_decisions():
    d = review.parse_decisions("u: 3 7  # two urchins\ni: 5\n\n", [3, 5, 7, 9])
    assert d == {3: "u", 5: "i", 7: "u", 9: "n"}
    with pytest.raises(ValueError, match="not on this sheet"):
        review.parse_decisions("u: 11", [3])
    with pytest.raises(ValueError, match="twice"):
        review.parse_decisions("u: 3\ni: 3", [3])
    with pytest.raises(ValueError, match="bad decisions"):
        review.parse_decisions("x: 3", [3])


def test_parse_misses():
    m = review.parse_misses("a.jpg 10 20\nb.jpg 5.5 6 i  # unclear colour\n")
    assert m == [("a.jpg", 10.0, 20.0, False), ("b.jpg", 5.5, 6.0, True)]
    with pytest.raises(ValueError):
        review.parse_misses("a.jpg 10")


def test_overlay_tiles_split():
    tiles = review.overlay_tiles(np.zeros((400, 600, 3), np.uint8), [[10, 10, 50, 50]], [])
    assert [t.shape[:2] for t in tiles] == [(200, 300)] * 4


def test_coco_roundtrip():
    coco = review.to_coco(
        [("a.jpg", 100, 80), ("b.jpg", 100, 80)],
        {
            "a.jpg": [
                {"box": [1, 2, 11, 22], "source": "owl+sam"},
                {"box": [50, 50, 60, 60], "ignore": True, "source": "point"},
            ]
        },
    )
    assert coco["annotations"][0]["bbox"] == [1, 2, 10, 20]
    assert coco["annotations"][1]["ignore"] == 1
    back = review.from_coco(coco)
    np.testing.assert_allclose(back["a.jpg"][0], [[1, 2, 11, 22]])
    np.testing.assert_allclose(back["a.jpg"][1], [[50, 50, 60, 60]])
    assert back["b.jpg"][0].shape == (0, 4)


def test_still_sheets_number_items_in_list_order(tmp_path):
    root = tmp_path / "stills"
    (root / "v").mkdir(parents=True)
    names = []
    for k in range(7):
        cv2.imwrite(str(root / "v" / f"v_t{k:06d}.jpg"), np.full((90, 160, 3), 20 * k, np.uint8))
        names.append(f"v/v_t{k:06d}.jpg")
    (tmp_path / "list.txt").write_text("\n".join(names) + "\n")
    out = tmp_path / "sheets"
    assert review.main(["still-sheets", str(tmp_path / "list.txt"), str(root), str(out)]) == 0
    sheets = sorted(out.glob("still_sheet_*.jpg"))
    assert len(sheets) == 2  # 6 + 1
    import json

    assert json.loads((out / "still_sheet_001.json").read_text()) == ["v/v_t000006.jpg"]
    img = cv2.imread(str(sheets[0]))
    assert img.shape[1] == 2 * 960
    with pytest.raises(FileNotFoundError):
        review.still_sheets(["v/missing.jpg"], root, out)
