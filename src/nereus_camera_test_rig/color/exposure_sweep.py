"""Exposure-sweep scoring and best-frame pick on RAW frames — pool tool (Nick, 2026-10-05).

A sweep is N RAW frames of one camera at a ladder of shutter times, gain locked at the floor.
Per frame, on the RAW (never the camera JPEG):

* **level** — green-plane 99.5th percentile and the clipped fraction (any channel ≥ 98 % of
  full scale) in the region of interest; the card's mid-grey level when the card is found;
* **sharpness** — edge acutance of the green plane after 2×2 binning: the strongest 1 % of
  gradient magnitudes over the region's contrast (``_edge_acutance``; exposure-invariant on
  static frames), in the card area when the card is found, else in the given ROI / the frame
  centre. Frames darker than 3 % of full scale are "too dark to judge";
* **red SNR** — mean / std of the red channel on the card's mid-grey patch when the card is
  found, else a global estimate on the frame centre (Immerkær's noise estimator).

**Pick:** among frames that are not clipped in the region of interest, keep those whose
sharpness is within ``tolerance`` (default 10 %) of the sharpest, and take the **longest shutter**
(most light, least red noise). The reason is written out. Every frame stays on disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

DEFAULT_CARD = Path(__file__).resolve().parents[3] / "configs" / "cards" / "nereus_v1.yaml"
CLIP_MAX_FRAC = 0.005  # more than 0.5 % clipped pixels in the region = clipped
MIN_LEVEL = 0.03       # region p99.5 below 3 % of full scale: too dark to judge sharpness
BRIGHT_PATCHES = ("gray_white", "gray_light")  # the card's brightest patches: clip test
SAT_MARGIN = 0.01      # a RAW site counts as clipped at >= white - 1 % of (white - black)
DEFAULT_TOLERANCE = 0.10


def _crop(frame, x0: int, y0: int, w: int, h: int):
    """A RawFrame of the mosaic region (active-area px, even origin so the CFA is unchanged)."""
    from .raw_io import RawFrame
    raw, cfa, black = frame.active()
    x0, y0 = max(int(x0) // 2 * 2, 0), max(int(y0) // 2 * 2, 0)
    w = min(int(w) // 2 * 2, raw.shape[1] - x0)
    h = min(int(h) // 2 * 2, raw.shape[0] - y0)
    return RawFrame(mosaic=np.ascontiguousarray(raw[y0:y0 + h, x0:x0 + w]), cfa=cfa,
                    black_level=tuple(float(v) for v in np.asarray(black).ravel()),
                    white_level=frame.white_level, exposure_s=frame.exposure_s,
                    source=dict(frame.source))


def _planes(frame):
    """Binned linear RGB (H/2, W/2, 3; 0 = black, 1 = white) and its per-channel clip mask."""
    from .raw_io import bin2x2, normalize
    linear, saturated, cfa = normalize(frame)
    return bin2x2(linear, cfa, saturated)


def _laplacian_energy(g: np.ndarray) -> Optional[float]:
    """Laplacian energy of the 2x2-binned plane minus its expected noise share, over the squared
    mean. A 4-neighbour Laplacian of white noise of std s has variance 20 s^2; s comes from
    Immerkær's estimator on the same binned plane, so dark, noisy frames no longer look sharp."""
    if g.shape[0] < 8 or g.shape[1] < 8:
        return None
    h, w = g.shape[0] // 2 * 2, g.shape[1] // 2 * 2
    b = g[:h, :w].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))   # 2x2 binning: less noise
    m = float(b.mean())
    if m <= 1e-6:
        return None
    lap = (b[1:-1, :-2] + b[1:-1, 2:] + b[:-2, 1:-1] + b[2:, 1:-1] - 4 * b[1:-1, 1:-1])
    sigma = _immerkaer_sigma(b) or 0.0
    return float(max(np.mean(lap ** 2) - 20 * sigma ** 2, 0.0) / (m * m))


def _edge_acutance(g: np.ndarray) -> Optional[float]:
    """Edge sharpness that does not depend on exposure: per direction, the mean of the strongest
    1 % of gradient magnitudes over the region's contrast (95th − 5th percentile), on the
    2x2-binned plane; the LOWER of the two directions is returned, so motion blur in any one
    direction counts. Measured on static frames (2026-10-05) it stays flat across a 16x shutter
    range where a brightness-normalised Laplacian fell by half (8-bit quantisation in dark
    frames). None when the region has no structure above the noise (contrast < 5 sigma)."""
    if g.shape[0] < 8 or g.shape[1] < 8:
        return None
    h, w = g.shape[0] // 2 * 2, g.shape[1] // 2 * 2
    b = g[:h, :w].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
    c = float(np.percentile(b, 95) - np.percentile(b, 5))
    sigma = _immerkaer_sigma(b) or 0.0
    if c <= 1e-6 or c < 5 * sigma:
        return None
    gy, gx = np.gradient(b)
    vals = []
    for d in (np.abs(gx), np.abs(gy)):
        vals.append(float(d[d >= np.percentile(d, 99)].mean() / c))
    return min(vals)


def _immerkaer_sigma(p: np.ndarray) -> Optional[float]:
    """Fast noise sigma estimate (Immerkær 1996) on one plane."""
    if p.shape[0] < 8 or p.shape[1] < 8:
        return None
    k = (p[:-2, :-2] - 2 * p[:-2, 1:-1] + p[:-2, 2:] - 2 * p[1:-1, :-2] + 4 * p[1:-1, 1:-1]
         - 2 * p[1:-1, 2:] + p[2:, :-2] - 2 * p[2:, 1:-1] + p[2:, 2:])
    return float(np.sqrt(np.pi / 2) * np.mean(np.abs(k)) / 6)


def score_frame(frame, card_box: Optional[tuple[int, int, int, int]] = None,
                roi: Optional[tuple[int, int, int, int]] = None,
                bright: Optional[dict[str, np.ndarray]] = None) -> dict[str, Any]:
    """Scores for one RawFrame. Only two crops are normalised, which keeps a 12 MP IMX708 frame
    inside the Pi Zero's memory: the card box ``(x0, y0, x1, y1)`` in mosaic px when known, and
    the fallback region — ``roi`` ``(x, y, w, h)`` when given (e.g. the MEDIUM crop, so light
    sources elsewhere in the frame stay out), else the frame centre (50 % x 50 %)."""
    raw, _, _ = frame.active()
    H, W = raw.shape
    fits = roi is not None and roi[0] >= 0 and roi[1] >= 0 and roi[0] + roi[2] <= W \
        and roi[1] + roi[3] <= H
    boxes = {"centre": tuple(roi) if fits else (W // 4, H // 4, W // 2, H // 2)}
    if card_box is not None:
        x0, y0, x1, y1 = card_box
        boxes["card"] = (x0, y0, x1 - x0, y1 - y0)
    out: dict[str, Any] = {"roi": "card" if "card" in boxes else "centre", "sharpness": {},
                           "fallback": ("given roi" if fits else "frame centre"
                                        + (" (roi outside this frame)" if roi else ""))}
    for name, box in boxes.items():
        binned, clip = _planes(_crop(frame, *box))
        g = binned[..., 1]
        out["sharpness"][name] = _edge_acutance(g)
        if name == out["roi"]:
            out["level_p995"] = round(float(np.percentile(g, 99.5)), 4)
            out["clip_frac"] = round(float(np.mean(clip.any(axis=-1))), 5)
            out["clipped"] = out["clip_frac"] > CLIP_MAX_FRAC
        if name == "centre":
            r = binned[..., 0]
            sig = _immerkaer_sigma(r)
            out["red_snr_global"] = round(float(r.mean() / sig), 2) if sig else None
    if bright:  # the card's brightest patches, per channel on the RAW (whatever the ROI shows)
        out["bright_patch_clip"] = {pid: _patch_clip(frame, q) for pid, q in bright.items()}
        out["white_clipped"] = any(v["max"] > CLIP_PATCH_FRAC
                                   for v in out["bright_patch_clip"].values())
    return out


CLIP_PATCH_FRAC = 0.01  # > 1 % of a bright patch's sites clipped in any channel = clipped


def _patch_clip(frame, quad: np.ndarray, margin: float = SAT_MARGIN) -> dict[str, float]:
    """Fraction of RAW sites at saturation (>= white - margin x range) inside ``quad`` (active-
    area mosaic px), per colour channel."""
    import cv2
    raw, cfa, black = frame.active()
    q = np.asarray(quad, float)
    x0 = max(int(np.floor(q[:, 0].min())) // 2 * 2, 0)
    y0 = max(int(np.floor(q[:, 1].min())) // 2 * 2, 0)
    x1 = min(int(np.ceil(q[:, 0].max())) + 2, raw.shape[1])
    y1 = min(int(np.ceil(q[:, 1].max())) + 2, raw.shape[0])
    if x1 <= x0 or y1 <= y0:
        return {"R": 0.0, "G": 0.0, "B": 0.0, "max": 0.0, "n": 0}
    sub = raw[y0:y1, x0:x1].astype(np.float32)
    mask = np.zeros(sub.shape, np.uint8)
    cv2.fillPoly(mask, [np.round(q - [x0, y0]).astype(np.int32)], 1)
    mask = mask.astype(bool)
    bl = np.asarray(black, float).ravel()  # 2x2 grid, same order as cfa
    out: dict[str, list] = {"R": [], "G": [], "B": []}
    for i, ch in enumerate(cfa):
        r, c = i // 2, i % 2
        thr = frame.white_level - margin * (frame.white_level - bl[i])
        m = mask[r::2, c::2]
        if m.any():
            out[ch].append(float((sub[r::2, c::2][m] >= thr).mean()))
    res = {k: round(max(v), 4) if v else 0.0 for k, v in out.items()}
    res["max"] = max(res.values())
    res["n"] = int(mask.sum())
    return res


def pick(frames: list[dict[str, Any]], tolerance: float = DEFAULT_TOLERANCE) -> dict[str, Any]:
    """``frames``: [{"shutter_us", "scores": score_frame(...)}, ...] (failed frames omitted).
    Returns {"index", "shutter_us", "reason", "candidates"}."""
    key = "card" if all((f["scores"].get("sharpness") or {}).get("card") for f in frames) \
        else "centre"
    dark = [f["shutter_us"] for f in frames if (f["scores"].get("level_p995") or 0) < MIN_LEVEL]
    white = [f["shutter_us"] for f in frames if f["scores"].get("white_clipped")]
    ok = [i for i, f in enumerate(frames) if not f["scores"].get("clipped")
          and not f["scores"].get("white_clipped")
          and f["shutter_us"] not in dark and (f["scores"].get("sharpness") or {}).get(key)]
    if not ok:
        return {"index": None, "shutter_us": None, "metric": key,
                "reason": "every frame is clipped, too dark to judge or has no sharpness score"
                          + (f" (too dark: {dark})" if dark else "")
                          + (f" (card white patch clipped: {white})" if white else "")}
    best = max(frames[i]["scores"]["sharpness"][key] for i in ok)
    sharp_ok = [i for i in ok if frames[i]["scores"]["sharpness"][key] >= (1 - tolerance) * best]
    i = max(sharp_ok, key=lambda j: frames[j]["shutter_us"])
    rel = frames[i]["scores"]["sharpness"][key] / best
    clipped = [f["shutter_us"] for f in frames if f["scores"].get("clipped")]
    blurred = [frames[j]["shutter_us"] for j in ok if j not in sharp_ok]
    reason = (f"longest shutter ({frames[i]['shutter_us']} us) with {key} sharpness within "
              f"{tolerance:.0%} of the sharpest ({rel:.0%})"
              + (f"; longer but blurred: {blurred}" if blurred else "")
              + (f"; card white/light grey clipped: {white}" if white else "")
              + (f"; scored region clipped: {clipped}" if clipped else "")
              + (f"; too dark to judge: {dark}" if dark else ""))
    return {"index": i, "shutter_us": frames[i]["shutter_us"], "metric": key,
            "tolerance": tolerance, "sharpness_rel": round(rel, 3), "reason": reason,
            "candidates": [frames[j]["shutter_us"] for j in sharp_ok]}


def score_sweep(paths: list[Path], shutters_us: list[int], card_yaml: Optional[Path] = None,
                tolerance: float = DEFAULT_TOLERANCE,
                roi: Optional[tuple[int, int, int, int]] = None) -> dict[str, Any]:
    """Score every RAW of one camera's sweep (``.dng`` or ``.bayer`` + sidecar) and pick one.
    The card is located once (first frame where it is found) and reused: the camera is fixed
    for the few seconds a sweep takes. When it is located, a frame whose white or light-grey
    patch clips in any RAW channel is ineligible, whatever the region shows."""
    from .raw_io import read_dng, read_openmv_bayer

    def read(p):
        return read_dng(p) if Path(p).suffix == ".dng" else read_openmv_bayer(p)

    quad, card_note = None, "no card YAML"
    if card_yaml:
        from .card import load_card
        card = load_card(card_yaml)
        card_note = "card not found on any frame"
        # longest shutter first: the brightest frame finds the card most easily; the camera is
        # fixed for the seconds a sweep takes, so one box serves every frame
        geo = None
        for path, _ in sorted(zip(paths, shutters_us), key=lambda t: -t[1]):
            geo = _card_geometry(read(path), card, roi)
            if geo is not None:
                quad = geo["box"]
                card_note = (f"card found on {Path(path).name} ({geo['where']}), box and bright "
                             "patches reused for every frame")
                break
    bright = geo["bright"] if card_yaml and geo else None
    frames = []
    for path, shutter in zip(paths, shutters_us):
        frames.append({"file": Path(path).name, "shutter_us": int(shutter),
                       "scores": score_frame(read(path), quad, roi, bright)})
    result = {"frames": frames, "card": card_note,
              "fallback_region": list(roi) if roi else "frame centre 50 %",
              "clip_test": ("card white + light grey patches (per RAW channel) + region"
                            if bright else "region only (card not located)")}
    result["pick"] = pick(frames, tolerance)
    return result


def _card_geometry(frame, card, roi=None) -> Optional[dict[str, Any]]:
    """Locate the card (decimated whole frame first, then the ROI crop at up to 4x the detail —
    at ~1 m the IMX708's tags are too small after decimating 12 MP) and return its padded
    tag box and the brightest patches' quads, all in active-area mosaic px; None if not found."""
    import cv2

    from .patches import INNER, homography
    from .raw_meter import card_levels, decimate_cells
    attempts = [("full frame", frame, 0, 0)]
    if roi is not None:
        raw = frame.active()[0]
        x0, y0 = max(int(roi[0]) // 2 * 2, 0), max(int(roi[1]) // 2 * 2, 0)
        if x0 + roi[2] <= raw.shape[1] and y0 + roi[3] <= raw.shape[0]:
            attempts.append(("ROI crop", _crop(frame, *roi), x0, y0))
    for where, fr, ox, oy in attempts:
        try:
            lv = card_levels(fr, card)
        except ValueError:
            continue
        k = decimate_cells(fr).source.get("decimated_cells", 1)
        q = np.asarray(lv["quad_raw"], float) * k + [ox, oy]
        pad = 0.15 * (q[:, 0].max() - q[:, 0].min())
        H = homography(card, q)
        bright = {}
        for pid in BRIGHT_PATCHES:
            b = card.patch(pid).box
            cx, cy = b.x + b.w / 2, b.y + b.h / 2
            hw, hh = b.w * INNER / 2, b.h * INNER / 2
            c = np.array([[[cx - hw, cy - hh], [cx + hw, cy - hh], [cx + hw, cy + hh],
                           [cx - hw, cy + hh]]], np.float64)
            bright[pid] = cv2.perspectiveTransform(c, H)[0]
        return {"where": where, "tags": lv["tags_found"],
                "box": (int(q[:, 0].min() - pad), int(q[:, 1].min() - pad),
                        int(q[:, 0].max() + pad), int(q[:, 1].max() + pad)),
                "bright": bright}
    return None


def _card_box(frame, card) -> Optional[tuple[int, int, int, int]]:
    """The card's tag-centre box, padded by a quarter tag pitch, in mosaic px; None if the
    card is not found (``raw_meter.card_levels`` works on a decimated copy)."""
    from .raw_meter import card_levels, decimate_cells
    try:
        lv = card_levels(frame, card)
    except ValueError:
        return None
    k = decimate_cells(frame).source.get("decimated_cells", 1)
    q = np.asarray(lv["quad_raw"], float) * k   # decimated mosaic px -> full mosaic px
    pad = 0.15 * (q[:, 0].max() - q[:, 0].min())
    return (int(q[:, 0].min() - pad), int(q[:, 1].min() - pad), int(q[:, 0].max() + pad),
            int(q[:, 1].max() + pad))
