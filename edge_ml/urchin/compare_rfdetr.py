"""Nick's RF-DETR urchin model vs the night pilot's calls and OWLv2 (image level). Study-only code.

    <rfdetr venv>/bin/python -I compare_rfdetr.py run   <pilot_run_dir> <out.json> <checkpoint.pth>
    python3 -I compare_rfdetr.py score <pilot_run_dir>

`run` needs the `rfdetr` package (Apache-2.0); it is not in requirements.txt: the model and its
environment are Nick's, outside this repo: ~/nereus_ml/venvs/rfdetr, checkpoint
~/nereus_ml/runs/rfdetr_gate/checkpoint_best_total.pth, code in ADIN_SPI_OpenMV ml/rfdetr_gate/.
It writes detections (score >= 0.05) for the 100-still pilot sample, whole frame (as the model was
built, 560 px) and tiled like OWLv2 (squares of min(short side, 1080) px, >= 20 % overlap, NMS 0.5).
`score` needs only the standard library. The rule (references, thresholds, metrics) was written in
the run folder's compare_rfdetr/MANIFEST.md before the run.
"""

from __future__ import annotations

import csv
import json
import math
import sys
import time
from pathlib import Path

FLOOR = 0.05
THRESHOLDS = (0.3, 0.5)


def tile_starts(length: int, side: int, min_overlap: float = 0.2) -> list[int]:
    if length <= side:
        return [0]
    step = side * (1 - min_overlap)
    n = math.ceil((length - side) / step) + 1
    return [round(i * (length - side) / (n - 1)) for i in range(n)]


def nms(boxes, scores, thr: float = 0.5) -> list[int]:
    import numpy as np

    order = np.argsort(-scores, kind="stable")
    keep = []
    while len(order):
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        x1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        y1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        x2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        y2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        order = rest[inter / (area_i + area_r - inter + 1e-9) < thr]
    return keep


def cmd_run(run: Path, out: Path, ck: str) -> int:
    import numpy as np
    from PIL import Image
    from rfdetr import RFDETRBase

    def dets(pred, dx=0, dy=0):
        return [
            {"box": [float(a) + dx, float(b) + dy, float(c) + dx, float(d) + dy], "score": float(s)}
            for (a, b, c, d), s in zip(pred.xyxy, pred.confidence, strict=True)
        ]

    names = [ln.strip() for ln in (run / "pilot_sample.txt").read_text().splitlines() if ln.strip()]
    model = RFDETRBase(pretrain_weights=ck)
    res = {"checkpoint": ck, "floor": FLOOR, "whole": {}, "tiled": {}}
    t0 = time.time()
    for k, n in enumerate(names):
        im = Image.open(run / "stills" / n).convert("RGB")
        res["whole"][n] = dets(model.predict(im, threshold=FLOOR))
        w, h = im.size
        side = min(w, h, 1080)
        allb = []
        for y0 in tile_starts(h, side):
            for x0 in tile_starts(w, side):
                crop = im.crop((x0, y0, x0 + side, y0 + side))
                allb += dets(model.predict(crop, threshold=FLOOR), x0, y0)
        if allb:
            b = np.array([d["box"] for d in allb])
            s = np.array([d["score"] for d in allb])
            allb = [allb[i] for i in nms(b, s)]
        res["tiled"][n] = sorted(allb, key=lambda d: -d["score"])
        if (k + 1) % 10 == 0:
            print(
                f"{k + 1}/{len(names)} stills, {(time.time() - t0) / (k + 1):.1f} s/still",
                flush=True,
            )
    out.write_text(json.dumps(res))
    print("done", round(time.time() - t0), "s")
    return 0


def top(dets: dict, still: str) -> float:
    return max((d["score"] for d in dets.get(still, [])), default=0.0)


def auc(pos: list[float], neg: list[float]) -> float | None:
    """ROC-AUC (Mann–Whitney, ties count half); None when a side is empty."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def compare(calls: list[dict], models: dict[str, dict]) -> dict:
    """Image-level comparison against purple-urchin calls (yes / no / unsure)."""
    groups = {
        "yes": [c for c in calls if c["urchin"] == "yes"],
        "no": [c for c in calls if c["urchin"] == "no"],
        "no_red": [c for c in calls if c["urchin"] == "no" and "red" in c["note"].lower()],
        "no_other": [c for c in calls if c["urchin"] == "no" and "red" not in c["note"].lower()],
        "unsure": [c for c in calls if c["urchin"] == "unsure"],
    }
    out: dict = {"n": {k: len(v) for k, v in groups.items()}, "models": {}}
    for name, dets in models.items():
        s = {k: [top(dets, c["still"]) for c in v] for k, v in groups.items()}
        m: dict = {
            "auc_yes_vs_no": auc(s["yes"], s["no"]),
            "auc_yes_vs_no_other": auc(s["yes"], s["no_other"]),
        }
        for thr in THRESHOLDS:
            m[f"flagged@{thr}"] = {k: f"{sum(x >= thr for x in v)}/{len(v)}" for k, v in s.items()}
        m["median_top_score"] = {
            k: round(sorted(v)[len(v) // 2], 3) if v else None for k, v in s.items()
        }
        out["models"][name] = m
    return out


def cmd_score(run: Path) -> int:
    c = run / "compare_rfdetr"
    calls = list(csv.DictReader((run / "pilot_sample_calls.csv").open()))
    owl = json.loads((run / "owl_prelabels.json").read_text())["detections"]
    rf = json.loads((c / "rfdetr_dets.json").read_text())
    models = {
        "RF-DETR (Nick), whole frame": rf["whole"],
        "RF-DETR (Nick), tiled": rf["tiled"],
        "OWLv2 zero-shot, tiled": owl,
    }
    out = compare(calls, models)
    (c / "compare.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "run":
        return cmd_run(Path(argv[1]), Path(argv[2]), argv[3])
    if len(argv) == 2 and argv[0] == "score":
        return cmd_score(Path(argv[1]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
