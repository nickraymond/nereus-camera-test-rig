"""Stage ``locate`` — card position per frame (SPEC §4 Phase 8 S1, brief §7 P1.1 step 2).

**RAW first** (SPEC §20): tags are detected on a detection image built from the RAW — the
linear green channel (most signal underwater; no JPEG noise reduction, sharpening or clipping),
exposure-stretched and gamma-encoded. The camera JPEG is used only to add tags the RAW pass
missed, or when a shot has no RAW; each tag records its source.

- **(a) whole frame:** detect at 1×, ½× and ¼× and merge (finest scale's corners win) —
  downscaling finds large/soft tags the native pass misses; upscaling a 12 MP frame costs
  4–30× and rarely helps.
- **(b) window:** a frame still unlocated after (a) is searched again inside a window around
  the card's position in the nearest located frame of the same dive within ``window_max_s``
  (same sweep preferred). It is searched at 1×–4×, but a scaled window never exceeds
  ~16 MP — near-frame neighbours have large cards, so their windows can span most of the
  frame, and a 4× upscale of that is the 190 MP cost this stage otherwise avoids.
- **(c) manual:** tag centres clicked with ``host_tools.color click`` go to the dataset's
  versioned manual-corners file (``manual_corners`` in the dataset config, next to it; human
  work must survive a results/ or worktree cleanup). This stage only reads it, never writes.

What to look for comes from the card YAML (``tag_spec``): the tag dictionary
(``apriltag.family``) and the tag-quad width/height range (``apriltag.quad_ratio`` ×
``RATIO_TOLERANCE``; V2 3.985 → 2.5–6.0). A tag cut by the edge of the searched image is
dropped and listed in ``tags_at_border``. Frames up to ``SMALL_FRAME_PX`` (OpenMV 1280 × 800)
also get a 2× pass.

≥ 3 corner tags → the tag-centre quad. A single missing corner is inferred from a homography
fitted to the 12 corners of the 3 found tags (card geometry from the card YAML), which is
exact under perspective; without card geometry it falls back to the parallelogram of
``infer_card_corners_from_tags`` (exact only under an affine view — on close TG-7 cards it
was off by up to ~50 px, enough to push patch samples onto the neighbouring patch). The quad must be convex with a card-like width/height ratio (design 3.985),
else it is rejected. Quads are stored in RAW-mosaic and JPEG pixel coordinates; the JPEG
quad is the RAW one through the dataset's RAW → JPEG map (``jpeg_from_raw``, OQ-42 — the TG-7
JPEG is radially remapped, not a plain crop), and tags found on the JPEG are mapped back to
RAW the same way. Output: ``locate/{corners.json, summary.json,
stage.json}``; unlocated frames are recorded with their reason, never dropped.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np

from ..analysis.apriltag_detector import DEFAULT_FAMILY, DetectionOutcome, TagDetection, detect_tags
from ..analysis.reference_card import CardLocalizationError, infer_card_corners_from_tags
from ..config import load_yaml
from .card import load_card
from .jpeg_geometry import JpegMap, read_jpeg
from .raw_io import RawFrame, demosaic_bilinear, normalize
from .stages import run_parallel, sha256_file, verify_fresh, write_stage

DEFAULT_NO_CARD = ["4_no_card"]
SCALES = (1.0, 0.5, 0.25)
WINDOW_SCALES = (1.0, 2.0, 3.0, 4.0)
WINDOW_MAX_S = 120.0
MAX_SCALED_PIXELS = 16e6  # never upscale a window past ~16 MP (a 4x full frame is 190 MP)
RATIO_RANGE = (2.5, 6.0)  # tag-quad width / height, V2 (design 364.9 / 91.566 = 3.985)
RATIO_TOLERANCE = (0.627, 1.506)  # × the card's quad ratio (V2: 3.985 → 2.5–6.0)
BORDER_FRACTION = 0.07  # of the tag side (~½ cell): a tag with a corner this close is cut
BORDER_PX = 3  # ... and never less than this
SMALL_FRAME_PX = 3e6  # frames up to this size (OpenMV 1280 × 800) also get a 2× pass
MANUAL_FILE = "manual_corners.json"


@dataclass(frozen=True)
class TagSpec:
    """What ``locate`` looks for: the card's tag dictionary and tag-quad width/height range."""

    family: str = DEFAULT_FAMILY
    ratio_range: tuple[float, float] = RATIO_RANGE


def tag_spec(card) -> TagSpec:
    """From the card YAML (``apriltag.family``, ``apriltag.quad_ratio``)."""
    ratio = card.quad_ratio
    rng = (ratio * RATIO_TOLERANCE[0], ratio * RATIO_TOLERANCE[1]) if ratio else RATIO_RANGE
    return TagSpec(card.aruco_dictionary, (round(rng[0], 4), round(rng[1], 4)))


def frame_scales(shape, scales) -> tuple[float, ...]:
    """``scales``, plus a 2× pass on small frames (tags there are often too few pixels)."""
    if shape[0] * shape[1] <= SMALL_FRAME_PX and 2.0 not in scales:
        return (scales[0], 2.0, *scales[1:])
    return tuple(scales)


def at_border(corners: np.ndarray, shape) -> bool:
    """A tag cut by the image edge can still decode, with its corners wrong (20–60 px on real
    frames, card-V3 review). Measured with OpenCV: a 120 px 25h9 tag cut by 2–5 px is found
    with its edge 6 px (a third of a cell) inside the frame; cut deeper it does not decode.
    An uncut tag sits at least its quiet zone from the edge (the V2 render: 21 px at 223 px).
    So a tag whose corners come within ``BORDER_FRACTION`` of its side (~½ cell) counts as
    cut."""
    h, w = shape[:2]
    c = np.asarray(corners)
    side = min(np.linalg.norm(c[(i + 1) % 4] - c[i]) for i in range(4))
    margin = max(BORDER_PX, BORDER_FRACTION * side)
    return bool((c[:, 0] < margin).any() or (c[:, 1] < margin).any()
                or (c[:, 0] > w - 1 - margin).any() or (c[:, 1] > h - 1 - margin).any())

RawReader = Callable[[Path], RawFrame]


def raw_detection_image(frame: RawFrame) -> tuple[np.ndarray, tuple[int, int]]:
    """8-bit detection image from the RAW green channel + its origin in mosaic pixels."""
    linear, _, cfa = normalize(frame)
    green = demosaic_bilinear(linear, cfa)[..., 1]
    top = float(np.percentile(green, 99.5)) or 1.0
    img = np.clip(green / top, 0.0, 1.0) ** (1 / 2.2)
    x, y = (frame.valid_crop or (0, 0))[:2]
    return (img * 255 + 0.5).astype(np.uint8), (x, y)


def _detect(gray: np.ndarray, card_ids: set, scales, shift=(0.0, 0.0),
            family: str = DEFAULT_FAMILY, border: Optional[set] = None) -> dict:
    """{tag_id: (TagDetection in shifted native coords, scale)} for card tags, finest first.
    Tags cut by the image edge are skipped (their ids added to ``border`` if given)."""
    found: dict[int, tuple[TagDetection, float]] = {}
    for s in scales:
        if s > 1 and gray.shape[0] * gray.shape[1] * s * s > MAX_SCALED_PIXELS:
            continue
        img = gray if s == 1 else cv2.resize(
            gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        for i, t in detect_tags(img, family=family, scales=(1,)).tags.items():
            if i in card_ids and at_border(t.corners, img.shape):
                if border is not None:
                    border.add(i)
                continue
            if i in card_ids and i not in found:
                corners = t.corners / s + np.asarray(shift)
                found[i] = (TagDetection(i, corners, (float(corners[:, 0].mean()),
                                                      float(corners[:, 1].mean())),
                                         t.side_px_min / s), s)
    return found


def plausible(quad: np.ndarray, ratio_range=RATIO_RANGE) -> bool:
    """Convex TL,TR,BR,BL quad with a card-like width/height ratio."""
    edges = [quad[(i + 1) % 4] - quad[i] for i in range(4)]
    cross = [float(edges[i][0] * edges[(i + 1) % 4][1] - edges[i][1] * edges[(i + 1) % 4][0])
             for i in range(4)]
    if not (all(c > 0 for c in cross) or all(c < 0 for c in cross)):
        return False
    width = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
    height = (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / 2
    return height > 0 and ratio_range[0] <= width / height <= ratio_range[1]


def tag_geometry(card) -> tuple[float, float, float]:
    """(tag-centre spacing x, y, tag edge) in mm from a loaded card."""
    mm = card.physical_mm
    return (mm["tag_center_spacing_x"], mm["tag_center_spacing_y"], mm["tag_edge"])


_CENTRES = {"tl": (0, 0), "tr": (1, 0), "br": (1, 1), "bl": (0, 1)}
_TAG_CORNERS = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]]) / 2  # detector order TL,TR,BR,BL


def infer_quad_from_tag_corners(tags: dict, corner_map: dict[str, int],
                                geometry: tuple[float, float, float]) -> np.ndarray:
    """Tag-centre quad (TL,TR,BR,BL) from a homography on the found tags' corners.

    Card tags are printed upright, so detector corner k of every tag is the same card
    direction (checked on the V2 render). Needs ≥ 2 tags; used for exactly 3.
    """
    sx, sy, edge = geometry
    src, dst = [], []
    for name, tid in corner_map.items():
        if tid in tags:
            cx, cy = _CENTRES[name]
            src.extend(np.array([cx * sx, cy * sy]) + _TAG_CORNERS * edge)
            dst.extend(np.asarray(tags[tid].corners, dtype=np.float64))
    H, _ = cv2.findHomography(np.asarray(src), np.asarray(dst), 0)
    if H is None:
        raise CardLocalizationError("tag-corner homography failed")
    centres = np.array([[_CENTRES[k][0] * sx, _CENTRES[k][1] * sy]
                        for k in ("tl", "tr", "br", "bl")], dtype=np.float64)
    return cv2.perspectiveTransform(centres.reshape(-1, 1, 2), H).reshape(4, 2)


def _record(tags: dict, corner_map: dict, jpeg_map: JpegMap, method: str,
            geometry: Optional[tuple] = None, ratio_range=RATIO_RANGE,
            border: frozenset = frozenset()) -> dict[str, Any]:
    """Build the JSON record from {id: (TagDetection in RAW coords, scale, source)}."""
    record: dict[str, Any] = {
        "tags_found": sorted(tags),
        "tag_source": {str(i): tags[i][2] for i in sorted(tags)},
        "found_at_scale": {str(i): tags[i][1] for i in sorted(tags)},
        "tag_side_px_min": {str(i): round(tags[i][0].side_px_min, 1) for i in sorted(tags)},
        "tag_centers_raw": {str(i): [round(c, 2) for c in tags[i][0].center]
                            for i in sorted(tags)},
    }
    if border - set(tags):
        record["tags_at_border"] = sorted(border - set(tags))  # seen, cut by the edge, unused
    try:
        quad, inferred = infer_card_corners_from_tags(
            DetectionOutcome(tags={i: t[0] for i, t in tags.items()}), corner_map, min_tags=3)
        if inferred and geometry is not None:
            known = {tid: t[0] for tid, t in tags.items()}
            known_quad = infer_quad_from_tag_corners(known, corner_map, geometry)
            # keep the detected centres; only the missing corner comes from the homography
            idx = ("tl", "tr", "br", "bl").index(inferred[0])
            quad = quad.copy()
            quad[idx] = known_quad[idx]
            record["inference"] = "homography_tag_corners"
        elif inferred:
            record["inference"] = "parallelogram"
    except CardLocalizationError:
        return {**record, "located": False, "locate_method": None,
                "reason": f"{len(tags)} of 4 card tags found (need 3)"}
    if not plausible(quad, ratio_range):
        return {**record, "located": False, "locate_method": None,
                "reason": "implausible tag-quad geometry (non-convex or bad aspect ratio)"}
    return {**record, "located": True,
            "locate_method": f"{method}{'4' if not inferred else '3'}",
            "inferred_corners": list(inferred), "quad_type": "tag_centers",
            "quad_raw": [[round(float(x), 2), round(float(y), 2)] for x, y in quad],
            "quad_jpeg": jpeg_map.to_jpeg(quad).round(2).tolist()}


def _images(raw: Optional[Path], jpeg: Optional[Path], raw_reader: Optional[RawReader]):
    raw_img = raw_origin = jpg = None
    if raw is not None and raw_reader is not None:
        raw_img, raw_origin = raw_detection_image(raw_reader(raw))
    if jpeg is not None:
        jpg = read_jpeg(jpeg, cv2.IMREAD_GRAYSCALE)
    return raw_img, raw_origin, jpg


def _crop(img, origin, window):
    """Crop ``img`` (whose (0, 0) is at ``origin`` in RAW coords) to a RAW-coord window."""
    ox, oy = int(round(origin[0])), int(round(origin[1]))
    if window is None:
        return img, (ox, oy)
    x0, y0, x1, y1 = (int(v) for v in window)
    cx0, cy0 = max(0, x0 - ox), max(0, y0 - oy)
    cx1, cy1 = max(0, min(img.shape[1], x1 - ox)), max(0, min(img.shape[0], y1 - oy))
    return img[cy0:cy1, cx0:cx1], (ox + cx0, oy + cy0)


def _to_raw(t: TagDetection, jpeg_map: JpegMap) -> TagDetection:
    corners = jpeg_map.to_raw(t.corners)
    return TagDetection(t.tag_id, corners, tuple(float(v) for v in corners.mean(axis=0)),
                        t.side_px_min)


def _jpeg_window(window, jpeg_map: JpegMap):
    """Bounding box in JPEG px of a RAW-coord window (edges sampled: the map is not affine)."""
    if window is None:
        return None
    x0, y0, x1, y1 = window
    t = np.linspace(0, 1, 9)
    edge = np.concatenate([np.stack([x0 + (x1 - x0) * t, np.full(9, y)], 1) for y in (y0, y1)]
                          + [np.stack([np.full(9, x), y0 + (y1 - y0) * t], 1) for x in (x0, x1)])
    j = jpeg_map.to_jpeg(edge)
    return (*j.min(axis=0), *j.max(axis=0))


def _search(raw_img, raw_origin, jpg, card_ids, jpeg_map, scales, window=None,
            spec: TagSpec = TagSpec()) -> tuple[dict, set]:
    """RAW pass, then the JPEG for tags the RAW missed. ``window`` is in RAW coords.
    Returns (tags, ids of tags skipped because an image edge cut them)."""
    tags: dict[int, tuple] = {}
    border: set = set()

    def scan(img):
        return scales if window else frame_scales(img.shape, scales)

    if raw_img is not None:
        crop, shift = _crop(raw_img, raw_origin, window)
        if crop.size:
            tags = {i: (*v, "raw") for i, v in _detect(crop, card_ids, scan(raw_img), shift,
                                                       spec.family, border).items()}
    if len(tags) < 4 and jpg is not None:
        crop, shift = _crop(jpg, (0, 0), _jpeg_window(window, jpeg_map))
        if crop.size:
            for i, (t, s) in _detect(crop, card_ids, scan(jpg), shift, spec.family,
                                     border).items():
                tags.setdefault(i, (_to_raw(t, jpeg_map), s, "jpeg"))
    return tags, border


def locate_frame(raw: Optional[Path], jpeg: Optional[Path], corner_map: dict[str, int],
                 jpeg_map: JpegMap, raw_reader: Optional[RawReader] = None,
                 window: Optional[tuple] = None,
                 geometry: Optional[tuple] = None, spec: TagSpec = TagSpec()) -> dict[str, Any]:
    """Locate the card in one shot, RAW first; ``window`` restricts to a RAW-coord box (b)."""
    try:
        raw_img, raw_origin, jpg = _images(raw, jpeg, raw_reader)
    except (OSError, ValueError, RuntimeError) as exc:
        return {"located": False, "locate_method": None, "tags_found": [],
                "reason": f"could not read shot: {exc}"}
    if raw_img is None and jpg is None:
        return {"located": False, "locate_method": None, "tags_found": [],
                "reason": "no readable RAW or JPEG"}
    scales = WINDOW_SCALES if window else SCALES
    tags, border = _search(raw_img, raw_origin, jpg, set(corner_map.values()), jpeg_map, scales,
                           window, spec)
    return _record(tags, corner_map, jpeg_map, "window" if window else "apriltag", geometry,
                   spec.ratio_range, frozenset(border))


def window_for(neighbour: dict) -> tuple[float, float, float, float]:
    """Search box (RAW coords) around a located neighbour's tag quad: ±1 width, ±2 heights."""
    q = np.asarray(neighbour["quad_raw"])
    (x0, y0), (x1, y1) = q.min(axis=0), q.max(axis=0)
    w, h = x1 - x0, y1 - y0
    return (float(x0 - w), float(y0 - 2 * h), float(x1 + w), float(y1 + 2 * h))


def nearest_located(stem: str, rows: dict, corners: dict, max_s: float) -> Optional[str]:
    """Nearest located frame in the same dive within ``max_s``; same sweep preferred."""
    me = rows[stem]
    t = datetime.fromisoformat(me["time_utc"])
    best = None
    for other, rec in corners.items():
        o = rows[other]
        if other == stem or not rec.get("located") or o["dive_id"] != me["dive_id"]:
            continue
        dt = abs((datetime.fromisoformat(o["time_utc"]) - t).total_seconds())
        if dt > max_s:
            continue
        key = (not (me["sweep_id"] and o["sweep_id"] == me["sweep_id"]), dt)
        if best is None or key < best[0]:
            best = (key, other)
    return best[1] if best else None


def manual_record(entry: dict, jpeg_map: JpegMap) -> dict[str, Any]:
    """A located record from clicked tag centres ({"quad_raw": [[x, y] ×4, TL,TR,BR,BL]})."""
    quad = np.asarray(entry["quad_raw"], dtype=np.float64)
    return {"located": True, "locate_method": "manual", "tags_found": [],
            "inferred_corners": [], "quad_type": "tag_centers",
            "quad_raw": quad.round(2).tolist(),
            "quad_jpeg": jpeg_map.to_jpeg(quad).round(2).tolist(),
            "clicked_utc": entry.get("clicked_utc")}


def manual_corners_path(dataset_config: Path, cfg: dict, out_dir: Path) -> Path:
    """The dataset's manual-corners file: ``manual_corners`` (relative to the dataset config),
    else ``<locate dir>/manual_corners.json``."""
    name = cfg.get("manual_corners")
    return dataset_config.parent / name if name else out_dir / MANUAL_FILE


def locate(ingest_dir: Path, card_path: Path, dataset_config: Path,
           raw_reader: Optional[RawReader] = None, workers: int | None = None) -> dict[str, Any]:
    """Run ``locate`` over every card-bearing frame of an ingested dataset.

    ``raw_reader`` opens the dataset's RAW files (the Mac CLI passes the TG-7 ORF reader); it
    must be a module-level function so worker processes can use it. Without it: JPEG only.
    """
    ingest_record = verify_fresh(ingest_dir)
    dataset_dir = Path(ingest_record["params"]["dataset_dir"])
    cfg = load_yaml(dataset_config)
    card = load_card(card_path)
    no_card = cfg.get("no_card_categories", DEFAULT_NO_CARD)
    jmap = JpegMap.from_config(cfg)
    max_s = float(cfg.get("window_max_s", WINDOW_MAX_S))
    workers = workers or os.cpu_count() or 1
    out_dir = ingest_dir.parent / "locate"

    rows = {r["stem"]: r for r in csv.DictReader((ingest_dir / "manifest.csv").open())
            if r["category"] not in no_card}

    def paths(r):
        raw = dataset_dir / r["file"] if r["has_raw"] == "True" else None
        return raw, (dataset_dir / r["jpeg"] if r["jpeg"] else None)

    geometry = tag_geometry(card)
    spec = tag_spec(card)
    frame = partial(locate_frame, corner_map=card.corner_map, jpeg_map=jmap,
                    raw_reader=raw_reader, geometry=geometry, spec=spec)
    stems = list(rows)
    corners = dict(zip(stems, run_parallel(frame, [paths(rows[s]) for s in stems], workers)))

    retry = [(s, n) for s in stems if not corners[s]["located"]
             for n in [nearest_located(s, rows, corners, max_s)] if n]
    jobs = [(*paths(rows[s]), card.corner_map, jmap, raw_reader, window_for(corners[n]),
             geometry, spec) for s, n in retry]
    for (stem, n), rec in zip(retry, run_parallel(locate_frame, jobs, workers)):
        if rec["located"]:
            corners[stem] = {**rec, "neighbour": n}
        else:
            corners[stem]["window_tried"] = {"neighbour": n, "result": rec["reason"]}

    manual_path = manual_corners_path(dataset_config, cfg, out_dir)
    manual = json.loads(manual_path.read_text()) if manual_path.is_file() else {}
    for stem, entry in manual.items():
        if stem in corners and not corners[stem]["located"]:
            if entry.get("skip"):
                corners[stem].update(manual_skip=True,
                                     reason=f"operator: {entry.get('reason', 'not usable')}")
            else:
                corners[stem] = manual_record(entry, jmap)

    counts: dict[str, dict[str, int]] = {}
    for stem, rec in corners.items():
        rec.update(category=rows[stem]["category"], dive_id=rows[stem]["dive_id"],
                   sweep_id=rows[stem]["sweep_id"])
        key = rec["locate_method"] or "unlocated"
        counts.setdefault(rec["category"], {}).setdefault(key, 0)
        counts[rec["category"]][key] += 1

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "corners.json").write_text(json.dumps(corners, indent=1) + "\n")
    summary = {"frames": len(corners), "by_category": counts,
               "located": sum(r["located"] for r in corners.values()),
               "unlocated": sum(not r["located"] for r in corners.values()),
               "manual_entries": len(manual)}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    # The manual-corners file is hashed as a config: new clicks make locate (and everything
    # downstream) stale, as SPEC §20 requires.
    configs = [card_path, dataset_config] + ([manual_path] if manual_path.is_file() else [])
    write_stage(out_dir, "locate", configs=configs, upstream=[ingest_dir],
                params={"source": "raw first, jpeg fallback" if raw_reader else "jpeg only",
                        "scales": list(SCALES), "window_scales": list(WINDOW_SCALES),
                        "window_max_s": max_s, "min_tags": 3,
                        "tag_family": spec.family, "ratio_range": list(spec.ratio_range),
                        "border_fraction": BORDER_FRACTION,
                        "small_frame_px": SMALL_FRAME_PX,
                        "jpeg_from_raw": jmap.as_dict(),
                        "manual_corners": str(manual_path),
                        "manual_corners_sha256": sha256_file(manual_path) if manual else None})
    summary["out_dir"] = str(out_dir)
    return summary
