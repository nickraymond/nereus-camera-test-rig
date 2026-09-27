"""Stage ``correct`` — apply every method to every usable frame (SPEC §4 Phase 8 S2a, v0.1).

Each RAW method is one per-channel affine map in exposure-normalized linear camera RGB, then
the camera colour matrix (L1), clip, sRGB encode (L3)::

    out = encode(clip(M · ((I − haze) · gain)))

| method | column name | haze | gain | card |
|---|---|---|---|---|
| ``raw_card_wb`` | RAW + card WB | 0 | anchor grey → its truth | yes |
| ``raw_card_wb_haze`` | RAW + card WB − haze | from the black patch, capped at the image's darkest pixels | anchor grey (after haze) → truth | yes |
| ``raw_depth_wb_haze`` | RAW + depth WB − haze (no card) | the image's darkest pixels | light colour predicted from depth; brightness from the image | **no** |

JPEG methods work on the camera's 8-bit output: ``camera_jpeg`` (as-shot, or the Olympus
underwater preset on ``2_underwater_preset`` frames) and ``jpeg_card_wb`` (the decoded JPEG
white-balanced on the same anchor grey — a channel the camera clipped to 0 stays 0).

- Anchor grey: grey 128, else its right half, else grey 74 (whichever qc kept).
- Haze from the black patch: ``(I_black − r·I_anchor) / (1 − r)``, ``r`` = the card's black /
  anchor reflectance (``card_reference`` in the calibration file); capped per channel at the
  image's ``DARK_PERCENTILE`` (it can never exceed what the darkest pixels contain).
- Depth WB table: ``ln(R/G)``, ``ln(B/G)`` of the light vs depth, pooled over the stable dives
  (3, 4) **leaving the frame's own dive out**; brightness puts the image's 99th percentile of
  green at ``TARGET_P99``. Distance is not used (run A: colour barely changes over 0.5–3 m).

Patch scores apply the same map to the patch means and encode to 8-bit before scoring
(SPEC §20). L3 images: 2×2-binned RAW, JPEG q90, JSON sidecar with every parameter.
Output: ``correct/{scores.json, frames.json, images/<method>/<stem>.jpg|.json, summary.json,
stage.json}``.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np

from ..config import load_yaml
from .card import Card, load_card
from .metrics import score_srgb8, srgb8_to_linear
from .raw_io import RawFrame, bin2x2, normalize
from .stages import run_parallel, verify_fresh, write_stage
from .water_model import ANCHORS, haze_from_black, grey_reflectance

TABLE_SOURCE_DIVES = ("3", "4")
DARK_PERCENTILE = 0.5
TARGET_P99 = 0.8
COLUMNS = {  # method → the name used in sheets and reports
    "camera_jpeg": "Camera JPEG",
    "jpeg_card_wb": "JPEG + card WB",
    "raw_card_wb": "RAW + card WB",
    "raw_card_wb_haze": "RAW + card WB − haze",
    "raw_depth_wb_haze": "RAW + depth WB − haze (no card)",
}
CLASSES = {"card_anchored": ("jpeg_card_wb", "raw_card_wb", "raw_card_wb_haze"),
           "card_free": ("camera_jpeg", "raw_depth_wb_haze")}


def encode8(linear: np.ndarray) -> np.ndarray:
    x = np.clip(linear, 0.0, 1.0)
    x = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return np.round(x * 255)


def apply(I, haze, gain, matrix: np.ndarray) -> np.ndarray:
    return ((np.asarray(I, dtype=np.float64) - np.asarray(haze)) * np.asarray(gain)) @ matrix.T


def depth_table(points_csv: Path, held_out: str) -> Optional[dict]:
    """Pooled ln(R/G), ln(B/G) vs depth over the stable dives except ``held_out``."""
    pts = [p for p in csv.DictReader(points_csv.open())
           if p["dive"] in TABLE_SOURCE_DIVES and p["dive"] != held_out]
    if len(pts) < 3:
        return None
    d = np.array([float(p["depth_m"]) for p in pts])
    X = np.c_[np.ones_like(d), d]
    out = {"source_dives": sorted({p["dive"] for p in pts}), "n": len(pts)}
    for key in ("ln_rg", "ln_bg"):
        coef, *_ = np.linalg.lstsq(X, np.array([float(p[key]) for p in pts]), rcond=None)
        out[key] = [float(coef[0]), float(coef[1])]
    return out


def _truth_linear(card: Card, pid: str) -> float:
    parent = next((s.parent for s in card.sub_patches if s.id == pid), pid)
    return float(srgb8_to_linear(card.patch(parent).truth[0]))


def frame_maps(job: dict, image: np.ndarray) -> dict[str, tuple]:
    """{method: (haze, gain)} for this frame, using the image for its dark-pixel floor."""
    dark = np.percentile(image.reshape(-1, 3), DARK_PERCENTILE, axis=0)
    maps: dict[str, tuple] = {}
    anchor, raw = job.get("anchor"), job.get("raw_means") or {}
    if anchor:
        t = job["anchor_truth"]
        a = np.asarray(raw[anchor])
        maps["raw_card_wb"] = ([0.0] * 3, (t / np.maximum(a, 1e-9)).tolist())
        haze = haze_from_black(job["raw_stats"], anchor, job["rho_anchor"],
                               job["black_to_white"]) if job.get("black_usable") else None
        haze = dark if haze is None else np.minimum(np.maximum(haze, 0), dark)
        maps["raw_card_wb_haze"] = (np.asarray(haze).tolist(),
                                    (t / np.maximum(a - haze, 1e-9)).tolist())
    table = job.get("table")
    if table:
        d = job["depth_m"]
        colour = np.array([np.exp(table["ln_rg"][0] + table["ln_rg"][1] * d), 1.0,
                           np.exp(table["ln_bg"][0] + table["ln_bg"][1] * d)])
        green = (image[..., 1] - dark[1]).ravel()
        scale = TARGET_P99 / max(float(np.percentile(green, 99)), 1e-9)
        maps["raw_depth_wb_haze"] = (dark.tolist(), (scale / colour).tolist())
    return maps


def score(job: dict, maps: dict, card: Card, matrix: np.ndarray) -> dict[str, Any]:
    anchor, excluded = job["anchor"], job["excluded"]
    held = ((anchor,) + (("gray_mid",) if anchor == "gray_mid_right" else ())) if anchor else ()
    lm = anchor or "gray_mid"
    out: dict[str, Any] = {}
    for method, (haze, gain) in maps.items():
        means = {pid: encode8(apply(v, haze, gain, matrix)).tolist()
                 for pid, v in job["raw_means"].items()}
        neutral = held if method in CLASSES["card_anchored"] else ()
        out[method] = score_srgb8(means, card, neutralized=neutral, anchor=lm, exclude=excluded)
    jm = job.get("jpeg_means")
    if jm:
        out["camera_jpeg"] = score_srgb8(jm, card, anchor=lm, exclude=excluded)
        if anchor:
            lin = {k: srgb8_to_linear(v) for k, v in jm.items()}
            gain = job["anchor_truth"] / np.maximum(lin[anchor], 1e-9)
            out["jpeg_card_wb"] = score_srgb8({k: encode8(v * gain).tolist()
                                               for k, v in lin.items()},
                                              card, neutralized=held, anchor=anchor,
                                              exclude=excluded)
    for m in out.values():
        m.pop("de2000", None)
    return out


class _Frame:
    """Picklable per-frame worker: read the RAW once, derive maps, score, render."""

    def __init__(self, card: Card, matrix: np.ndarray, out_dir: Path,
                 reader: Callable[[Path], RawFrame]):
        self.card, self.matrix, self.out_dir, self.reader = card, matrix, out_dir, reader

    def __call__(self, job: dict) -> dict[str, Any]:
        frame = self.reader(Path(job["raw_path"]))
        linear, sat, cfa = normalize(frame)
        image, _ = bin2x2(linear, cfa, sat)
        image = image / frame.exposure_factor()
        maps = frame_maps(job, image)
        result = {"maps": maps, "scores": score(job, maps, self.card, self.matrix)
                  if job.get("raw_means") else {}}
        for method, (haze, gain) in maps.items():
            img = encode8(apply(image, haze, gain, self.matrix)).astype(np.uint8)
            d = self.out_dir / method
            d.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(d / f"{job['stem']}.jpg"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                        [cv2.IMWRITE_JPEG_QUALITY, 90])
            (d / f"{job['stem']}.json").write_text(json.dumps(
                {"stem": job["stem"], "method": method, "column": COLUMNS[method],
                 "haze": haze, "gain": gain, "color_matrix": self.matrix.tolist(),
                 "depth_m": job["depth_m"], "anchor": job.get("anchor"),
                 "table": job.get("table"),
                 "pipeline": "exposure-normalized linear camera RGB → (I − haze) · gain → "
                             "colour matrix → clip → sRGB 8-bit"}, indent=1) + "\n")
        return result


def correct(fit_dir: Path, calibration: Path, dataset_config: Path, card_path: Path,
            raw_reader: Callable[[Path], RawFrame], workers: int | None = None) -> dict[str, Any]:
    verify_fresh(fit_dir)
    root = fit_dir.parent
    dataset_dir = Path(verify_fresh(root / "ingest")["params"]["dataset_dir"])
    rows = {r["stem"]: r for r in csv.DictReader((root / "ingest" / "manifest.csv").open())}
    qc = json.loads((root / "qc" / "qc.json").read_text())
    patches = json.loads((root / "patches" / "patches.json").read_text())
    dist = json.loads((root / "distance" / "distances.json").read_text())
    card = load_card(card_path)
    calib = load_yaml(calibration)
    matrix = np.asarray(calib["color_matrix"]["matrix"], dtype=np.float64)
    black_to_white = float(calib["card_reference"]["black_to_white"])
    cfg = load_yaml(dataset_config)
    no_card = set(cfg.get("no_card_categories", ["4_no_card"]))
    torch = set(cfg.get("torch_frames") or [])
    rho = grey_reflectance(card)
    tables = {d: depth_table(fit_dir / "wb_points.csv", d)
              for d in {r["dive_id"] for r in rows.values()}}

    jobs = []
    for stem, row in rows.items():
        if row["has_raw"] != "True" or stem in torch:
            continue
        job: dict[str, Any] = {"stem": stem, "raw_path": str(dataset_dir / row["file"]),
                               "depth_m": float(row["depth_m"]), "table": tables[row["dive_id"]]}
        if row["category"] not in no_card:
            q, d = qc.get(stem), dist.get(stem, {})
            if (q is None or not q["usable"] or "raw" not in patches.get(stem, {})
                    or d.get("medium") != "water"):
                continue
            keep = {pid for pid, p in q["patches"].items() if p["usable"]}
            anchor = next((a for a in ANCHORS if a in keep), None)
            raw_stats = patches[stem]["raw"]["patches"]
            job.update(
                anchor=anchor, excluded=[pid for pid in q["patches"] if pid not in keep],
                anchor_truth=_truth_linear(card, anchor) if anchor else None,
                rho_anchor=rho.get(anchor), black_to_white=black_to_white,
                black_usable="gray_black" in keep, raw_stats=raw_stats,
                raw_means={pid: s["mean_norm"] for pid, s in raw_stats.items()
                           if pid in keep and s.get("mean_norm")},
                jpeg_means={pid: s["mean"] for pid, s in
                            (patches[stem].get("jpeg") or {"patches": {}})["patches"].items()
                            if pid in keep and s.get("mean")})
        jobs.append(job)

    out_dir = root / "correct"
    results = run_parallel(_Frame(card, matrix, out_dir / "images", raw_reader),
                           [(j,) for j in jobs], workers or os.cpu_count() or 1)
    scores, frames = {}, {}
    for job, res in zip(jobs, results):
        frames[job["stem"]] = {"anchor": job.get("anchor"), "depth_m": job["depth_m"],
                               "table_source": (job["table"] or {}).get("source_dives"),
                               "maps": res["maps"]}
        if res["scores"]:
            scores[job["stem"]] = {"anchor": job.get("anchor"), "methods": res["scores"]}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scores.json").write_text(json.dumps(scores, indent=1) + "\n")
    (out_dir / "frames.json").write_text(json.dumps(frames, indent=1) + "\n")
    summary = {"scored_frames": len(scores), "rendered_frames": len(frames),
               "no_card_frames": len(frames) - len(scores), "columns": COLUMNS}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_stage(out_dir, "correct", configs=[calibration, dataset_config, card_path],
                upstream=[fit_dir], params={"version": "v0.1", "anchors": list(ANCHORS),
                                            "table_source_dives": list(TABLE_SOURCE_DIVES),
                                            "dark_percentile": DARK_PERCENTILE,
                                            "target_p99": TARGET_P99})
    summary["out_dir"] = str(out_dir)
    return summary
