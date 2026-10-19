"""Full-run labels (PREREG "Labels"): candidate decisions → SAM boxes → miss search → COCO.
Study-only code; never imported from src/.

    python -m edge_ml.urchin.labels assemble <prelabels.json> <sheets_dir> <stills_root> <out.json>
    python -m edge_ml.urchin.labels overlays <labels.json> <names.txt> <stills_root> <out_dir>
    python -m edge_ml.urchin.labels misses   <labels.json> <misses.txt> <stills_root> <out.json>
    python -m edge_ml.urchin.labels coco     <labels.json> <fold_dir>

Sheets come from `review.py candidate-sheets` on the (floored) pre-labels; a reviewer writes
sheet_NNN.txt per sheet (`u:` urchin, `i:` ignore, unlisted = not). `assemble` needs every sheet
reviewed, tightens each u / i box with SAM (box prompt), turns a u box whose short side is < 16 px
into an ignore box (PREREG ignore rule), and merges duplicates of one urchin (PREREG
clarification, PE 005, Sun 10/18 10:46 PM PT): within a still, SAM boxes with IoU >= 0.7 are one
urchin; a u box wins over an ignore box, else the higher pre-label score. The same rule runs after
the miss search. Per still, `dup` counts merges, unmerged pairs with 0.5 <= IoU < 0.7, unmerged
pairs with the smaller box >= 90 % inside the larger, and SAM failures; `coco` sums them per split
in labels_report.json. `overlays` draws the boxes on full frames with a pixel grid (the SAM
check and, for test and validation stills, the miss search). `misses` adds `<still> <x> <y> [i]`
points as SAM boxes.
`coco` writes labels_train/val/test.json for a fold (review.to_coco). Every still in the
pre-labels is in labels.json, with or without boxes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from edge_ml.urchin.review import candidate_ids, parse_decisions, parse_misses, to_coco

MIN_SIDE = 16  # PREREG: short side < 16 px at native resolution → ignore box
DUP_IOU = 0.7  # PREREG clarification (PE 005, Sun 10/18 10:46 PM PT): one urchin if SAM IoU >= 0.7
NEAR_IOU = 0.5  # reported only: unmerged pairs with 0.5 <= IoU < 0.7
CONTAINED = 0.9  # reported only: smaller box >= 90 % inside the larger (two urchins, not merged)


def iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def decisions(prelabels: dict, sheets_dir: Path) -> dict[int, str]:
    """{candidate id: u / i / n}; every candidate on exactly one sheet, every sheet reviewed."""
    out: dict[int, str] = {}
    unreviewed = []
    for js in sorted(sheets_dir.glob("sheet_[0-9][0-9][0-9].json")):
        ids = json.loads(js.read_text())
        txt = js.with_suffix(".txt")
        if not txt.exists():
            unreviewed.append(txt.name)
            continue
        for cid, tag in parse_decisions(txt.read_text(), ids).items():
            if cid in out:
                raise ValueError(f"candidate {cid} is on two sheets")
            out[cid] = tag
    if unreviewed:
        raise ValueError(f"{len(unreviewed)} sheets not reviewed: {unreviewed[:5]}")
    want = {c[0] for c in candidate_ids(prelabels)}
    if set(out) != want:
        raise ValueError(f"sheets cover {len(out)} of {len(want)} candidates")
    return out


def inside(a: list[float], b: list[float]) -> float:
    """Share of the smaller box's area inside the other box."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / small if small > 0 else 0.0


def dedupe(boxes: list[dict]) -> tuple[list[dict], dict]:
    """Merge duplicates of one urchin (box geometry only): u before i, then higher pre-label score;
    a box with IoU >= 0.7 to a kept one is dropped. Returns the kept boxes and the counts the
    PREREG row reports: merged, unmerged pairs with 0.5 <= IoU < 0.7, unmerged pairs where the
    smaller box is >= 90 % inside the larger."""
    kept: list[dict] = []
    for b in sorted(boxes, key=lambda b: (b["ignore"], -b.get("score", 0.0))):
        if all(iou(b["box"], k["box"]) < DUP_IOU for k in kept):
            kept.append(b)
    pairs = [(a, b) for i, a in enumerate(kept) for b in kept[i + 1 :]]
    stats = {
        "merged": len(boxes) - len(kept),
        "near_0p5_0p7": sum(NEAR_IOU <= iou(a["box"], b["box"]) < DUP_IOU for a, b in pairs),
        "contained_0p9": sum(inside(a["box"], b["box"]) >= CONTAINED for a, b in pairs),
    }
    return kept, stats


def small_to_ignore(b: dict) -> dict:
    x1, y1, x2, y2 = b["box"]
    if not b["ignore"] and min(x2 - x1, y2 - y1) < MIN_SIDE:
        return b | {"ignore": True, "small": True}
    return b


def read_rgb(path: Path) -> np.ndarray:
    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


def assemble(prelabels: dict, sheets_dir: Path, stills_root: Path, refiner) -> dict:
    """refiner: sam.SamRefiner (or any object with refine_box(rgb, box) -> {"box", ...})."""
    dec = decisions(prelabels, sheets_dir)
    by_still: dict[str, list[dict]] = {s: [] for s in prelabels["detections"]}
    for cid, still, box, score in candidate_ids(prelabels):
        if dec[cid] != "n":
            by_still[still].append(
                {"cand_id": cid, "box": box, "score": score, "ignore": dec[cid] == "i"}
            )
    out: dict[str, dict] = {}
    for still in sorted(by_still):
        w, h = image_size(stills_root / still)
        boxes = []
        if by_still[still]:
            rgb = read_rgb(stills_root / still)
            for b in by_still[still]:
                r = refiner.refine_box(rgb, b["box"])
                boxes.append(small_to_ignore(b | r | {"source": "prelabel+sam"}))
        kept, stats = dedupe(boxes)
        stats["sam_failures"] = sum(not b.get("sam_ok", True) for b in boxes)
        out[still] = {"width": w, "height": h, "boxes": kept, "dup": stats}
    counts = {t: sum(v == t for v in dec.values()) for t in "uin"}
    return {
        "prelabels_model": prelabels.get("model"),
        "min_score": prelabels.get("min_score"),
        "decisions": counts,
        "sam_failures": {
            s: v["dup"]["sam_failures"] for s, v in out.items() if v["dup"]["sam_failures"]
        },
        "stills": out,
    }


def add_misses(labels: dict, misses: list[tuple[str, float, float, bool]], stills_root: Path,
               refiner) -> tuple[dict, list[str]]:  # fmt: skip
    """Each miss point → a SAM box (source miss+sam); a point SAM can't box is listed, not added."""
    out = json.loads(json.dumps(labels))
    failed: list[str] = []
    touched: set[str] = set()
    for still, x, y, ign in misses:
        if still not in out["stills"]:
            raise ValueError(f"miss on {still}, which is not in the labels")
        r = refiner.point_box(read_rgb(stills_root / still), x, y)
        if r["box"] is None:
            failed.append(f"{still} {x:g} {y:g}")
            continue
        b = {"box": r["box"], "ignore": ign, "source": "miss+sam", "point": [x, y]}
        b = small_to_ignore(b | {"sam_iou": r["sam_iou"], "sam_ok": r["sam_ok"]})
        out["stills"][still]["boxes"].append(b)
        touched.add(still)
    for still in touched:  # the same duplicate rule runs after the miss search (PREREG row)
        st = out["stills"][still]
        kept, stats = dedupe(st["boxes"])
        prev = st.get("dup", {})
        st["boxes"] = kept
        st["dup"] = stats | {"merged": prev.get("merged", 0) + stats["merged"],
                             "sam_failures": prev.get("sam_failures", 0)}  # fmt: skip
    out["misses"] = {"points": len(misses), "added": len(misses) - len(failed), "failed": failed}
    return out, failed


def write_overlays(labels: dict, names: list[str], stills_root: Path, out_dir: Path) -> int:
    from edge_ml.urchin.review import overlay_tiles

    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for k, name in enumerate(names):
        boxes = labels["stills"][name]["boxes"]
        img = cv2.imread(str(stills_root / name))
        if img is None:
            raise FileNotFoundError(stills_root / name)
        tiles = overlay_tiles(img, [b["box"] for b in boxes if not b["ignore"]],
                              [b["box"] for b in boxes if b["ignore"]])  # fmt: skip
        for t, tile in enumerate(tiles):
            cv2.imwrite(str(out_dir / f"ov_{k:04d}_{t}.jpg"), tile, [cv2.IMWRITE_JPEG_QUALITY, 88])
            n += 1
    (out_dir / "index.json").write_text(json.dumps(names))
    return n


def write_coco(labels: dict, fold_dir: Path) -> dict[str, int]:
    """labels_<part>.json per split + labels_report.json (the PREREG row's counts per split)."""
    out, report = {}, {}
    for part in ("train", "val", "test"):
        names = [ln.strip() for ln in (fold_dir / f"{part}.txt").read_text().splitlines() if ln]
        if missing := [n for n in names if n not in labels["stills"]]:
            raise ValueError(f"{part}: {len(missing)} stills have no labels, e.g. {missing[:3]}")
        st = labels["stills"]
        coco = to_coco([(n, st[n]["width"], st[n]["height"]) for n in names],
                       {n: st[n]["boxes"] for n in names})  # fmt: skip
        (fold_dir / f"labels_{part}.json").write_text(json.dumps(coco, indent=1))
        out[part] = len(coco["annotations"])
        keys = ("merged", "near_0p5_0p7", "contained_0p9", "sam_failures")
        dup = [st[n].get("dup", {}) for n in names]
        report[part] = {"stills": len(names), "label_boxes": sum(
            not b["ignore"] for n in names for b in st[n]["boxes"]), "ignore_boxes": sum(
            b["ignore"] for n in names for b in st[n]["boxes"])} | {
            k: sum(d.get(k, 0) for d in dup) for k in keys} | {
            "merged_per_still": round(sum(d.get("merged", 0) for d in dup) / len(names), 4)
            if names else 0.0}  # fmt: skip
    (fold_dir / "labels_report.json").write_text(json.dumps(report, indent=1) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("assemble")
    for k in ("prelabels", "sheets_dir", "stills_root", "out"):
        a.add_argument(k, type=Path)
    o = sub.add_parser("overlays")
    for k in ("labels", "names", "stills_root", "out_dir"):
        o.add_argument(k, type=Path)
    m = sub.add_parser("misses")
    for k in ("labels", "misses", "stills_root", "out"):
        m.add_argument(k, type=Path)
    c = sub.add_parser("coco")
    for k in ("labels", "fold_dir"):
        c.add_argument(k, type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "assemble":
        from edge_ml.urchin.sam import SamRefiner

        pre = json.loads(args.prelabels.read_text())
        res = assemble(pre, args.sheets_dir, args.stills_root, SamRefiner())
        res["prelabels_file"] = {  # QE 008 N4: ids are only valid for this exact file
            "path": str(args.prelabels),
            "sha256": hashlib.sha256(args.prelabels.read_bytes()).hexdigest(),
        }
        args.out.write_text(json.dumps(res, indent=1))
        print(f"labels: {res['decisions']} → {args.out}")
        return 0
    lab = json.loads(args.labels.read_text())
    if args.cmd == "overlays":
        names = [ln.strip() for ln in args.names.read_text().splitlines() if ln.strip()]
        print(f"{write_overlays(lab, names, args.stills_root, args.out_dir)} overlay tiles")
        return 0
    if args.cmd == "misses":
        from edge_ml.urchin.sam import SamRefiner

        res, failed = add_misses(lab, parse_misses(args.misses.read_text()), args.stills_root,
                                 SamRefiner())  # fmt: skip
        args.out.write_text(json.dumps(res, indent=1))
        print(f"misses: {res['misses']['added']} added, {len(failed)} SAM failures {failed}")
        return 1 if failed else 0
    print(f"coco: {write_coco(lab, args.fold_dir)} annotations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
