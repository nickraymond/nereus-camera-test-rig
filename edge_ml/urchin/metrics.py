"""Detection scoring for the urchin study (PREREG "Metric" section). numpy only.

Boxes are xyxy pixel arrays. Per still: detections sorted by score are matched greedily to the
best-IoU *unmatched* label box; IoU >= 0.5 is a TP. A detection that is not a TP but overlaps an
ignore box at IoU >= 0.5 is dropped (neither TP nor FP). Everything else is an FP; unmatched
label boxes are FNs.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

IOU_THR = 0.5


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU between every box in a (N,4) and b (M,4), xyxy."""
    a = np.asarray(a, float).reshape(-1, 4)
    b = np.asarray(b, float).reshape(-1, 4)
    if not len(a) or not len(b):
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    return np.where(union > 0, inter / np.where(union > 0, union, 1), 0.0)


@dataclass
class ImageMatch:
    """Scored detections of one still: score + TP flag (ignored detections removed)."""

    scores: np.ndarray  # (K,) descending
    tp: np.ndarray  # (K,) bool
    n_gt: int  # label boxes (ignore boxes not counted)


def match_image(
    det_boxes: np.ndarray,
    det_scores: np.ndarray,
    gt_boxes: np.ndarray,
    ignore_boxes: np.ndarray | None = None,
    iou_thr: float = IOU_THR,
) -> ImageMatch:
    det_boxes = np.asarray(det_boxes, float).reshape(-1, 4)
    det_scores = np.asarray(det_scores, float).reshape(-1)
    gt_boxes = np.asarray(gt_boxes, float).reshape(-1, 4)
    ign = np.zeros((0, 4)) if ignore_boxes is None else np.asarray(ignore_boxes, float)
    order = np.argsort(-det_scores, kind="stable")
    iou_gt = iou_matrix(det_boxes, gt_boxes)
    iou_ign = iou_matrix(det_boxes, ign.reshape(-1, 4))
    matched = np.zeros(len(gt_boxes), bool)
    scores, tp = [], []
    for d in order:
        if len(gt_boxes):
            cand = np.where(matched, -1.0, iou_gt[d])
            g = int(np.argmax(cand))
            if cand[g] >= iou_thr:
                matched[g] = True
                scores.append(det_scores[d])
                tp.append(True)
                continue
        if iou_ign.shape[1] and iou_ign[d].max() >= iou_thr:
            continue  # on an ignore box: not counted
        scores.append(det_scores[d])
        tp.append(False)
    return ImageMatch(np.array(scores, float), np.array(tp, bool), len(gt_boxes))


@dataclass
class Counts:
    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else float("nan")

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else float("nan")

    @property
    def f1(self) -> float:
        d = 2 * self.tp + self.fp + self.fn
        return 2 * self.tp / d if d else float("nan")


def counts_at(matches: Sequence[ImageMatch], threshold: float) -> Counts:
    tp = fp = n_gt = 0
    for m in matches:
        keep = m.scores >= threshold
        tp += int(m.tp[keep].sum())
        fp += int((~m.tp[keep]).sum())
        n_gt += m.n_gt
    return Counts(tp, fp, n_gt - tp)


def _pooled(matches: Sequence[ImageMatch]) -> tuple[np.ndarray, np.ndarray, int]:
    scores = np.concatenate([m.scores for m in matches]) if matches else np.zeros(0)
    tp = np.concatenate([m.tp for m in matches]) if matches else np.zeros(0, bool)
    order = np.argsort(-scores, kind="stable")
    return scores[order], tp[order], sum(m.n_gt for m in matches)


def ap50(matches: Sequence[ImageMatch]) -> float:
    """All-point interpolated average precision (VOC 2010+)."""
    _, tp, n_gt = _pooled(matches)
    if n_gt == 0:
        return float("nan")
    ctp = np.cumsum(tp)
    cfp = np.cumsum(~tp)
    recall = np.concatenate([[0.0], ctp / n_gt, [ctp[-1] / n_gt if len(ctp) else 0.0]])
    precision = np.concatenate([[1.0], ctp / np.maximum(ctp + cfp, 1), [0.0]])
    precision = np.maximum.accumulate(precision[::-1])[::-1]
    return float(np.sum((recall[1:] - recall[:-1]) * precision[1:]))


def best_f1_threshold(matches: Sequence[ImageMatch]) -> float:
    """Score threshold maximising pooled F1 (ties → the highest threshold).

    Returns +inf when there are no detections (nothing passes)."""
    scores, tp, n_gt = _pooled(matches)
    if not len(scores):
        return float("inf")
    ctp = np.cumsum(tp)
    cfp = np.cumsum(~tp)
    f1 = 2 * ctp / np.maximum(2 * ctp + cfp + (n_gt - ctp), 1)
    # Only cut between distinct scores: the last index of each score run.
    last = np.r_[scores[1:] != scores[:-1], True]
    idx = np.flatnonzero(last)
    best = idx[np.argmax(f1[idx])]  # argmax returns the first = highest threshold on ties
    return float(scores[best])


def block_bootstrap(
    blocks: Sequence[object],
    stat: Callable[[np.ndarray], float],
    n_resamples: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """Percentile CI of stat over resampled blocks.

    blocks[i] is the block id of still i (PREREG: video + 30 s window). stat gets an index
    array of stills (with repeats) and returns a number. Returns (point, low, high)."""
    ids = np.array([str(b) for b in blocks])
    uniq = np.unique(ids)
    members = [np.flatnonzero(ids == u) for u in uniq]
    rng = np.random.default_rng(seed)
    point = stat(np.arange(len(ids)))
    vals = []
    for _ in range(n_resamples):
        pick = rng.integers(0, len(uniq), len(uniq))
        vals.append(stat(np.concatenate([members[p] for p in pick])))
    vals = np.array(vals, float)
    vals = vals[np.isfinite(vals)]
    lo, hi = np.quantile(vals, [alpha / 2, 1 - alpha / 2]) if len(vals) else (np.nan, np.nan)
    return float(point), float(lo), float(hi)


def block_id(video: str, t_s: float, block_s: float = 30.0) -> str:
    return f"{video}#{int(t_s // block_s)}"
