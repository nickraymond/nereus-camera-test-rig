"""Stage ``qc`` — exclude bad frames and patches, never repair them (SPEC §4 Phase 8 S1.7, §20).

Frame filter (the whole frame is excluded): flash fired, diver torch in view (dataset config
``torch_frames``), no RAW, patch sampling failed, or any grey patch smaller than
``MIN_PATCH_RAW_PX`` RAW pixels on a side (brief §7 P1.1 step 4). There is no blur measure
yet, so "too small for its blur" is the size rule alone.

Patch filter (that patch is excluded in that frame):

- partly outside the image; any channel clipped on more than ``CLIP_MAX`` of its pixels;
- **known damage map** (dataset config ``card_damage``): the listed patches, from
  ``from_stem`` onward by capture time. A parent patch is excluded with its damaged
  sub-patch (grey 128 goes with its left half; its right half is tested on its own);
- **cell test**: the max / min of the 3×3 cell means, in any channel, above the 99th
  percentile of the same patch on the clean reference frames (``clean_reference``);
- a patch with a cell under ``MIN_CELL_PX`` binned pixels cannot be cell-tested: it is
  ``damage: unknown`` — never ``clean`` — but not excluded for that alone.

Every exclusion carries its reason. Output: ``qc/{qc.json, summary.json, stage.json}``.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..config import ConfigError, load_yaml
from .card import Card, load_card
from .patches import INNER
from .stages import verify_fresh, write_stage

MIN_PATCH_RAW_PX = 30
CLIP_MAX = 0.01
MIN_CELL_PX = 16
BASELINE_PERCENTILE = 99


def cell_ratio(stats: dict) -> Optional[np.ndarray]:
    """Per-channel max / min of the 3×3 cell means; None if any cell is too small."""
    if not stats.get("cells") or np.min(stats["cell_n"]) < MIN_CELL_PX:
        return None
    cells = np.asarray(stats["cells"], dtype=np.float64).reshape(-1, 3)
    lo, hi = cells.min(axis=0), cells.max(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(lo > 0, hi / lo, np.inf)


def patch_side_raw_px(stats: dict) -> float:
    """Shorter side of the whole patch in RAW pixels (sampled side is binned, central 60 %)."""
    return min(stats["size_px"]) * 2 / INNER


def baseline(frames: dict[str, dict], stems: list[str], ids: list[str]) -> dict[str, list]:
    """99th-percentile cell ratio per patch and channel over the clean reference frames."""
    out = {}
    for pid in ids:
        ratios = [r for s in stems
                  for r in [cell_ratio(frames[s]["raw"]["patches"].get(pid, {}))]
                  if r is not None and np.all(np.isfinite(r))]
        out[pid] = (np.percentile(ratios, BASELINE_PERCENTILE, axis=0).round(4).tolist()
                    if ratios else None)
    return out


def _damage_ids(card: Card, listed: list[str]) -> set[str]:
    parents = {s.id: s.parent for s in card.sub_patches}
    return set(listed) | {parents[p] for p in listed if p in parents}


def qc_patch(stats: dict, pid: str, known_damage: bool, threshold) -> dict[str, Any]:
    reasons, codes = [], []
    if not stats.get("n_px"):
        return {"usable": False, "damage": "unknown", "reasons": ["no pixels sampled"],
                "codes": ["no_pixels"]}
    if not stats["in_frame"]:
        reasons.append("partly outside the image")
        codes.append("outside_image")
    if max(stats.get("clip_frac") or [0]) > CLIP_MAX:
        reasons.append(f"clipped (> {CLIP_MAX:.0%} of pixels)")
        codes.append("clipped")
    ratio = cell_ratio(stats)
    rec: dict[str, Any] = {"cell_ratio": None if ratio is None else
                           [None if not np.isfinite(v) else round(float(v), 4) for v in ratio]}
    if known_damage:
        rec["damage"] = "known"
        reasons.append("known water damage (card_damage map)")
        codes.append("known_damage")
    elif ratio is None or threshold is None:
        rec["damage"] = "unknown"
    elif np.any(ratio > np.asarray(threshold)):
        rec["damage"] = "cells"
        worst = int(np.argmax(ratio / np.asarray(threshold)))
        reasons.append(f"cell ratio {ratio[worst]:.2f} > clean p{BASELINE_PERCENTILE} "
                       f"{threshold[worst]:.2f} ({'RGB'[worst]})")
        codes.append("cell_ratio")
    else:
        rec["damage"] = "clean"
    return {"usable": not reasons, **rec, "reasons": reasons, "codes": codes}


def qc(patches_dir: Path, dataset_config: Path, card_path: Path) -> dict[str, Any]:
    verify_fresh(patches_dir)
    root = patches_dir.parent
    rows = {r["stem"]: r for r in csv.DictReader((root / "ingest" / "manifest.csv").open())}
    frames = json.loads((patches_dir / "patches.json").read_text())
    cfg = load_yaml(dataset_config)
    card = load_card(card_path)
    damage = cfg.get("card_damage") or {}
    if damage and damage.get("from_stem") not in rows:
        raise ConfigError(f"{dataset_config}: card_damage.from_stem "
                          f"{damage.get('from_stem')!r} is not in the manifest")
    damaged_from = rows[damage["from_stem"]]["time_utc"] if damage else None
    damaged_ids = _damage_ids(card, damage.get("patches", [])) if damage else set()
    torch = set(cfg.get("torch_frames") or [])
    ids = [p.id for p in card.patches] + [s.id for s in card.sub_patches]
    greys = [p.id for p in card.group("grey")]

    ref = damage.get("clean_reference") or []
    ref_stems = [s for s in frames if "raw" in frames[s] and len(ref) == 2
                 and rows[ref[0]]["time_utc"] <= rows[s]["time_utc"] <= rows[ref[1]]["time_utc"]]
    thresholds = baseline(frames, ref_stems, ids)

    out: dict[str, Any] = {}
    for stem, data in frames.items():
        row = rows[stem]
        condition = ("damaged" if damaged_from and row["time_utc"] >= damaged_from
                     else "clean")
        reasons = []
        if row["flash_fired"] == "True":
            reasons.append("flash fired")
        if stem in torch:
            reasons.append("diver torch in view")
        if "error" in data:
            reasons.append(f"patch sampling failed: {data['error']}")
        if "raw" not in data:
            reasons.append("no RAW")
            out[stem] = {"usable": False, "card_condition": condition, "reasons": reasons,
                         "patches": {}}
            continue
        raw = data["raw"]["patches"]
        small = [p for p in greys if raw[p].get("n_px") and
                 patch_side_raw_px(raw[p]) < MIN_PATCH_RAW_PX]
        if small or any(not raw[p].get("n_px") for p in greys):
            reasons.append(f"grey patch < {MIN_PATCH_RAW_PX} RAW px")
        patches_qc = {pid: qc_patch(raw[pid], pid, condition == "damaged" and pid in damaged_ids,
                                    thresholds.get(pid)) for pid in ids}
        out[stem] = {"usable": not reasons, "card_condition": condition, "reasons": reasons,
                     "patches": patches_qc}

    summary = _summary(out, ids, ref_stems, thresholds)
    out_dir = root / "qc"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "qc.json").write_text(json.dumps(out, indent=1) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_stage(out_dir, "qc", configs=[dataset_config, card_path], upstream=[patches_dir],
                params={"min_patch_raw_px": MIN_PATCH_RAW_PX, "clip_max": CLIP_MAX,
                        "min_cell_px": MIN_CELL_PX, "baseline_percentile": BASELINE_PERCENTILE,
                        "clean_reference_frames": ref_stems, "thresholds": thresholds})
    summary["out_dir"] = str(out_dir)
    return summary


def _summary(out: dict, ids: list[str], ref_stems: list[str], thresholds: dict) -> dict:
    by_condition: dict[str, dict] = {}
    frame_reasons: dict[str, int] = {}
    patch_reasons: dict[str, int] = {}
    for rec in out.values():
        c = by_condition.setdefault(rec["card_condition"], {
            "frames": 0, "usable_frames": 0, "usable_patches": 0,
            "damage": {"clean": 0, "known": 0, "cells": 0, "unknown": 0},
            "usable_by_patch": {pid: 0 for pid in ids}})
        c["frames"] += 1
        for r in rec["reasons"]:
            key = r.split(":")[0]
            frame_reasons[key] = frame_reasons.get(key, 0) + 1
        if not rec["usable"]:
            continue
        c["usable_frames"] += 1
        for pid, p in rec["patches"].items():
            c["damage"][p["damage"]] += 1
            if p["usable"]:
                c["usable_patches"] += 1
                c["usable_by_patch"][pid] += 1
            for code in p["codes"]:
                patch_reasons[code] = patch_reasons.get(code, 0) + 1
    return {"frames": len(out), "by_card_condition": by_condition,
            "frame_exclusions": frame_reasons, "patch_exclusions_in_usable_frames": patch_reasons,
            "clean_reference_frames": ref_stems,
            "patches_without_baseline": [p for p, t in thresholds.items() if t is None]}
