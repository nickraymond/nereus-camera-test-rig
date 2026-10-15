"""Score models A and B on held-out stills and apply the PREREG bar. numpy only.

    python -m edge_ml.urchin.evaluate --fold <fold_dir> [--fold ...] --groups groups.json \
        --out <dir>

Fold dir layout (written by the run scripts):
    fold.json     split.Fold.info(): {"name", "mode", "test_groups", "train_groups" (required),
                  "val_groups", "n", ...}; written by `python -m edge_ml.urchin.split folds`
    labels_val.json, labels_test.json          COCO (review.to_coco)
    a/pred_labels_val.json, a/pred_labels_test.json            model A (owl.py)
    b/seed<k>/pred_labels_val.json, b/seed<k>/pred_labels_test.json   model B (train_b.py)
groups.json maps video stem → group.

Per model (and per B seed): the threshold is the max-F1 score on validation, frozen, then test
P/R/F1 at it, AP50, per-group F1, and the paired, group-stratified 30 s block bootstrap of
F1(B) − F1(A) computed at the frozen thresholds. Writes eval.json and eval.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

from edge_ml.urchin import metrics as m
from edge_ml.urchin.review import from_coco

STILL_RE = re.compile(r"^(?:.*/)?(?P<video>.+)_t(?P<tenths>\d{6})\.jpg$")
MIN_GROUPS = 2  # PREREG minimums
MIN_TEST_BOXES = 100
MIN_DELTA_F1 = 0.10  # PREREG bar condition 1
N_BOOT = 2000


def still_video_time(name: str) -> tuple[str, float]:
    mt = STILL_RE.match(name)
    if not mt:
        raise ValueError(f"still name {name!r} does not match <video>_tNNNNNN.jpg")
    return mt["video"], int(mt["tenths"]) / 10


def match_all(labels: dict, preds: dict) -> list[m.ImageMatch]:
    out = []
    for name in sorted(labels):
        gt, ign = labels[name]
        dets = preds.get(name, [])
        boxes = np.array([d["box"] for d in dets], float).reshape(-1, 4)
        scores = np.array([d["score"] for d in dets], float)
        out.append(m.match_image(boxes, scores, gt, ign))
    return out


def per_still_counts(matches: list[m.ImageMatch], thr: float) -> np.ndarray:
    """(n_stills, 3) TP, FP, FN at a frozen threshold."""
    rows = []
    for mt in matches:
        keep = mt.scores >= thr
        tp = int(mt.tp[keep].sum())
        rows.append((tp, int((~mt.tp[keep]).sum()), mt.n_gt - tp))
    return np.array(rows, int).reshape(-1, 3)


def f1_of(c: np.ndarray) -> float:
    tp, fp, fn = c.sum(0)
    d = 2 * tp + fp + fn
    return 2 * tp / d if d else float("nan")


def score_model(val_labels, test_labels, val_preds, test_preds, still_groups) -> dict:
    thr = m.best_f1_threshold(match_all(val_labels, val_preds))
    test_m = match_all(test_labels, test_preds)
    c = m.counts_at(test_m, thr)
    counts = per_still_counts(test_m, thr)
    names = sorted(test_labels)
    by_group = {}
    for g in sorted(set(still_groups.values())):
        idx = [i for i, n in enumerate(names) if still_groups[n] == g]
        by_group[g] = {
            "f1": f1_of(counts[idx]),
            "n_stills": len(idx),
            "n_boxes": int(counts[idx, 0].sum() + counts[idx, 2].sum()),
        }
    return {
        "threshold": thr,
        "precision": c.precision,
        "recall": c.recall,
        "f1": c.f1,
        "tp": c.tp,
        "fp": c.fp,
        "fn": c.fn,
        "ap50": m.ap50(test_m),
        "by_group": by_group,
        "_counts": counts,
    }


def evaluate_fold(fold_dir: Path, groups: dict[str, str]) -> dict:
    info = json.loads((fold_dir / "fold.json").read_text())
    val_l = from_coco(json.loads((fold_dir / "labels_val.json").read_text()))
    test_l = from_coco(json.loads((fold_dir / "labels_test.json").read_text()))
    n_val_boxes = sum(len(gt) for gt, _ in val_l.values())
    if n_val_boxes == 0:
        raise ValueError(
            f"{fold_dir}: validation has no label boxes ({len(val_l)} stills); thresholds "
            "would freeze at the top score. PREREG: stop here."
        )
    names = sorted(test_l)
    vt = [still_video_time(n) for n in names]
    still_groups = {n: groups[v] for n, (v, _) in zip(names, vt, strict=True)}
    blocks = [m.block_id(v, t) for v, t in vt]
    strata = [still_groups[n] for n in names]

    def load(p: Path) -> dict:
        return json.loads(p.read_text())["detections"]

    a = score_model(
        val_l,
        test_l,
        load(fold_dir / "a/pred_labels_val.json"),
        load(fold_dir / "a/pred_labels_test.json"),
        still_groups,
    )
    seeds = {}
    for sd in sorted((fold_dir / "b").glob("seed*")):
        b = score_model(
            val_l,
            test_l,
            load(sd / "pred_labels_val.json"),
            load(sd / "pred_labels_test.json"),
            still_groups,
        )
        ca, cb = a["_counts"], b["_counts"]
        vals = []

        def stat(idx, ca=ca, cb=cb, vals=vals):
            d = f1_of(cb[idx]) - f1_of(ca[idx])
            vals.append(d)
            return d

        pt, lo, hi = m.block_bootstrap(blocks, stat, N_BOOT, seed=0, strata=strata)
        n_nonfinite = int(np.sum(~np.isfinite(np.array(vals[1:], float))))
        groups_ok = all(
            b["by_group"][g]["f1"] >= a["by_group"][g]["f1"] for g in info["test_groups"]
        )
        b["delta_f1"] = {
            "point": pt,
            "ci95": [lo, hi],
            "n_resamples": N_BOOT,
            "n_nonfinite_dropped": n_nonfinite,
        }
        b["bar"] = {
            "delta_ge_0.10": bool(pt >= MIN_DELTA_F1),
            "ci_excludes_0": bool(lo > 0),
            "b_ge_a_every_group": bool(groups_ok),
        }
        seeds[sd.name] = b
    new_site = new_site_groups(info)
    split_f1 = {}
    for label, gs in (
        ("new_site", new_site),
        ("same_site", [g for g in info["test_groups"] if g not in new_site]),
    ):
        idx = [i for i, n in enumerate(names) if still_groups[n] in gs]
        split_f1[label] = {
            "groups": gs,
            "n_stills": len(idx),
            "n_boxes": int(a["_counts"][idx, 0].sum() + a["_counts"][idx, 2].sum()),
            "f1_A": f1_of(a["_counts"][idx]),
            "f1_B": {s: f1_of(b["_counts"][idx]) for s, b in seeds.items()},
        }
    n_boxes = int(a["_counts"][:, 0].sum() + a["_counts"][:, 2].sum())
    n_ignore = int(sum(len(ign) for _, ign in test_l.values()))
    return {
        "fold": info,
        "n_test_stills": len(names),
        "n_test_boxes": n_boxes,
        "n_test_ignore": n_ignore,
        "n_test_videos": len({v for v, _ in vt}),
        "A": a,
        "B": seeds,
        "secondary_new_vs_same_site": split_f1,
    }


def site_of(group: str) -> str:
    return group.split("|", 1)[0]


def new_site_groups(info: dict) -> list[str]:
    """Held-out groups whose site has no training group (validation does not count)."""
    if "train_groups" not in info:
        raise KeyError(
            f"fold {info.get('name')!r}: fold.json has no train_groups; write it with "
            "split.Fold.info() (python -m edge_ml.urchin.split folds)"
        )
    train_sites = {site_of(g) for g in info["train_groups"]}
    return [g for g in info["test_groups"] if site_of(g) not in train_sites]


def outcome(folds: list[dict]) -> tuple[str, str]:
    modes = {f["fold"]["mode"] for f in folds}
    if "same_dive" in modes:
        return "inconclusive (same dive)", "only one eligible group; PREREG allows no claim"
    n_groups = len({g for f in folds for g in f["fold"]["test_groups"]})
    n_boxes = sum(f["n_test_boxes"] for f in folds)
    if n_groups < MIN_GROUPS or n_boxes < MIN_TEST_BOXES:
        return (
            "inconclusive — need more labelled video",
            f"{n_groups} held-out groups, {n_boxes} test boxes (need >= 2 and >= 100)",
        )
    if not all(f["B"] for f in folds):
        return "incomplete", "a fold has no model B seeds"
    fails = [
        f"{f['fold']['name']}/{s}: {k}"
        for f in folds
        for s, b in f["B"].items()
        for k, ok in b["bar"].items()
        if not ok
    ]
    if fails:
        return "not shown", "; ".join(fails)
    reason = f"all bar conditions hold in {len(folds)} fold(s) x every seed"
    if not any(new_site_groups(f["fold"]) for f in folds):
        return "helped on other dives at sites in training", reason + "; no held-out new site"
    return "helped", reason


def _fmt(x: float) -> str:
    return "—" if x is None or not np.isfinite(x) else f"{x:.3f}"


def write_md(folds: list[dict], result: tuple[str, str]) -> str:
    lines = [f"**Outcome: {result[0]}** — {result[1]}", ""]
    for f in folds:
        fi = f["fold"]
        lines += [
            f"### Fold {fi['name']} ({fi['mode']}; test groups: {', '.join(fi['test_groups'])})",
            f"n = {f['n_test_stills']} stills, {f['n_test_boxes']} label boxes, "
            f"{f['n_test_ignore']} ignore boxes, {f['n_test_videos']} videos",
            "",
            "| Model | threshold | precision | recall | F1 | AP50 | ΔF1 vs A [95 % CI] |",
            "|---|---|---|---|---|---|---|",
        ]
        a = f["A"]
        lines.append(
            f"| A (OWLv2 zero-shot) | {_fmt(a['threshold'])} | {_fmt(a['precision'])} | "
            f"{_fmt(a['recall'])} | {_fmt(a['f1'])} | {_fmt(a['ap50'])} | — |"
        )
        for s, b in f["B"].items():
            d = b["delta_f1"]
            lines.append(
                f"| B {s} | {_fmt(b['threshold'])} | {_fmt(b['precision'])} | "
                f"{_fmt(b['recall'])} | {_fmt(b['f1'])} | {_fmt(b['ap50'])} | "
                f"{_fmt(d['point'])} [{_fmt(d['ci95'][0])}, {_fmt(d['ci95'][1])}] |"
            )
        lines.append("")
    return "\n".join(lines)


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    if isinstance(obj, np.generic):
        return _clean(obj.item())
    return obj


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--fold", type=Path, action="append", required=True)
    ap.add_argument("--groups", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    groups = json.loads(args.groups.read_text())
    folds = [evaluate_fold(f, groups) for f in args.fold]
    result = outcome(folds)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "eval.json").write_text(
        json.dumps(_clean({"outcome": result[0], "reason": result[1], "folds": folds}), indent=1)
    )
    (args.out / "eval.md").write_text(write_md(folds, result))
    print(write_md(folds, result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
