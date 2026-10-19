"""Pre-labels from Nick's RF-DETR urchin model, tiled (PREREG amendment 2026-10-18).

    cd <rig repo> && ~/nereus_ml/venvs/rfdetr/bin/python -m edge_ml.urchin.rfdetr_prelabel \\
        <stills.txt> <stills_root> <out.json> [--min-score 0.05] [--checkpoint PATH]

stills.txt lists still paths relative to stills_root, one per line. The output has the same format
as owl.py ({"model", "query", "min_score", "detections": {still: [{"box", "score"}, ...]}}), so
`review.py candidate-sheets` works unchanged. Tiling is the PREREG's: squares of
min(short side, 1080) px with >= 20 % overlap, detections merged by NMS at IoU 0.5.

Needs the `rfdetr` package (Apache-2.0) from Nick's environment (~/nereus_ml/venvs/rfdetr); the
model, its training code (ADIN_SPI_OpenMV ml/rfdetr_gate/) and data live outside this repo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

from edge_ml.urchin.compare_rfdetr import nms, tile_starts

log = logging.getLogger("urchin.rfdetr_prelabel")

CHECKPOINT = Path.home() / "nereus_ml/runs/rfdetr_gate/checkpoint_best_total.pth"
TILE_MAX = 1080
NMS_IOU = 0.5
MAX_PER_STILL = 300


def detect_tiled(model, im, min_score: float) -> list[dict]:
    """Tiled detection on a PIL image with any model whose predict(img, threshold) returns
    .xyxy and .confidence (RF-DETR's supervision Detections)."""
    import numpy as np

    w, h = im.size
    side = min(w, h, TILE_MAX)
    boxes, scores = [], []
    for y0 in tile_starts(h, side):
        for x0 in tile_starts(w, side):
            pred = model.predict(im.crop((x0, y0, x0 + side, y0 + side)), threshold=min_score)
            for (x1, y1, x2, y2), s in zip(pred.xyxy, pred.confidence, strict=True):
                boxes.append([float(x1) + x0, float(y1) + y0, float(x2) + x0, float(y2) + y0])
                scores.append(float(s))
    if not boxes:
        return []
    b, s = np.array(boxes), np.array(scores)
    b[:, [0, 2]] = b[:, [0, 2]].clip(0, w)
    b[:, [1, 3]] = b[:, [1, 3]].clip(0, h)
    keep = nms(b, s, NMS_IOU)[:MAX_PER_STILL]
    return [
        {"box": [round(float(v), 1) for v in b[i]], "score": round(float(s[i]), 4)} for i in keep
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stills", type=Path)
    ap.add_argument("stills_root", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--min-score", type=float, default=0.05)
    ap.add_argument("--checkpoint", type=Path, default=CHECKPOINT)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from PIL import Image
    from rfdetr import RFDETRBase

    ck_sha = hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
    names = [ln.strip() for ln in args.stills.read_text().splitlines() if ln.strip()]
    model = RFDETRBase(pretrain_weights=str(args.checkpoint))
    result, t0 = {}, time.monotonic()
    for i, name in enumerate(names):
        im = Image.open(args.stills_root / name).convert("RGB")
        result[name] = detect_tiled(model, im, args.min_score)
        if (i + 1) % 25 == 0:
            log.info(
                "%d/%d stills, %.1f s/still", i + 1, len(names), (time.monotonic() - t0) / (i + 1)
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "model": f"RF-DETR Base (Nick), tiled, {args.checkpoint}",
                "checkpoint_sha256": ck_sha,
                "query": None,
                "min_score": args.min_score,
                "detections": result,
            },
            indent=1,
        )
    )
    log.info("wrote %s: %d stills in %.0f s", args.out, len(result), time.monotonic() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
