"""Stage ``measure-card`` — the printed card's real colours, read by the camera in air (SPEC §20).

The print is not its design file: the V2 card reads 30–40 C\\* less saturated than designed in
yellow / orange / red. Fitting or scoring against design values rewards corrections that
over-saturate (the v0.3 "yellow cast", Nick's blind review 2026-09-27). Rule (Nick): use
real-world measurements as the reference wherever they exist, and always show how far they
are from the design.

Method, per reference frame (the dataset config's ``card_reference_frames``, else its in-air
frames ``medium.air``): the binned-RAW patch means that qc kept and that are not clipped,
white-balanced on a **common anchor grey** (the first of the card's ``wb_anchors`` usable in
every reference frame), then the camera's daylight colour matrix (calibration) → linear sRGB;
the median over frames is then scaled so the **first** ``wb_anchors`` grey keeps its design
value — every correction sets white balance and exposure on it, so renderings keep their
brightness — and stored on the 8-bit sRGB scale (values above 255 are allowed). A
damaged patch falls back to its first usable sub-patch. Per patch: the median over frames.
Greys are stored neutral at their measured luminance (white balance defines them as neutral);
their residual tint is reported.

Caveat, recorded with the values: the reading goes through the camera's own daylight matrix
(unverified, OQ-39); an instrument measurement of the card is the better reference (OQ-40).

The stage never edits the card YAML: ``summary.json`` carries a ``config_block`` (the
``measured:`` block) to review and paste, and ``config_matches`` checks the one in the card.
Output: ``measure_card/{measured.json, summary.json, stage.json}``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..config import load_yaml
from .card import Card, load_card
from .metrics import delta_e2000, linear_to_lab, luminance, srgb8_to_linear
from .stages import verify_fresh, write_stage

MATCH_TOL = 1.0  # 8-bit counts: configured vs freshly measured


def linear_to_srgb8(lin) -> np.ndarray:
    """Linear sRGB → 8-bit sRGB scale, unrounded (values above 255 are kept)."""
    x = np.maximum(np.asarray(lin, dtype=np.float64), 0.0)
    return 255 * np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def _usable(q: dict, raw: dict, pid: str) -> bool:
    s = raw.get(pid) or {}
    return bool(q.get(pid, {}).get("usable") and s.get("mean")
                and max(s.get("clip_frac") or [0]) == 0)


def common_anchor(card: Card, frames: list[tuple[dict, dict]]) -> Optional[str]:
    """The first ``wb_anchors`` grey usable in every reference frame."""
    return next((a for a in card.roles.wb_anchors
                 if all(_usable(q, raw, a) for q, raw in frames)), None)


def design_linear(card: Card, pid: str) -> np.ndarray:
    patch = card.patch(card.parent_of(pid))
    return srgb8_to_linear(np.asarray(patch.design or patch.truth, dtype=np.float64))


def read_frame(card: Card, q: dict, raw: dict, anchor: str,
               matrix: np.ndarray) -> dict[str, np.ndarray]:
    """{patch: linear sRGB} of one reference frame, white-balanced on ``anchor``."""
    gain = design_linear(card, anchor) / np.asarray(raw[anchor]["mean"], dtype=np.float64)
    out = {}
    for p in card.patches:
        region = next((r for r in [p.id] + [s.id for s in card.sub_patches if s.parent == p.id]
                       if _usable(q, raw, r)), None)
        if region is not None:
            out[p.id] = np.maximum((np.asarray(raw[region]["mean"]) * gain) @ matrix.T, 0.0)
    return out


def _lch(lin) -> tuple[float, float, float]:
    L = linear_to_lab(np.maximum(lin, 1e-9))
    return float(L[0]), float(np.hypot(L[1], L[2])), float(np.degrees(np.arctan2(L[2], L[1])))


def measure(card: Card, frames: dict[str, tuple[dict, dict]],
            matrix: np.ndarray) -> dict[str, Any]:
    """Measured values (8-bit sRGB scale) + the design-vs-measured comparison per patch."""
    anchor = common_anchor(card, list(frames.values()))
    if anchor is None:
        raise ValueError(f"no wb_anchors grey {card.roles.wb_anchors} is usable in every "
                         f"reference frame {sorted(frames)}")
    reads = {s: read_frame(card, q, raw, anchor, matrix) for s, (q, raw) in frames.items()}
    got_by = {p.id: [r[p.id] for r in reads.values() if p.id in r] for p in card.patches}
    lins = {pid: np.median(g, axis=0) for pid, g in got_by.items() if g}
    # Brightness scale: the first wb_anchors grey keeps its design value (it is what every
    # correction sets white balance and exposure on); everything else keeps its measured
    # relation to it. The common anchor only links frames.
    scale_anchor = next((a for a in card.roles.wb_anchors if a in lins), anchor)
    k = luminance(design_linear(card, scale_anchor)) / luminance(lins[scale_anchor])
    values, table = {}, {}
    for p in card.patches:
        got = [g * k for g in got_by[p.id]]
        if not got:
            continue
        lin = lins[p.id] * k
        tint = _lch(lin)[1]
        if p.group == "grey":  # neutral by definition of white balance; tint reported
            lin = np.full(3, luminance(lin))
        values[p.id] = [round(float(v), 1) for v in linear_to_srgb8(lin)]
        d = design_linear(card, p.id)
        (Lm, Cm, hm), (Ld, Cd, hd) = _lch(lin), _lch(d)
        spread = max((float(delta_e2000(linear_to_lab(np.maximum(a, 1e-9)),
                                        linear_to_lab(np.maximum(b, 1e-9))))
                      for a in got for b in got), default=0.0)
        table[p.id] = {"n_frames": len(got), "design": list(p.design or p.truth),
                       "measured": values[p.id],
                       "de2000_vs_design": round(float(delta_e2000(
                           linear_to_lab(np.maximum(d, 1e-9)), linear_to_lab(lin))), 2),
                       "dL": round(Lm - Ld, 1), "dC": round(Cm - Cd, 1),
                       "dh_deg": round((hm - hd + 180) % 360 - 180, 1) if p.group != "grey"
                       else None,
                       "grey_tint_C": round(tint, 1) if p.group == "grey" else None,
                       "frame_spread_de2000": round(spread, 2)}
    return {"anchor": anchor, "scale_anchor": scale_anchor, "values": values, "patches": table}


def config_block(result: dict, source: str, frames: list[str], camera: str) -> str:
    vals = "".join(f"\n    {pid}: [{', '.join(f'{v:.1f}' for v in rgb)}]"
                   for pid, rgb in result["values"].items())
    return (f"measured:\n"
            f"  source: {source}\n"
            f"  method: in air, WB on {result['anchor']}, {camera} daylight colour matrix, "
            f"median over frames, scaled so {result['scale_anchor']} = its design value\n"
            f"  reference_frames: [{', '.join(frames)}]\n"
            f"  values:                # 8-bit sRGB scale; greys neutral{vals}\n")


def measure_card(qc_dir: Path, card_path: Path, calibration: Path,
                 dataset_config: Path) -> dict[str, Any]:
    verify_fresh(qc_dir)
    root = qc_dir.parent
    cfg = load_yaml(dataset_config)
    card = load_card(card_path)
    calib = load_yaml(calibration)
    matrix = np.asarray(calib["color_matrix"]["matrix"], dtype=np.float64)
    stems = [str(s) for s in (cfg.get("card_reference_frames")
                              or (cfg.get("medium") or {}).get("air") or [])]
    if not stems:
        raise ValueError(f"{dataset_config}: no card_reference_frames and no medium.air frames")
    qc = json.loads((qc_dir / "qc.json").read_text())
    patches = json.loads((root / "patches" / "patches.json").read_text())
    missing = [s for s in stems if s not in qc or "raw" not in patches.get(s, {})]
    if missing:
        raise ValueError(f"reference frames without qc / RAW patches: {missing}")
    frames = {s: (qc[s]["patches"], patches[s]["raw"]["patches"]) for s in stems}
    result = measure(card, frames, matrix)
    source = f"measured_{calib.get('camera_id', 'camera')}_in_air_{cfg.get('dataset', root.name)}"
    configured = {p.id: list(p.truth) for p in card.patches} if card.truth_is_measured else {}
    gap = max((max(abs(a - b) for a, b in zip(result["values"][k], configured[k]))
               for k in result["values"] if k in configured), default=None)
    summary = {"reference_frames": stems, "anchor": result["anchor"],
               "scale_anchor": result["scale_anchor"],
               "patches": result["patches"],
               "median_de2000_vs_design": {
                   g: round(float(np.median([v["de2000_vs_design"] for k, v in
                                             result["patches"].items()
                                             if card.patch(k).group == g])), 2)
                   for g in ("grey", "color")},
               "config_matches": gap is not None and gap < MATCH_TOL,
               "configured_vs_measured_max_counts": None if gap is None else round(gap, 2),
               "caveat": "read through the camera's daylight colour matrix (OQ-39); an "
                         "instrument measurement of the card is better (OQ-40)",
               "config_block": config_block(result, source, stems,
                                            calib.get("camera_id", "camera"))}
    out_dir = root / "measure_card"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "measured.json").write_text(json.dumps(result, indent=1) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_stage(out_dir, "measure_card", configs=[card_path, calibration, dataset_config],
                upstream=[qc_dir], params={"reference_frames": stems,
                                           "anchor": result["anchor"],
                                           "scale_anchor": result["scale_anchor"],
                                           "match_tol_counts": MATCH_TOL})
    summary["out_dir"] = str(out_dir)
    return summary
