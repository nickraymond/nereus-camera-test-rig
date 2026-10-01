"""Stage ``calibrate`` — in-air colour calibration per camera x illuminant (Phase 8 S4).

Input: a session config (``configs/calibration/sessions/<session>.yaml``) and a ``--data``
root with one folder of locked RAW captures per camera x illuminant, written by
``scripts/capture_raw_imx708.py`` / ``capture_raw_openmv.py`` with ``--stops … --repeat …``
(``stop_<s>_r<n>.dng|.bayer``). Every frame is read from the RAW (SPEC §20 RAW first).

1. **Sample** each frame: card located by its tags, chart found in the card plane
   (``color.chart``), binned-linear means of every patch (central 60 %), clip fractions.
2. **Truth** (``reference`` in the session): the reference camera's frames under the reference
   illuminant, through its DNG ColorMatrix1 (XYZ → camera), Bradford-adapted from the anchor
   grey to D65, linear sRGB, scaled so the anchor keeps its design luminance; median over
   frames; unclipped values (may be negative outside sRGB). Provisional: that matrix is the
   camera tuning's, not an instrument reading — and circular for the reference camera itself.
3. **Fit** per camera x illuminant: each frame white-balanced + exposure-matched on the anchor
   (anchor → neutral at its truth luminance), then a 3x3 matrix with rows summing to 1
   (neutral stays neutral) by least squares on relative error, on the frames whose repeat is
   not in ``holdout_repeats``.
4. **Score** (ΔE2000, D65) on the held-out frames: WB only, the fitted matrix, the matrix
   fitted on the card only (scored on the chart) and on the chart only (scored on the card),
   the same camera's other-illuminant matrix, a camera's DNG matrix where it has one, and
   the other boards' matrices. Diagnostics: repeatability (anchor spread over repeats — lamp
   flicker) and linearity (anchor ratio per stop).

Output ``calibrate/{samples.json, truth.json, summary.json, calibration/<camera_id>.yaml,
stage.json}``. The calibration YAMLs are proposals: reviewed, then copied to
``configs/calibration/`` (the stage never writes configs).
"""

from __future__ import annotations

import json
import re
from functools import partial
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..config import load_yaml
from .card import load_card
from .chart import find_chart, load_chart
from .jpeg_geometry import JpegMap
from .locate import locate_frame, tag_geometry, tag_spec
from .metrics import SRGB_TO_XYZ, WHITE_XYZ, delta_e2000, linear_to_lab, luminance, srgb8_to_linear
from .patches import homography, mosaic_to_binned, sample
from .raw_io import bin2x2, normalize
from .stages import git_state, open_raw, run_parallel, write_stage

SERIES = re.compile(r"^stop_([+-]?\d+(?:\.\d+)?)_r(\d+)\.(dng|bayer)$")
CLIP_MAX = 0.01  # a patch with more clipped pixels than this in any channel is not used
MIN_LEVEL = 0.005  # brightest channel, fraction of full scale: below it a patch is noise
BRADFORD = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367],
                     [0.0389, -0.0685, 1.0296]])
XYZ_TO_SRGB = np.linalg.inv(SRGB_TO_XYZ)


def series_frames(folder: Path) -> list[tuple[Path, float, int]]:
    out = []
    for p in sorted(folder.iterdir()):
        m = SERIES.match(p.name)
        if m:
            out.append((p, float(m.group(1)), int(m.group(2))))
    if not out:
        raise ValueError(f"{folder}: no stop_<s>_r<n>.dng|.bayer frames")
    return out


def sample_frame(path: Path, card_path: Path, chart_path: Path, region: tuple) -> dict:
    """Card + chart patch means of one RAW frame (binned linear camera RGB, 0..1)."""
    card, chart = load_card(card_path), load_chart(chart_path)
    frame = open_raw(path)
    rec = locate_frame(path, None, card.corner_map, JpegMap.offset(0, 0),
                       raw_reader=lambda _p: frame, geometry=tag_geometry(card),
                       spec=tag_spec(card))
    out: dict[str, Any] = {"file": path.name, "tags_found": rec.get("tags_found"),
                           "exposure_s": frame.exposure_s, "iso": frame.iso,
                           "gain_db": frame.source.get("gain_db"),
                           "color_matrix": None if frame.color_matrix is None
                           else np.asarray(frame.color_matrix).tolist(),
                           "black_level": list(frame.black_level),
                           "white_level": frame.white_level, "cfa": frame.cfa,
                           "bits": frame.source.get("bits_per_sample")}
    if not rec["located"]:
        return {**out, "error": f"card not located: {rec.get('reason')}"}
    linear, saturated, cfa = normalize(frame)
    binned, clip = bin2x2(linear, cfa, saturated)
    H = mosaic_to_binned(frame.valid_crop) @ homography(card, np.asarray(rec["quad_raw"]))
    boxes = {p.id: p.box for p in card.patches}
    white = sample(binned, H, card.patch("gray_white").box)["mean"]
    try:
        found = find_chart(binned, H, white, chart, tuple(region))
        boxes.update(found.pop("boxes"))
        out["chart"] = found
    except ValueError as exc:
        out["chart"] = {"error": str(exc)}
    out["patches"] = {}
    for pid, box in boxes.items():
        st = sample(binned, H, box, clip=clip)
        if st.get("mean"):
            out["patches"][pid] = {k: st.get(k) for k in ("mean", "std", "n_px", "clip_frac")}
    return out


def bradford(src_xyz, dst_xyz) -> np.ndarray:
    s, d = BRADFORD @ np.asarray(src_xyz), BRADFORD @ np.asarray(dst_xyz)
    return np.linalg.inv(BRADFORD) @ np.diag(d / s) @ BRADFORD


def dng_to_linear(cam: np.ndarray, color_matrix, neutral) -> np.ndarray:
    """Camera RGB → linear sRGB (D65) through a DNG ColorMatrix (XYZ → camera), adapted so
    ``neutral`` (camera RGB) becomes D65 white with Y = 1."""
    inv = np.linalg.inv(np.asarray(color_matrix, dtype=np.float64))
    A = bradford(inv @ np.asarray(neutral, dtype=np.float64), WHITE_XYZ)
    return np.asarray(cam, dtype=np.float64) @ (XYZ_TO_SRGB @ A @ inv).T


def usable(p: Optional[dict]) -> bool:
    return bool(p and p.get("mean") and max(p.get("clip_frac") or [0]) <= CLIP_MAX
                and max(p["mean"]) >= MIN_LEVEL)


def anchor_y(card, anchor: str) -> float:
    patch = card.patch(anchor)
    return float(luminance(srgb8_to_linear(np.asarray(patch.design or patch.truth, float))))


def white_balanced(frame: dict, anchor: str, y: float) -> dict[str, np.ndarray]:
    """{patch: camera RGB with the anchor at (y, y, y)} — empty if the anchor is unusable."""
    a = frame["patches"].get(anchor)
    if not usable(a):
        return {}
    gain = y / np.asarray(a["mean"], dtype=np.float64)
    return {pid: np.asarray(p["mean"]) * gain for pid, p in frame["patches"].items()
            if usable(p)}


def dng_read(frame: dict, anchor: str, y: float) -> dict[str, np.ndarray]:
    """{patch: linear sRGB} through the frame's own DNG matrix, on the RAW means (the matrix
    maps XYZ to raw camera RGB), adapted on the anchor and scaled to its luminance ``y``."""
    a = frame["patches"].get(anchor)
    if not usable(a) or frame.get("color_matrix") is None:
        return {}
    return {pid: y * dng_to_linear(p["mean"], frame["color_matrix"], a["mean"])
            for pid, p in frame["patches"].items() if usable(p)}


def reference_truth(frames: list[dict], anchor: str, y: float) -> dict[str, Any]:
    reads: dict[str, list] = {}
    for f in frames:
        for pid, lin in dng_read(f, anchor, y).items():
            reads.setdefault(pid, []).append(lin)
    values = {pid: np.median(r, axis=0) for pid, r in reads.items()}
    spread = {pid: float(max(delta_e2000(linear_to_lab(a), linear_to_lab(b))
                             for a in r for b in r)) for pid, r in reads.items()}
    return {"values": values, "n_frames": {k: len(v) for k, v in reads.items()},
            "spread_de2000": spread}


def fit_ccm(cam: np.ndarray, target: np.ndarray) -> np.ndarray:
    """3x3 matrix, rows summing to 1, minimizing error relative to the target's luminance."""
    w = 1 / np.maximum(luminance(target), 0.02)
    X = np.stack([cam[:, 1] - cam[:, 0], cam[:, 2] - cam[:, 0]], axis=1) * w[:, None]
    M = np.zeros((3, 3))
    for i in range(3):
        (a, b), *_ = np.linalg.lstsq(X, (target[:, i] - cam[:, 0]) * w, rcond=None)
        M[i] = [1 - a - b, a, b]
    return M


def _pairs(frames: list[dict], truth: dict, anchor: str, y: float, ids) -> tuple:
    cam, tgt, keys = [], [], []
    for f in frames:
        wb = white_balanced(f, anchor, y)
        for pid in ids:
            if pid in wb and pid in truth and pid != anchor:
                cam.append(wb[pid])
                tgt.append(truth[pid])
                keys.append((f["file"], pid))
    return np.array(cam).reshape(-1, 3), np.array(tgt).reshape(-1, 3), keys


def de_stats(pred: np.ndarray, target: np.ndarray) -> dict[str, Any]:
    if not len(pred):
        return {"n": 0}
    de = delta_e2000(linear_to_lab(target), linear_to_lab(pred))
    return {"n": int(len(de)), "median": round(float(np.median(de)), 2),
            "p90": round(float(np.percentile(de, 90)), 2), "max": round(float(de.max()), 2)}


def _diagnostics(frames: list[dict], anchor: str) -> dict[str, Any]:
    by_stop: dict[float, list] = {}
    for f in frames:
        a = f["patches"].get(anchor)
        if usable(a):
            by_stop.setdefault(f["stop"], []).append(float(a["mean"][1]) / f["exposure_s"])
    levels = {s: float(np.mean(v)) for s, v in sorted(by_stop.items())}
    stops = sorted(levels)
    return {"repeat_spread_pct": {f"{s:+g}": round(100 * (max(v) - min(v)) / np.mean(v), 2)
                                  for s, v in sorted(by_stop.items())},
            "linearity_per_exposure": {f"{a:+g}->{b:+g}": round(levels[b] / levels[a], 4)
                                       for a, b in zip(stops, stops[1:])}}


def calibrate(session_path: Path, data_root: Path, out_root: Path,
              workers: int = 4) -> dict[str, Any]:
    cfg = load_yaml(session_path)
    card_path, chart_path = Path(cfg["card"]), Path(cfg["chart"]["config"])
    card, chart = load_card(card_path), load_chart(chart_path)
    anchor, holdout = cfg["wb_anchor"], set(cfg["holdout_repeats"])
    y = anchor_y(card, anchor)
    out_dir = Path(out_root) / cfg["session"] / "calibrate"
    jobs, where = [], []
    for cam, c in cfg["cameras"].items():
        for ill, folder in c["captures"].items():
            for path, stop, rep in series_frames(Path(data_root) / folder):
                jobs.append((path,))
                where.append((cam, ill, stop, rep))
    fn = partial(sample_frame, card_path=card_path, chart_path=chart_path,
                 region=cfg["chart"]["region"])
    samples: dict[str, dict[str, list]] = {}
    for (cam, ill, stop, rep), s in zip(where, run_parallel(fn, jobs, workers)):
        if "error" in s:
            raise ValueError(f"{cam}/{ill}/{s['file']}: {s['error']}")
        s.update(stop=stop, repeat=rep, heldout=rep in holdout)
        samples.setdefault(cam, {}).setdefault(ill, []).append(s)

    ref = cfg["reference"]
    tr = reference_truth(samples[ref["camera"]][ref["illuminant"]], anchor, y)
    truth = tr["values"]
    card_ids = [p.id for p in card.patches]
    chart_ids = [p.id for p in chart.patches]
    fits: dict[str, dict[str, np.ndarray]] = {}
    for cam, by_ill in samples.items():
        for ill, frames in by_ill.items():
            train = [f for f in frames if not f["heldout"]]
            fits.setdefault(cam, {})[ill] = fit_ccm(*_pairs(train, truth, anchor, y,
                                                            card_ids + chart_ids)[:2])
    results: dict[str, Any] = {}
    for cam, by_ill in samples.items():
        for ill, frames in by_ill.items():
            train = [f for f in frames if not f["heldout"]]
            test = [f for f in frames if f["heldout"]]
            c_all, t_all, _ = _pairs(test, truth, anchor, y, card_ids + chart_ids)
            c_tr, t_tr, _ = _pairs(train, truth, anchor, y, card_ids + chart_ids)
            c_card, t_card, _ = _pairs(test, truth, anchor, y, card_ids)
            c_chart, t_chart, _ = _pairs(test, truth, anchor, y, chart_ids)
            M = fits[cam][ill]
            m_card = fit_ccm(*_pairs(train, truth, anchor, y, card_ids)[:2])
            m_chart = fit_ccm(*_pairs(train, truth, anchor, y, chart_ids)[:2])
            r = {"n_frames": {"train": len(train), "heldout": len(test)},
                 "train": de_stats(c_tr @ M.T, t_tr),
                 "heldout": {"wb_only": de_stats(c_all, t_all),
                             "ccm": de_stats(c_all @ M.T, t_all),
                             "ccm_card": de_stats(c_card @ M.T, t_card),
                             "ccm_chart": de_stats(c_chart @ M.T, t_chart),
                             "card_fit_on_chart": de_stats(c_chart @ m_card.T, t_chart),
                             "chart_fit_on_card": de_stats(c_card @ m_chart.T, t_card)},
                 "diagnostics": _diagnostics(frames, anchor),
                 "chart_finder": [f.get("chart") for f in frames]}
            for other_ill, M2 in fits[cam].items():
                if other_ill != ill:
                    r["heldout"][f"ccm_{other_ill}"] = de_stats(c_all @ M2.T, t_all)
            for other, f2 in fits.items():
                if other != cam and ill in f2:
                    r["heldout"][f"ccm_of_{other}"] = de_stats(c_all @ f2[ill].T, t_all)
            if all(f.get("color_matrix") for f in test):  # the camera's own DNG matrix
                pred, tgt = [], []
                for f in test:
                    for pid, lin in dng_read(f, anchor, y).items():
                        if pid in truth and pid != anchor:
                            pred.append(lin)
                            tgt.append(truth[pid])
                r["heldout"]["dng_matrix"] = de_stats(np.array(pred), np.array(tgt))
            wb_gain = np.median([np.asarray(f["patches"][anchor]["mean"])[1]
                                 / np.asarray(f["patches"][anchor]["mean"]) for f in train], 0)
            r["wb_gains_rgb"] = [round(float(v), 4) for v in wb_gain]
            results.setdefault(cam, {})[ill] = r

    configured = {p.id: np.asarray(p.truth, float) for p in card.patches} \
        if card.truth_is_measured else {}
    vs_config = {pid: round(float(delta_e2000(linear_to_lab(srgb8_to_linear(v)),
                                              linear_to_lab(truth[pid]))), 2)
                 for pid, v in configured.items() if pid in truth}
    summary = {"session": cfg["session"], "anchor": anchor, "reference": ref,
               "holdout_repeats": sorted(holdout), "results": results,
               "truth_spread_de2000": {k: round(v, 2) for k, v in tr["spread_de2000"].items()},
               "truth_vs_card_measured_block_de2000": vs_config,
               "matrices": {c: {i: np.round(M, 4).tolist() for i, M in f.items()}
                            for c, f in fits.items()}}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "samples.json").write_text(json.dumps(samples, indent=1) + "\n")
    (out_dir / "truth.json").write_text(json.dumps(
        {"method": _truth_method(ref), "values_linear_srgb": {k: np.round(v, 6).tolist()
                                                              for k, v in truth.items()},
         "n_frames": tr["n_frames"]}, indent=1) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    cal_dir = out_dir / "calibration"
    cal_dir.mkdir(exist_ok=True)
    for cam, c in cfg["cameras"].items():
        (cal_dir / f"{c['camera_id']}.yaml").write_text(
            calibration_yaml(c["camera_id"], cfg, samples[cam], results[cam], fits[cam]))
    write_stage(out_dir, "calibrate", configs=[session_path, card_path, chart_path],
                params={"data_root": str(Path(data_root).resolve()), "anchor": anchor,
                        "clip_max": CLIP_MAX, "min_level": MIN_LEVEL})
    summary["out_dir"] = str(out_dir)
    return summary


def _truth_method(ref: dict) -> str:
    return (f"{ref['camera']} under {ref['illuminant']}: DNG ColorMatrix1 (XYZ->camera), "
            f"Bradford from the anchor grey to D65, linear sRGB, median over frames")


def calibration_yaml(camera_id: str, cfg: dict, samples: dict, results: dict,
                     fits: dict) -> str:
    f0 = next(iter(samples.values()))[0]
    lines = [f"# {camera_id} — in-air colour calibration (SPEC §4 Phase 8 S4), written by the",
             f"# `calibrate` stage from session {cfg['session']} "
             f"(git {git_state().get('sha', '?')[:10]}).",
             "# PROVISIONAL: the truth is the reference camera's DNG matrix reading "
             f"({_truth_method(cfg['reference'])}),",
             "# not an instrument measurement (OQ-27/OQ-40).", "",
             f"camera_id: {camera_id}", "provisional: true",
             f"raw: {{cfa: {f0['cfa']}, bits: {f0.get('bits') or 'null'}, "
             f"black_level: {f0['black_level']}, white_level: {f0['white_level']}}}",
             "aperture_f: null   # not recorded in the RAW; unverified (OQ-52)", "",
             "# Per illuminant: camera RGB white-balanced on the card's "
             f"{cfg['wb_anchor']} -> linear sRGB (D65).",
             "# Rows sum to 1 (a balanced neutral stays neutral). ΔE2000 residuals: 'train' on",
             "# the fit frames, 'heldout' on the held-out repeats; card_fit_on_chart and",
             "# chart_fit_on_card fit on one set of surfaces and score the other.",
             "color_matrices:"]
    for ill, M in fits.items():
        r, meta = results[ill], cfg["illuminants"].get(ill, {})
        h = r["heldout"]
        lines += [f"  {ill}:", f"    cct_k: {meta.get('cct_k') or 'null'}",
                  f"    wb_gains_rgb: {r['wb_gains_rgb']}   # multipliers, G = 1",
                  "    matrix:"]
        lines += [f"      - [{', '.join(f'{v:.4f}' for v in row)}]" for row in M]
        lines.append("    residual_de2000:")
        for key in ("train",):
            lines.append(f"      {key}: {json.dumps(r[key])}")
        for key in ("wb_only", "ccm", "card_fit_on_chart", "chart_fit_on_card"):
            lines.append(f"      heldout_{key}: {json.dumps(h[key])}")
    return "\n".join(lines) + "\n"
