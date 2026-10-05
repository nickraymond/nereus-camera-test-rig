"""Exposure-sweep scoring and best-frame pick on RAW frames — pool tool (Nick, 2026-10-05).

A sweep is N RAW frames of one camera at a ladder of shutter times, gain locked at the floor.
Per frame, on the RAW (never the camera JPEG):

* **level** — green-plane 99.5th percentile and the clipped fraction (any channel ≥ 98 % of
  full scale) in the region of interest; the card's mid-grey level when the card is found;
* **sharpness** — Laplacian energy of the green plane after 2×2 binning, minus its expected
  noise share (Immerkær noise estimate), divided by the squared mean (so it does not grow with
  brightness), in the card area when the card is found and in the frame centre (motion
  anywhere in view). Frames darker than 3 % of full scale are "too dark to judge";
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


def _immerkaer_sigma(p: np.ndarray) -> Optional[float]:
    """Fast noise sigma estimate (Immerkær 1996) on one plane."""
    if p.shape[0] < 8 or p.shape[1] < 8:
        return None
    k = (p[:-2, :-2] - 2 * p[:-2, 1:-1] + p[:-2, 2:] - 2 * p[1:-1, :-2] + 4 * p[1:-1, 1:-1]
         - 2 * p[1:-1, 2:] + p[2:, :-2] - 2 * p[2:, 1:-1] + p[2:, 2:])
    return float(np.sqrt(np.pi / 2) * np.mean(np.abs(k)) / 6)


def score_frame(frame, card_box: Optional[tuple[int, int, int, int]] = None) -> dict[str, Any]:
    """Scores for one RawFrame. Only two crops are normalised (the frame centre, 50 % x 50 %,
    and the card box ``(x0, y0, x1, y1)`` in mosaic px when known), which keeps a 12 MP IMX708
    frame inside the Pi Zero's memory."""
    raw, _, _ = frame.active()
    H, W = raw.shape
    boxes = {"centre": (W // 4, H // 4, W // 2, H // 2)}
    if card_box is not None:
        x0, y0, x1, y1 = card_box
        boxes["card"] = (x0, y0, x1 - x0, y1 - y0)
    out: dict[str, Any] = {"roi": "card" if "card" in boxes else "centre", "sharpness": {}}
    for name, box in boxes.items():
        binned, clip = _planes(_crop(frame, *box))
        g = binned[..., 1]
        out["sharpness"][name] = _laplacian_energy(g)
        if name == out["roi"]:
            out["level_p995"] = round(float(np.percentile(g, 99.5)), 4)
            out["clip_frac"] = round(float(np.mean(clip.any(axis=-1))), 5)
            out["clipped"] = out["clip_frac"] > CLIP_MAX_FRAC
        if name == "centre":
            r = binned[..., 0]
            sig = _immerkaer_sigma(r)
            out["red_snr_global"] = round(float(r.mean() / sig), 2) if sig else None
    return out


def pick(frames: list[dict[str, Any]], tolerance: float = DEFAULT_TOLERANCE) -> dict[str, Any]:
    """``frames``: [{"shutter_us", "scores": score_frame(...)}, ...] (failed frames omitted).
    Returns {"index", "shutter_us", "reason", "candidates"}."""
    key = "card" if all((f["scores"].get("sharpness") or {}).get("card") for f in frames) \
        else "centre"
    dark = [f["shutter_us"] for f in frames if (f["scores"].get("level_p995") or 0) < MIN_LEVEL]
    ok = [i for i, f in enumerate(frames) if not f["scores"].get("clipped")
          and f["shutter_us"] not in dark and (f["scores"].get("sharpness") or {}).get(key)]
    if not ok:
        return {"index": None, "shutter_us": None, "metric": key,
                "reason": "every frame is clipped, too dark to judge or has no sharpness score"
                          + (f" (too dark: {dark})" if dark else "")}
    best = max(frames[i]["scores"]["sharpness"][key] for i in ok)
    sharp_ok = [i for i in ok if frames[i]["scores"]["sharpness"][key] >= (1 - tolerance) * best]
    i = max(sharp_ok, key=lambda j: frames[j]["shutter_us"])
    rel = frames[i]["scores"]["sharpness"][key] / best
    clipped = [f["shutter_us"] for f in frames if f["scores"].get("clipped")]
    blurred = [frames[j]["shutter_us"] for j in ok if j not in sharp_ok]
    reason = (f"longest shutter ({frames[i]['shutter_us']} us) with {key} sharpness within "
              f"{tolerance:.0%} of the sharpest ({rel:.0%})"
              + (f"; longer but blurred: {blurred}" if blurred else "")
              + (f"; clipped: {clipped}" if clipped else "")
              + (f"; too dark to judge: {dark}" if dark else ""))
    return {"index": i, "shutter_us": frames[i]["shutter_us"], "metric": key,
            "tolerance": tolerance, "sharpness_rel": round(rel, 3), "reason": reason,
            "candidates": [frames[j]["shutter_us"] for j in sharp_ok]}


def score_sweep(paths: list[Path], shutters_us: list[int], card_yaml: Optional[Path] = None,
                tolerance: float = DEFAULT_TOLERANCE) -> dict[str, Any]:
    """Score every RAW of one camera's sweep (``.dng`` or ``.bayer`` + sidecar) and pick one.
    The card is located once (first frame where it is found) and reused: the camera is fixed
    for the few seconds a sweep takes."""
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
        for path, _ in sorted(zip(paths, shutters_us), key=lambda t: -t[1]):
            quad = _card_box(read(path), card)
            if quad is not None:
                card_note = f"card found on {Path(path).name}, box reused for every frame"
                break
    frames = []
    for path, shutter in zip(paths, shutters_us):
        frames.append({"file": Path(path).name, "shutter_us": int(shutter),
                       "scores": score_frame(read(path), quad)})
    result = {"frames": frames, "card": card_note}
    result["pick"] = pick(frames, tolerance)
    return result


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
