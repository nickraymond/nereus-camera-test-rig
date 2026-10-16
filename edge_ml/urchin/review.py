"""Label review for the urchin study (PREREG "Labels"). numpy + OpenCV only.

Workflow:
1. `sheets`: candidate crops (from owl.py output) → numbered contact sheets. Each crop shows the
   candidate box in context; its number is the candidate id.
2. A reviewer writes one decisions file per sheet, `sheet_NNN.txt`:
       u: 3 7 12      # live purple urchin
       i: 5           # ignore (PREREG ignore rules)
   Unlisted ids on the sheet are "not an urchin". A sheet without a decisions file is unreviewed.
3. `overlay`: full-frame overlays with the current boxes and a coordinate grid, split into
   tiles, for the miss search on test stills. Misses are written as `misses.txt` lines
   `<still> <x> <y> [i]` (full-image pixels; `i` marks an ignore box) and turned into boxes by
   SAM (sam.py).
4. `coco`: the reviewed labels → COCO JSON (bbox = [x, y, w, h], `ignore` 0/1, `source`).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np

CELL = 192  # px per crop cell on a sheet
PER_SHEET = 48  # 8 × 6
COLS = 8
LABEL_H = 22
CONTEXT = 2.0  # crop side = 2 × the box's long side
CROP_MIN = 64


def candidate_ids(owl: dict) -> list[tuple[int, str, list[float], float]]:
    """Stable candidate ids: stills in sorted order, detections in their stored (score) order."""
    out, k = [], 0
    for still in sorted(owl["detections"]):
        for d in owl["detections"][still]:
            out.append((k, still, d["box"], d["score"]))
            k += 1
    return out


def crop_cell(img: np.ndarray, box: list[float], cell: int = CELL) -> np.ndarray:
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box
    side = max(CONTEXT * max(x2 - x1, y2 - y1), CROP_MIN)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    x0, y0 = int(math.floor(cx - side / 2)), int(math.floor(cy - side / 2))
    s = int(math.ceil(side))
    canvas = np.zeros((s, s, 3), np.uint8)
    sx0, sy0, sx1, sy1 = max(x0, 0), max(y0, 0), min(x0 + s, w), min(y0 + s, h)
    canvas[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0] = img[sy0:sy1, sx0:sx1]
    k = cell / s
    out = cv2.resize(
        canvas, (cell, cell), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC
    )
    bx = [int(round((v - o) * k)) for v, o in zip(box, (x0, y0, x0, y0), strict=True)]
    cv2.rectangle(out, (bx[0], bx[1]), (bx[2], bx[3]), (0, 255, 255), 1)
    return out


def make_sheets(owl: dict, stills_root: Path, out_dir: Path, per_sheet: int = PER_SHEET) -> int:
    """Write sheet_NNN.jpg + sheet_NNN.json (ids on it). Returns the number of sheets."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cands = candidate_ids(owl)
    cache: dict[str, np.ndarray] = {}
    n_sheets = math.ceil(len(cands) / per_sheet)
    rows = math.ceil(per_sheet / COLS)
    for s in range(n_sheets):
        part = cands[s * per_sheet : (s + 1) * per_sheet]
        sheet = np.full((rows * (CELL + LABEL_H), COLS * CELL, 3), 40, np.uint8)
        for j, (cid, still, box, score) in enumerate(part):
            if still not in cache:
                cache = {still: cv2.imread(str(stills_root / still))}  # one still at a time
                if cache[still] is None:
                    raise FileNotFoundError(stills_root / still)
            r, c = divmod(j, COLS)
            y, x = r * (CELL + LABEL_H), c * CELL
            sheet[y : y + CELL, x : x + CELL] = crop_cell(cache[still], box)
            cv2.putText(
                sheet,
                f"{cid}",
                (x + 4, y + CELL + 17),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
            cv2.putText(
                sheet,
                f"{score:.2f}",
                (x + CELL - 44, y + CELL + 17),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (160, 160, 160),
                1,
                cv2.LINE_AA,
            )
            cv2.line(sheet, (x, y), (x, y + CELL + LABEL_H), (90, 90, 90), 1)
        cv2.imwrite(str(out_dir / f"sheet_{s:03d}.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])
        (out_dir / f"sheet_{s:03d}.json").write_text(json.dumps([p[0] for p in part]))
    return n_sheets


def parse_decisions(text: str, sheet_ids: list[int]) -> dict[int, str]:
    """Decisions file → {candidate id: "u" | "i" | "n"} for every id on the sheet."""
    out = {cid: "n" for cid in sheet_ids}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        tag, _, ids = line.partition(":")
        tag = tag.strip()
        if tag not in ("u", "i") or not _:
            raise ValueError(f"bad decisions line {raw!r} (want 'u: 1 2' or 'i: 3')")
        for tok in ids.replace(",", " ").split():
            cid = int(tok)
            if cid not in out:
                raise ValueError(f"candidate {cid} is not on this sheet")
            if out[cid] != "n":
                raise ValueError(f"candidate {cid} listed twice")
            out[cid] = tag
    return out


def parse_misses(text: str) -> list[tuple[str, float, float, bool]]:
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) not in (3, 4) or (len(parts) == 4 and parts[3] != "i"):
            raise ValueError(f"bad misses line {raw!r} (want '<still> <x> <y> [i]')")
        out.append((parts[0], float(parts[1]), float(parts[2]), len(parts) == 4))
    return out


def overlay_tiles(
    img: np.ndarray,
    boxes: list[list[float]],
    ignore: list[list[float]],
    grid: int = 100,
    tiles_x: int = 2,
    tiles_y: int = 2,
) -> list[np.ndarray]:
    """Full frame split into tiles, with boxes (green, ignore yellow) and a labelled px grid."""
    h, w = img.shape[:2]
    ov = img.copy()
    for x in range(0, w, grid):
        cv2.line(ov, (x, 0), (x, h), (255, 255, 255) if x % (5 * grid) == 0 else (128, 128, 128), 1)
    for y in range(0, h, grid):
        cv2.line(ov, (0, y), (w, y), (255, 255, 255) if y % (5 * grid) == 0 else (128, 128, 128), 1)
    for b in boxes:
        cv2.rectangle(ov, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (0, 255, 0), 2)
    for b in ignore:
        cv2.rectangle(ov, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (0, 255, 255), 2)
    out = []
    tw, th = math.ceil(w / tiles_x), math.ceil(h / tiles_y)
    for ty in range(tiles_y):
        for tx in range(tiles_x):
            x0, y0 = tx * tw, ty * th
            tile = ov[y0 : y0 + th, x0 : x0 + tw].copy()
            for x in range(math.ceil(x0 / grid) * grid, min(x0 + tw, w), grid):
                if x % (5 * grid) == 0:
                    cv2.putText(
                        tile,
                        str(x),
                        (x - x0 + 3, 16),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (255, 255, 255),
                        1,
                        cv2.LINE_AA,
                    )
            for y in range(math.ceil(y0 / grid) * grid, min(y0 + th, h), grid):
                if y % (5 * grid) == 0:
                    cv2.putText(
                        tile,
                        str(y),
                        (3, y - y0 - 3),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (255, 255, 255),
                        1,
                        cv2.LINE_AA,
                    )
            out.append(tile)
    return out


def to_coco(stills: list[tuple[str, int, int]], labels: dict[str, list[dict]]) -> dict:
    """stills: (name, width, height).

    labels[name] = [{"box": xyxy, "ignore": bool, "source": str}, ...]."""
    images, anns = [], []
    for img_id, (name, w, h) in enumerate(stills, start=1):
        images.append({"id": img_id, "file_name": name, "width": w, "height": h})
        for lab in labels.get(name, []):
            x1, y1, x2, y2 = lab["box"]
            anns.append(
                {
                    "id": len(anns) + 1,
                    "image_id": img_id,
                    "category_id": 1,
                    "bbox": [round(x1, 1), round(y1, 1), round(x2 - x1, 1), round(y2 - y1, 1)],
                    "area": round((x2 - x1) * (y2 - y1), 1),
                    "iscrowd": 0,
                    "ignore": int(bool(lab.get("ignore"))),
                    "source": lab.get("source", ""),
                }
            )
    return {
        "images": images,
        "annotations": anns,
        "categories": [{"id": 1, "name": "purple_urchin", "supercategory": "urchin"}],
    }


def from_coco(coco: dict) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """COCO → {file_name: (label boxes xyxy, ignore boxes xyxy)} for metrics.match_image."""
    names = {im["id"]: im["file_name"] for im in coco["images"]}
    out: dict[str, tuple[list, list]] = {n: ([], []) for n in names.values()}
    for a in coco["annotations"]:
        x, y, w, h = a["bbox"]
        out[names[a["image_id"]]][1 if a.get("ignore") else 0].append([x, y, x + w, y + h])
    return {
        n: (np.array(g, float).reshape(-1, 4), np.array(i, float).reshape(-1, 4))
        for n, (g, i) in out.items()
    }


def still_sheets(
    names: list[str],
    stills_root: Path,
    out_dir: Path,
    per_sheet: int = 6,
    cols: int = 2,
    width: int = 960,
) -> list[Path]:
    """Whole stills, numbered in list order, for the Q1 review and the per-group screen.

    Item k on sheet s is names[s * per_sheet + k]; its number is printed on the tile. Returns
    the sheet paths (still_sheet_NNN.jpg), with a JSON index of the names next to each."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for s in range(math.ceil(len(names) / per_sheet)):
        part = names[s * per_sheet : (s + 1) * per_sheet]
        tiles = []
        for j, name in enumerate(part):
            img = cv2.imread(str(stills_root / name))
            if img is None:
                raise FileNotFoundError(stills_root / name)
            h = round(img.shape[0] * width / img.shape[1])
            tile = np.full((h + LABEL_H + 8, width, 3), 30, np.uint8)
            tile[:h] = cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA)
            label = f"{s * per_sheet + j}  {Path(name).name}"
            cv2.putText(
                tile,
                label,
                (6, h + LABEL_H),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
            tiles.append(tile)
        th = max(t.shape[0] for t in tiles)
        rows = math.ceil(len(tiles) / cols)
        sheet = np.full((rows * th, cols * width, 3), 30, np.uint8)
        for j, t in enumerate(tiles):
            r, c = divmod(j, cols)
            sheet[r * th : r * th + t.shape[0], c * width : (c + 1) * width] = t
        path = out_dir / f"still_sheet_{s:03d}.jpg"
        cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
        (out_dir / f"still_sheet_{s:03d}.json").write_text(json.dumps(part))
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Urchin label review helpers (see module doc).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("still-sheets", help="numbered whole-still sheets from a list file")
    st.add_argument("names", type=Path, help="text file of still paths relative to stills_root")
    st.add_argument("stills_root", type=Path)
    st.add_argument("out_dir", type=Path)
    st.add_argument("--per-sheet", type=int, default=6)
    cs = sub.add_parser("candidate-sheets", help="numbered candidate crops from owl.py output")
    cs.add_argument("owl_json", type=Path)
    cs.add_argument("stills_root", type=Path)
    cs.add_argument("out_dir", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "still-sheets":
        names = [ln.strip() for ln in args.names.read_text().splitlines() if ln.strip()]
        for p in still_sheets(names, args.stills_root, args.out_dir, args.per_sheet):
            print(p)
        return 0
    n = make_sheets(json.loads(args.owl_json.read_text()), args.stills_root, args.out_dir)
    print(f"{n} candidate sheets → {args.out_dir}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
