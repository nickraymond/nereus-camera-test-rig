"""Stage ``jpeg-map`` — fit and validate the RAW → camera-JPEG pixel map (OQ-42).

For every located frame, the card tags ``locate`` found on the **RAW** are looked for again,
independently, on the camera JPEG (same detector, 1×, ½×, ¼×). The matched tag centres give a
least-squares radial map (``jpeg_geometry.fit_robust``), validated three ways:

- error by radius band, next to the plain-crop map it replaces;
- leave-one-dive-out: fit on the other dives, score the held-out one;
- per frame: frames whose JPEG tags sit more than ``FRAME_TOL_PX`` from the map are listed —
  candidates for ``exclude_frames`` (their JPEG is then not scored; exclude, never repair).

The fitted map is **not** used directly: ``summary.json`` carries a ``config_block`` to review
and copy into the dataset config (``jpeg_from_raw``), and ``config_matches`` says whether the
map currently configured agrees with this fit (max gap over the frame < ``MATCH_TOL_PX``).
Output: ``jpeg_map/{pairs.json, summary.json, stage.json}``.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from ..config import load_yaml
from .jpeg_geometry import JpegMap, fit_robust, read_jpeg
from .locate import SCALES, TagSpec, _detect
from .stages import run_parallel, verify_fresh, write_stage

FRAME_TOL_PX = 4.0
MATCH_TOL_PX = 1.0
BANDS = (0, 500, 1000, 1500, 2000, 2600)


def jpeg_tags(jpeg: Path, tag_ids: list[int],
              family: str = TagSpec.family) -> dict[str, list[float]]:
    """{tag id: centre} of the card tags found on one camera JPEG (stored orientation)."""
    gray = read_jpeg(jpeg, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return {}
    return {str(i): [float(v) for v in t.center]
            for i, (t, _) in _detect(gray, set(tag_ids), SCALES, family=family).items()}


def _stats(err: np.ndarray) -> dict[str, Any]:
    if not len(err):
        return {"n": 0}
    return {"n": int(len(err)), "median_px": round(float(np.median(err)), 2),
            "p95_px": round(float(np.percentile(err, 95)), 2),
            "max_px": round(float(err.max()), 2)}


def _frame_gap(a: JpegMap, b: JpegMap, raw: np.ndarray) -> float:
    """Largest distance between two maps' JPEG positions over the box the tags cover."""
    (x0, y0), (x1, y1) = raw.min(axis=0), raw.max(axis=0)
    xx, yy = np.meshgrid(np.linspace(x0, x1, 41), np.linspace(y0, y1, 31))
    pts = np.stack([xx, yy], axis=-1).reshape(-1, 2)
    return float(np.linalg.norm(a.to_jpeg(pts) - b.to_jpeg(pts), axis=1).max())


def config_block(m: JpegMap, n_pairs: int, n_frames: int, exclude: dict) -> str:
    """YAML to paste into the dataset config (reviewed by a human, then versioned)."""
    ex = "".join(f"\n    {s}: \"{r}\"" for s, r in sorted(exclude.items())) or " {}"
    return (f"jpeg_from_raw:\n"
            f"  source: jpeg-map stage, {n_pairs} tag pairs on {n_frames} frames\n"
            f"  centre_raw: [{m.centre_raw[0]:.1f}, {m.centre_raw[1]:.1f}]\n"
            f"  centre_jpeg: [{m.centre_jpeg[0]:.2f}, {m.centre_jpeg[1]:.2f}]\n"
            f"  k: [{', '.join(f'{v:.6f}' for v in m.k)}]\n"
            f"  exclude_frames:{ex}\n")


def jpeg_map(locate_dir: Path, dataset_config: Path,
             workers: Optional[int] = None) -> dict[str, Any]:
    family = verify_fresh(locate_dir)["params"].get("tag_family", TagSpec.family)
    ingest_dir = locate_dir.parent / "ingest"
    dataset_dir = Path(verify_fresh(ingest_dir)["params"]["dataset_dir"])
    rows = {r["stem"]: r for r in csv.DictReader((ingest_dir / "manifest.csv").open())}
    corners = json.loads((locate_dir / "corners.json").read_text())
    configured = JpegMap.from_config(load_yaml(dataset_config))

    raw_tags = {s: {i: c for i, c in rec.get("tag_centers_raw", {}).items()
                    if rec["tag_source"][i] == "raw"}
                for s, rec in corners.items() if rows[s]["jpeg"]}
    stems = [s for s, t in raw_tags.items() if t]
    jobs = [(dataset_dir / rows[s]["jpeg"], [int(i) for i in raw_tags[s]], family)
            for s in stems]
    found = dict(zip(stems, run_parallel(jpeg_tags, jobs, workers or os.cpu_count() or 1)))

    pairs = [(s, i, raw_tags[s][i], c) for s in stems for i, c in found[s].items()]
    if len(pairs) < 12:
        raise ValueError(f"{locate_dir}: only {len(pairs)} RAW/JPEG tag pairs — too few to fit")
    stem = np.array([p[0] for p in pairs])
    dive = np.array([rows[s]["dive_id"] for s in stem])
    raw = np.array([p[2] for p in pairs], np.float64)
    jpg = np.array([p[3] for p in pairs], np.float64)
    start = raw.mean(axis=0)

    fitted, keep, err = fit_robust(raw, jpg, start=start)
    radius = np.linalg.norm(raw - fitted.centre_raw, axis=1)
    crop_err = np.linalg.norm(configured.to_jpeg(raw) - jpg, axis=1)
    bands = {f"{lo}-{hi}": {"map": _stats(err[m]), "configured": _stats(crop_err[m])}
             for lo, hi in zip(BANDS, BANDS[1:])
             for m in [keep & (radius >= lo) & (radius < hi)] if m.any()}
    held_out = {}
    for d in sorted(set(dive)):
        train, test = keep & (dive != d), keep & (dive == d)
        m = fit_robust(raw[train], jpg[train], start=start, rounds=1)[0]
        held_out[d] = _stats(np.linalg.norm(m.to_jpeg(raw[test]) - jpg[test], axis=1))
    frames = {}
    for s in sorted(set(stem)):
        m = stem == s
        if err[m].max() > FRAME_TOL_PX:
            shift = (jpg[m] - fitted.to_jpeg(raw[m])).mean(axis=0)
            frames[s] = {"category": rows[s]["category"], "tags": int(m.sum()),
                         "max_err_px": round(float(err[m].max()), 1),
                         "mean_shift_px": [round(float(v), 1) for v in shift]}

    out_dir = locate_dir.parent / "jpeg_map"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pairs.json").write_text(json.dumps(
        [{"stem": p[0], "tag": p[1], "raw": p[2], "jpeg": p[3], "err_px": round(float(e), 2),
          "inlier": bool(k)} for p, e, k in zip(pairs, err, keep)], indent=1) + "\n")
    gap = _frame_gap(configured, fitted, raw)
    summary = {"frames_with_raw_tags": len(stems), "pairs": len(pairs),
               "frames_paired": len(set(stem)), "inliers": int(keep.sum()),
               "fitted": fitted.as_dict(), "fit_error": _stats(err[keep]),
               "by_radius": bands, "leave_one_dive_out": held_out,
               "frames_over_tolerance": frames, "frame_tol_px": FRAME_TOL_PX,
               "configured": configured.as_dict(),
               "configured_vs_fitted_max_gap_px": round(gap, 2),
               "config_matches": gap < MATCH_TOL_PX,
               "config_block": config_block(fitted, len(pairs), len(set(stem)),
                                            configured.exclude_frames)}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_stage(out_dir, "jpeg_map", configs=[dataset_config], upstream=[locate_dir],
                params={"scales": list(SCALES), "tag_family": family,
                        "frame_tol_px": FRAME_TOL_PX,
                        "match_tol_px": MATCH_TOL_PX, "tag_source": "raw only"})
    summary["out_dir"] = str(out_dir)
    return summary
