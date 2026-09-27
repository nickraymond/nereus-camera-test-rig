"""Stage ``locate`` — card position per frame (SPEC §4 Phase 8 S1, brief §7 P1.1 step 2).

Method (a), AprilTags, on the camera JPEG: detect at 1×, ½× and ¼× and merge the card tags
found at any scale (the finest scale's corners win). On the TG-7 set, downscaling finds large or
soft tags the native pass misses (131 → 142 of 275 frames with ≥ 3 tags) while upscaling finds
almost nothing and costs 4–30× more, so 2× is only a last-resort retry. ≥ 3 corner tags → the
tag-centre quad, a single missing corner inferred as a parallelogram (``min_tags=3``). The quad
must be convex with a plausible width/height ratio (design 3.985; TG-7 range 3.5–4.7), else it
is rejected — guards against a false tag detection. Fallbacks (b) sweep-neighbour search and
(c) manual clicks arrive in S1.3; until then an unlocated frame is recorded with its reason.

The stored quad is the **tag-centre quad** (TL, TR, BR, BL), ``quad_type: tag_centers``, in
both JPEG and RAW-mosaic pixel coordinates (RAW = JPEG + the dataset's
``jpeg_offset_in_raw``). Output: ``locate/{corners.json, summary.json, stage.json}``.
"""

from __future__ import annotations

import csv
import json
import os
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..analysis.apriltag_detector import DetectionOutcome, TagDetection, detect_tags
from ..analysis.reference_card import CardLocalizationError, infer_card_corners_from_tags
from ..config import load_yaml
from .card import load_card
from .stages import verify_fresh, write_stage

DEFAULT_NO_CARD = ["4_no_card"]
SCALES = (1.0, 0.5, 0.25)
RATIO_RANGE = (2.5, 6.0)  # tag-quad width / height; design 364.9 / 91.566 = 3.985


def _detect_multiscale(gray: np.ndarray, card_ids: set, scales) -> tuple[dict, dict]:
    """Card tags found at any of ``scales`` (native-pixel coordinates), finest scale first."""
    tags: dict[int, TagDetection] = {}
    found_at: dict[int, float] = {}
    for s in scales:
        img = gray if s == 1 else cv2.resize(
            gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        for i, t in detect_tags(img, scales=(1,)).tags.items():
            if i in card_ids and i not in tags:
                corners = t.corners / s
                tags[i] = TagDetection(i, corners, (float(corners[:, 0].mean()),
                                                    float(corners[:, 1].mean())),
                                       t.side_px_min / s)
                found_at[i] = s
    return tags, found_at


def plausible(quad: np.ndarray) -> bool:
    """Convex TL,TR,BR,BL quad with a card-like width/height ratio."""
    edges = [quad[(i + 1) % 4] - quad[i] for i in range(4)]
    cross = [float(edges[i][0] * edges[(i + 1) % 4][1] - edges[i][1] * edges[(i + 1) % 4][0])
             for i in range(4)]
    if not (all(c > 0 for c in cross) or all(c < 0 for c in cross)):
        return False
    width = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
    height = (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / 2
    return height > 0 and RATIO_RANGE[0] <= width / height <= RATIO_RANGE[1]


def locate_frame(jpeg: Path, corner_map: dict[str, int], offset: tuple[float, float],
                 scales=SCALES, retry_scale: float = 2.0) -> dict[str, Any]:
    """Locate the card in one JPEG; returns a JSON-able record (located or not, with why)."""
    card_ids = set(corner_map.values())
    gray = cv2.imread(str(jpeg), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return {"located": False, "locate_method": None, "tags_found": [],
                "reason": f"could not read {jpeg}"}
    tags, found_at = _detect_multiscale(gray, card_ids, scales)
    if len(tags) < 3 and retry_scale and retry_scale not in scales:
        more, more_at = _detect_multiscale(gray, card_ids, (retry_scale,))
        for i, t in more.items():
            if i not in tags:
                tags[i], found_at[i] = t, more_at[i]
    record: dict[str, Any] = {
        "tags_found": sorted(tags),
        "found_at_scale": {str(i): found_at[i] for i in sorted(tags)},
        "tag_side_px_min": {str(i): round(t.side_px_min, 1) for i, t in sorted(tags.items())},
    }
    try:
        quad, inferred = infer_card_corners_from_tags(
            DetectionOutcome(tags=tags), corner_map, min_tags=3)
    except CardLocalizationError:
        record.update(located=False, locate_method=None,
                      reason=f"{len(tags)} of 4 card tags found (need 3)")
        return record
    if not plausible(quad):
        record.update(located=False, locate_method=None,
                      reason="implausible tag-quad geometry (non-convex or bad aspect ratio)")
        return record
    record.update(
        located=True,
        locate_method="apriltag4" if not inferred else "apriltag3",
        inferred_corners=list(inferred),
        quad_type="tag_centers",
        quad_jpeg=[[round(float(x), 2), round(float(y), 2)] for x, y in quad],
        quad_raw=[[round(float(x) + offset[0], 2), round(float(y) + offset[1], 2)]
                  for x, y in quad],
    )
    return record


def locate(ingest_dir: Path, card_path: Path, dataset_config: Path,
           workers: int | None = None) -> dict[str, Any]:
    """Run ``locate`` over every card-bearing frame of an ingested dataset."""
    ingest_record = verify_fresh(ingest_dir)
    dataset_dir = Path(ingest_record["params"]["dataset_dir"])
    cfg = load_yaml(dataset_config)
    card = load_card(card_path)
    no_card = cfg.get("no_card_categories", DEFAULT_NO_CARD)
    offset = tuple(float(v) for v in cfg.get("jpeg_offset_in_raw", (0, 0)))

    rows = [r for r in csv.DictReader((ingest_dir / "manifest.csv").open())
            if r["category"] not in no_card]
    frame = partial(locate_frame, corner_map=card.corner_map, offset=offset)
    jpegs = [dataset_dir / r["jpeg"] for r in rows]
    workers = workers or os.cpu_count() or 1
    if workers > 1:
        with ProcessPoolExecutor(workers) as pool:
            records = list(pool.map(frame, jpegs))
    else:
        records = [frame(j) for j in jpegs]
    corners: dict[str, Any] = {}
    counts: dict[str, dict[str, int]] = {}
    for row, rec in zip(rows, records):
        rec.update(category=row["category"], dive_id=row["dive_id"], sweep_id=row["sweep_id"])
        corners[row["stem"]] = rec
        key = rec["locate_method"] or "unlocated"
        counts.setdefault(row["category"], {}).setdefault(key, 0)
        counts[row["category"]][key] += 1

    out_dir = ingest_dir.parent / "locate"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "corners.json").write_text(json.dumps(corners, indent=1) + "\n")
    summary = {"frames": len(corners), "by_category": counts,
               "located": sum(r["located"] for r in corners.values()),
               "unlocated": sum(not r["located"] for r in corners.values())}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_stage(out_dir, "locate", configs=[card_path, dataset_config], upstream=[ingest_dir],
                params={"method": "apriltag", "scales": list(SCALES), "retry_scale": 2.0,
                        "min_tags": 3, "ratio_range": list(RATIO_RANGE),
                        "jpeg_offset_in_raw": list(offset)})
    summary["out_dir"] = str(out_dir)
    return summary
