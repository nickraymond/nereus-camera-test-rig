"""Model A of the urchin study: OWLv2 zero-shot, tiled (PREREG "Models").

    python -m edge_ml.urchin.owl <stills.txt> <stills_root> <out.json> [--min-score 0.05]

stills.txt lists still paths relative to stills_root, one per line. out.json maps each still to
its detections: {"still": [{"box": [x1, y1, x2, y2], "score": s}, ...]}, sorted by score.
The same tiled detector makes the pre-labels (score >= 0.05) and is scored as baseline A
(lower --min-score, so the threshold can be chosen on validation).

The tiling and NMS helpers are numpy-only; the model needs torch + transformers
(edge_ml/urchin/requirements.txt).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("urchin.owl")

MODEL_ID = "google/owlv2-base-patch16-ensemble"
QUERY = "purple sea urchin"  # PREREG: single fixed text query
TILE_MAX = 1080  # PREREG: tile side = min(short side, 1080 px)
MIN_OVERLAP = 0.2  # PREREG: >= 20 % overlap
NMS_IOU = 0.5  # PREREG
MAX_PER_STILL = 300


def tile_starts(length: int, side: int, min_overlap: float = MIN_OVERLAP) -> list[int]:
    """Evenly spaced tile starts covering [0, length) with >= min_overlap between neighbours."""
    if length <= side:
        return [0]
    step = side * (1 - min_overlap)
    n = math.ceil((length - side) / step) + 1
    return [round(i * (length - side) / (n - 1)) for i in range(n)]


def tiles(width: int, height: int, tile_max: int = TILE_MAX) -> list[tuple[int, int, int]]:
    """Square tiles (x0, y0, side) covering a width × height image."""
    side = min(width, height, tile_max)
    return [(x, y, side) for y in tile_starts(height, side) for x in tile_starts(width, side)]


def nms(boxes: np.ndarray, scores: np.ndarray, iou_thr: float = NMS_IOU) -> np.ndarray:
    """Greedy NMS; returns kept indices in descending score order."""
    from edge_ml.urchin.metrics import iou_matrix

    order = np.argsort(-np.asarray(scores), kind="stable")
    keep: list[int] = []
    suppressed = np.zeros(len(order), bool)
    iou = iou_matrix(boxes, boxes)
    for rank, i in enumerate(order):
        if suppressed[rank]:
            continue
        keep.append(int(i))
        suppressed |= iou[i, order] >= iou_thr
    return np.array(keep, int)


class OwlDetector:
    def __init__(self, device: str | None = None, query: str = QUERY, model_id: str = MODEL_ID):
        import torch
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        self.torch = torch
        self.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        self.processor = Owlv2Processor.from_pretrained(model_id)
        self.model = Owlv2ForObjectDetection.from_pretrained(model_id).to(self.device).eval()
        self.query = query

    def detect_tile(self, rgb: np.ndarray, min_score: float) -> tuple[np.ndarray, np.ndarray]:
        side = rgb.shape[0]
        inputs = self.processor(text=[[self.query]], images=rgb, return_tensors="pt").to(
            self.device
        )
        with self.torch.no_grad():
            out = self.model(**inputs)
        res = self.processor.post_process_grounded_object_detection(
            out, threshold=min_score, target_sizes=[(side, side)]
        )[0]
        return res["boxes"].cpu().numpy().reshape(-1, 4), res["scores"].cpu().numpy()

    def detect(self, bgr: np.ndarray, min_score: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
        h, w = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        all_boxes, all_scores = [], []
        for x0, y0, side in tiles(w, h):
            b, s = self.detect_tile(
                np.ascontiguousarray(rgb[y0 : y0 + side, x0 : x0 + side]), min_score
            )
            all_boxes.append(b + [x0, y0, x0, y0])
            all_scores.append(s)
        boxes = np.concatenate(all_boxes) if all_boxes else np.zeros((0, 4))
        scores = np.concatenate(all_scores) if all_scores else np.zeros(0)
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, w)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, h)
        keep = nms(boxes, scores)[:MAX_PER_STILL]
        return boxes[keep], scores[keep]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stills", type=Path, help="text file: still paths relative to stills_root")
    ap.add_argument("stills_root", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--min-score", type=float, default=0.05)
    ap.add_argument("--device", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    names = [ln.strip() for ln in args.stills.read_text().splitlines() if ln.strip()]
    det = OwlDetector(args.device)
    log.info("OWLv2 %s on %s, query %r, %d stills", MODEL_ID, det.device, det.query, len(names))
    result, t0 = {}, time.monotonic()
    for i, name in enumerate(names):
        img = cv2.imread(str(args.stills_root / name))
        if img is None:
            log.error("cannot read %s", args.stills_root / name)
            return 1
        boxes, scores = det.detect(img, args.min_score)
        result[name] = [
            {"box": [round(float(v), 1) for v in b], "score": round(float(s), 4)}
            for b, s in zip(boxes, scores, strict=True)
        ]
        if (i + 1) % 25 == 0:
            log.info(
                "%d/%d stills, %.1f s/still", i + 1, len(names), (time.monotonic() - t0) / (i + 1)
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {"model": MODEL_ID, "query": QUERY, "min_score": args.min_score, "detections": result},
            indent=1,
        )
    )
    n = sum(len(v) for v in result.values())
    log.info(
        "wrote %s: %d detections on %d stills in %.0f s",
        args.out,
        n,
        len(result),
        time.monotonic() - t0,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
