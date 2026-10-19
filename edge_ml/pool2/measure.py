"""Per-frame W1-W5 measurements on one camera frame (spec rev 13 + rev 16), pure but for OpenCV.

Rev 16: tag corners are undistorted (pose.undistort_corners, bm #184's K + dist_coeffs) before the
pose; the image is never undistorted. Every board point (sample outlines, the board outline, the
slanted square, patches) is projected back into raw-image pixels from that pose with the same K +
coefficients (cv2.projectPoints), and every pixel measurement is taken there. On the guard path
(Lens.mapped False) corners stay raw and the projection has no distortion.

One record per frame (the wscore.py frame record, plus what the RESULTS tables need):

  tags / tag_ids     the card's corner tags decoded in this frame
  range_m            pose.board_range on the undistorted corners: median IPPE depth of the decoded
                     tags (None: no tag). range_raw_m: the same on raw corners (F10 raw, report)
  pos_m              board centre (the card centre) in camera coordinates, m, from one planar PnP
                     on every undistorted corner; speed = |d pos / dt| (wscore.speeds)
  reproj_px          RMS between the decoded raw corners and the pose's projection of them
  board_whole        F11: >= MIN_TAGS_H tags decoded, and the board outline (densified, so a
                     bowed edge counts) projected into raw pixels lies wholly inside the frame
  board_box          the projected board outline's bounds, clipped to the frame (W4 crops)
  samples            per urchin sample, in raw pixels: px across (longest projected diameter) and
                     the reference box (the projected outline's bounds)
  mtf50_cpp / _lpmm  slanted-edge MTF50 (median over the square's usable edges), cycles/px and
                     line pairs per mm at the board (px per mm measured across each edge)
  edge_w_px          slanted-edge 10-90 % width, median over its usable edges (W5)
  tag_edge_w_px      tag outer-edge 10-90 % width, median over usable tag edges (W5)
  de00               CIEDE2000 per card patch, camera output as is (uncorrected, W1 / W3 / W4)

Readings, stated for QE (not spec text):
  - MIN_TAGS_H = 2: a pose from one tag (4 corners) extrapolates ~3 tag widths to the samples, so
    W1 needs >= 2 decoded tags; a 1-tag frame still has a range (W2).
  - ROIs: the central half of each edge; across it 0.9 tag cell each side for tags (the black
    border cell and the light quiet cell, tag25h9 = 7 cells across the black square) and
    min(side / 4, slanted_edge.grey_margin_mm) for the square. An ROI with under MIN_HALF_PX
    either side of the edge or under MIN_ROI_PX along it, or off the frame, is skipped (no
    value), never padded. A 10-90 % width over MAX_WIDTH_SHARE of the ROI's width means the
    edge's flat sides aren't in the ROI (too blurred or too small for it): no value. Dry run on
    Friday's clip100 (N6 at 2 m, 3 px either side) read 0.2 px without these guards.
  - Colour: the patch's central 60 % (as color/patches.py), mean camera sRGB8 -> CIELAB (D65)
    vs the card YAML truth (measured when it has a measured block); no white balance, no L* match.
"""

from __future__ import annotations

import math
import statistics
from typing import Callable

import cv2
import numpy as np

from edge_ml.pool2 import pose, sfr
from edge_ml.pool2.layout import Layout
from nereus_camera_test_rig.color import metrics

MIN_TAGS_H = 2
MIN_ROI_PX = 8         # along the edge
MIN_HALF_PX = 5.0      # across the edge, each side (the ESF needs its flat sides)
MAX_WIDTH_SHARE = 0.5  # a 10-90 % width over half the ROI width is not trusted
TAG_CELLS = 7          # tag25h9: 5 x 5 data + a 1-cell black border each side
MIN_PATCH_PX = 9
OUTLINE_STEPS = 20     # points per board-outline edge for the whole-board test

Projector = Callable[[np.ndarray], np.ndarray]   # board mm (N x 2) -> raw image px (N x 2)


def _inside(pts: np.ndarray, w: int, h: int) -> bool:
    return bool(np.isfinite(pts).all() and (pts[:, 0] >= 0).all() and (pts[:, 0] <= w - 1).all()
                and (pts[:, 1] >= 0).all() and (pts[:, 1] <= h - 1).all())


def board_pose(tags_u: dict[int, np.ndarray], lay: Layout, K: np.ndarray):
    """(rvec, tvec mm) of the board from one planar PnP over every undistorted corner, or None."""
    ids = [i for i in tags_u if i in lay.tags_mm]
    if not ids:
        return None
    obj = np.concatenate([np.c_[lay.tags_mm[i], np.zeros(4)] for i in ids])
    img = np.concatenate([tags_u[i] for i in ids]).astype(float)
    ok, rvec, tvec = cv2.solvePnP(obj, img, K, None, flags=cv2.SOLVEPNP_IPPE)
    return (rvec, tvec) if ok and tvec[2, 0] > 0 else None


def projector(rvec, tvec, lens: pose.Lens) -> Projector:
    """Board mm -> raw image px with the lens (rev 16: projectPoints with dist_coeffs)."""
    def proj(pts_mm: np.ndarray) -> np.ndarray:
        p = np.asarray(pts_mm, dtype=np.float64).reshape(-1, 2)
        obj = np.c_[p, np.zeros(len(p))]
        uv, _ = cv2.projectPoints(obj, rvec, tvec, lens.K, lens.dist)
        return uv.reshape(-1, 2)
    return proj


def board_position(rvec, tvec, lay: Layout) -> list | None:
    """The card centre in camera coordinates, m."""
    R, _ = cv2.Rodrigues(rvec)
    c = lay.card
    p = R @ np.array([c.canonical_w / 20.0, c.canonical_h / 20.0, 0.0]) + tvec.ravel()
    return [round(float(v) / 1000.0, 5) for v in p] if p[2] > 0 else None


def sample_box(proj: Projector, centre_mm, diameter_mm: float, n: int = 72) -> dict:
    """A round sample's outline projected into raw pixels: bounds [x1, y1, x2, y2] (the reference
    box) and px across = the
    SHORTEST projected diameter (spec rev 18 item 10: strict, the 30 px gate), px_across_max the
    longest (reported beside it)."""
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    r = diameter_mm / 2.0
    uv = proj(np.c_[centre_mm[0] + r * np.cos(a), centre_mm[1] + r * np.sin(a)])
    half = n // 2
    diam = np.linalg.norm(uv[:half] - uv[half:], axis=1)
    across, across_max = float(np.min(diam)), float(np.max(diam))
    (x1, y1), (x2, y2) = uv.min(axis=0), uv.max(axis=0)
    return {"box": [float(x1), float(y1), float(x2), float(y2)], "px_across": across,
            "px_across_max": across_max}


def _densify(poly: np.ndarray, steps: int) -> np.ndarray:
    pts = []
    for k in range(len(poly)):
        a, b = poly[k], poly[(k + 1) % len(poly)]
        pts.extend(a + (b - a) * s for s in np.linspace(0, 1, steps, endpoint=False))
    return np.array(pts)


def _edge_roi(gray: np.ndarray, a: np.ndarray, b: np.ndarray, hw_px: float
              ) -> np.ndarray | None:
    """An axis-aligned ROI on the central half of image segment a-b, hw_px either side of it,
    oriented so the edge runs along the ROI's rows (transposed for a near-horizontal edge)."""
    m = (a + b) / 2.0
    d = b - a
    half_len = 0.25 * float(np.hypot(*d))
    vertical = abs(d[1]) >= abs(d[0])
    hx, hy = (hw_px, half_len) if vertical else (half_len, hw_px)
    x0, x1 = int(round(m[0] - hx)), int(round(m[0] + hx))
    y0, y1 = int(round(m[1] - hy)), int(round(m[1] + hy))
    h, w = gray.shape[:2]
    if not np.isfinite(hw_px) or hw_px < MIN_HALF_PX or x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return None
    roi = gray[y0:y1, x0:x1].astype(float)
    if min(roi.shape) < MIN_ROI_PX:
        return None
    return roi if vertical else roi.T


def _width(roi: np.ndarray) -> float | None:
    """The ROI edge's 10-90 % width (px), or None when it isn't trustworthy in this ROI."""
    try:
        wpx = sfr.edge_width_10_90(roi)
    except (ValueError, IndexError):
        return None
    return wpx if 0 < wpx <= MAX_WIDTH_SHARE * roi.shape[1] else None


def _px_per_mm_across(proj: Projector, a_mm: np.ndarray, b_mm: np.ndarray) -> float:
    """Raw-image px per board mm perpendicular to the board-mm segment a-b, at its midpoint."""
    m = (a_mm + b_mm) / 2.0
    d = (b_mm - a_mm) / np.linalg.norm(b_mm - a_mm)
    n = np.array([-d[1], d[0]])
    p = proj(np.array([m, m + n]))
    return float(np.linalg.norm(p[1] - p[0]))


def _edges(poly: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    return [(poly[k], poly[(k + 1) % len(poly)]) for k in range(len(poly))]


def slanted_edge(gray: np.ndarray, proj: Projector, lay: Layout) -> dict:
    sq_mm = lay.square_corners_mm()
    sq_px = proj(sq_mm)
    side = float(lay.slanted_edge["side_mm"])
    hw_mm = min(side / 4.0, float(lay.slanted_edge["grey_margin_mm"]))
    cpp, lpmm, widths = [], [], []
    for (a_mm, b_mm), (a, b) in zip(_edges(sq_mm), _edges(sq_px)):
        ppm = _px_per_mm_across(proj, a_mm, b_mm)
        roi = _edge_roi(gray, a, b, hw_mm * ppm)
        if roi is None:
            continue
        try:
            c = sfr.mtf50(roi)
            cpp.append(c)
            lpmm.append(sfr.lp_per_mm(c, ppm))
        except ValueError:
            pass
        widths.append(_width(roi))
    return {"mtf50_cpp": _med(cpp), "mtf50_lpmm": _med(lpmm), "edge_w_px": _med(widths),
            "slanted_edges": len(cpp)}


def tag_edge_widths(gray: np.ndarray, tags_raw: dict[int, np.ndarray], lay: Layout,
                    proj: Projector) -> float | None:
    hw_mm = 0.9 * lay.tag_edge_mm / TAG_CELLS
    widths = []
    for i, corners in tags_raw.items():
        if i not in lay.tags_mm:
            continue
        for (a_mm, b_mm), (a, b) in zip(_edges(lay.tags_mm[i]), _edges(corners)):
            roi = _edge_roi(gray, a, b, hw_mm * _px_per_mm_across(proj, a_mm, b_mm))
            if roi is not None:
                widths.append(_width(roi))
    return _med(widths)


def patch_de00(bgr: np.ndarray, proj: Projector, lay: Layout) -> dict[str, float]:
    """CIEDE2000 per card patch: the camera's own sRGB8 output vs the card truth (uncorrected)."""
    h, w = bgr.shape[:2]
    out = {}
    for p in lay.card.patches:
        quad = proj(lay.patch_quad_mm(p.id))
        if not _inside(quad, w, h):
            continue
        mask = np.zeros((h, w), np.uint8)
        cv2.fillConvexPoly(mask, np.round(quad).astype(np.int32), 1)
        if int(mask.sum()) < MIN_PATCH_PX:
            continue
        b, g, r = (float(v) for v in cv2.mean(bgr, mask=mask)[:3])
        lab = metrics.linear_to_lab(metrics.srgb8_to_linear([r, g, b]))
        ref = metrics.linear_to_lab(metrics.srgb8_to_linear(p.truth))
        out[p.id] = round(float(metrics.delta_e2000(ref, lab)), 2)
    return out


def measure_frame(img: np.ndarray, lens: pose.Lens, lay: Layout, colour: bool = True) -> dict:
    """All per-frame numbers for one camera frame (BGR, or gray when ``colour`` is False)."""
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    tags = {i: c for i, c in pose.detect_tags(gray).items() if i in lay.tags_mm}
    rec = {"tags": len(tags), "tag_ids": sorted(tags), "range_m": None, "range_raw_m": None,
           "pos_m": None, "reproj_px": None, "board_whole": False, "samples": {},
           "mtf50_cpp": None, "mtf50_lpmm": None, "edge_w_px": None, "tag_edge_w_px": None,
           "de00": {}}
    if not tags:
        return rec
    ids = tuple(lay.tags_mm)
    tags_u = pose.undistort_corners(tags, lens)
    rng = pose.board_range(tags_u, ids, lay.tag_edge_mm, lens.K)
    raw = pose.board_range(tags, ids, lay.tag_edge_mm, lens.K)
    rec["range_m"] = round(rng["range_m"], 4) if rng["range_m"] else None
    rec["range_raw_m"] = round(raw["range_m"], 4) if raw["range_m"] else None
    rec["tag_depths_m"] = [round(d, 4) for d in rng["tag_depths_m"]]
    bp = board_pose(tags_u, lay, lens.K)
    if bp is None:
        return rec
    rvec, tvec = bp
    rec["pos_m"] = board_position(rvec, tvec, lay)
    proj = projector(rvec, tvec, lens)
    det = np.concatenate([tags[i] for i in sorted(tags)])
    back = proj(np.concatenate([lay.tags_mm[i] for i in sorted(tags)]))
    rec["reproj_px"] = round(float(np.sqrt(np.mean(np.sum((det - back) ** 2, axis=1)))), 3)
    if len(tags) < MIN_TAGS_H:
        return rec
    rec["rvec"] = [round(float(v), 6) for v in rvec.ravel()]
    rec["tvec_mm"] = [round(float(v), 3) for v in tvec.ravel()]
    outline = proj(_densify(lay.outline_corners_mm(), OUTLINE_STEPS))
    rec["board_whole"] = _inside(outline, w, h)
    (bx1, by1), (bx2, by2) = np.clip(outline.min(axis=0), 0, [w, h]), \
        np.clip(outline.max(axis=0), 0, [w, h])
    rec["board_box"] = [round(float(v), 1) for v in (bx1, by1, bx2, by2)]
    for name, (centre, diam) in lay.samples.items():
        s = sample_box(proj, centre, diam)
        rec["samples"][name] = {"px": round(s["px_across"], 2),
                                "px_max": round(s["px_across_max"], 2),
                                "box": [round(v, 1) for v in s["box"]]}
    rec.update(slanted_edge(gray, proj, lay))
    rec["tag_edge_w_px"] = tag_edge_widths(gray, tags, lay, proj)
    if colour and img.ndim == 3:
        rec["de00"] = patch_de00(img, proj, lay)
    return rec


def _med(v):
    v = [x for x in v if x is not None]
    return round(statistics.median(v), 4) if v else None
