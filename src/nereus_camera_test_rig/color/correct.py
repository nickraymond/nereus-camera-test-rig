"""Stage ``correct`` — apply every method to every usable frame (SPEC §4 Phase 8 S2a).

Each RAW method is one per-channel affine map in exposure-normalized linear camera RGB, then
the camera colour matrix (L1), clip, sRGB encode (L3)::

    out = encode(clip(M · ((I − offset) · gain)))

| method | offset | gain | uses the card |
|---|---|---|---|
| ``raw_card_wb`` (baseline) | 0 | anchor grey → its truth | yes |
| ``nereus_card`` | backscatter B(z) from the fit | anchor grey (after B) → truth | yes |
| ``nereus_table`` | B∞(1−e^(−βB z)) | 1 / (L(d) e^(−βD z)) | **no** |

JPEG methods work on the camera's 8-bit output: ``camera_jpeg`` (as-shot, or the Olympus
underwater preset for ``2_underwater_preset`` frames) and ``jpeg_card_wb`` (the decoded JPEG
white-balanced on the same anchor grey — a channel the camera clipped to 0 stays 0).

The anchor grey is grey 128, else its right half, else grey 74 (whichever qc kept). Table-mode
parameters are **leave-one-dive-out** from the dives with stable light (3 and 4; dives 1–2
are flagged): βD, βB and B∞ are medians over those dives' fits, L(d) is the pooled
``ln L = ln E(0) − K d`` regression. Table mode runs with the card's ``z`` (oracle) and with
the dataset's ``default_scene_z_m``.

Patch scores apply the map to the patch means (the map is per pixel, so this equals the mean
of the corrected pixels up to clipping) and encode to 8-bit before scoring (SPEC §20).
Also writes L3 images (2×2-binned RAW, JPEG q90) with a JSON sidecar of the parameters for
every usable card frame and every no-card frame (table mode).

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

ANCHORS = ("gray_mid", "gray_mid_right", "gray_dark")
TABLE_SOURCE_DIVES = ("3", "4")
RAW_METHODS = ("raw_card_wb", "nereus_card", "nereus_table", "nereus_table_default_z")
CLASSES = {"card_anchored": ("jpeg_card_wb", "raw_card_wb", "nereus_card"),
           "card_free": ("camera_jpeg", "nereus_table", "nereus_table_default_z")}


def encode8(linear: np.ndarray) -> np.ndarray:
    x = np.clip(linear, 0.0, 1.0)
    x = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return np.round(x * 255)


def table_params(params: dict, held_out: str) -> Optional[dict]:
    """βD, βB, B∞ and the L(d) regression from the stable dives, excluding ``held_out``."""
    src = [d for d in TABLE_SOURCE_DIVES if d in params and d != held_out]
    if not src:
        return None
    out: dict[str, Any] = {"source_dives": src, "beta_d": [], "beta_b": [], "B_inf": [],
                           "ln_E0": [], "K": []}
    for c, ch in enumerate("RGB"):
        out["beta_d"].append(float(np.median([params[d]["channels"][ch]["beta_d"] for d in src])))
        out["beta_b"].append(float(np.median([params[d]["channels"][ch]["beta_b"] for d in src])))
        sweeps = [s for d in src for s in params[d]["sweeps"].values()]
        out["B_inf"].append(float(np.median([s["B_inf"][c] for s in sweeps])))
        good = [s for s in sweeps if s["L"][c] > 0]
        depth = np.array([s["depth_m"] for s in good])
        lnL = np.log([s["L"][c] for s in good])
        coef, *_ = np.linalg.lstsq(np.c_[np.ones_like(depth), -depth], lnL, rcond=None)
        out["ln_E0"].append(float(coef[0]))
        out["K"].append(float(coef[1]))
    return out


def backscatter(params: dict, dive: str, sweep: str, z: float) -> list[float]:
    """B(z) per channel: the frame's own sweep fit if it has one, else the dive medians."""
    p = params.get(dive)
    if p is None:
        return [0.0, 0.0, 0.0]
    out = []
    for c, ch in enumerate("RGB"):
        bb = p["channels"][ch]["beta_b"]
        s = p["sweeps"].get(sweep)
        b_inf = s["B_inf"][c] if s else float(np.median([v["B_inf"][c]
                                                        for v in p["sweeps"].values()]))
        out.append(b_inf * (1 - np.exp(-bb * z)))
    return out


def table_map(tp: dict, depth: float, z: float) -> tuple[list[float], list[float]]:
    offset, gain = [], []
    for c in range(3):
        offset.append(tp["B_inf"][c] * (1 - np.exp(-tp["beta_b"][c] * z)))
        light = np.exp(tp["ln_E0"][c] - tp["K"][c] * depth) * np.exp(-tp["beta_d"][c] * z)
        gain.append(1.0 / light)
    return offset, gain


def apply(I, offset, gain, matrix: np.ndarray) -> np.ndarray:
    cam = (np.asarray(I, dtype=np.float64) - np.asarray(offset)) * np.asarray(gain)
    return cam @ matrix.T


def _truth_linear(card: Card, pid: str) -> float:
    parent = next((s.parent for s in card.sub_patches if s.id == pid), pid)
    return float(srgb8_to_linear(card.patch(parent).truth[0]))


def frame_maps(raw_means: dict, anchor: Optional[str], card: Card, offsets: dict,
               table: dict) -> dict[str, tuple]:
    """{method: (offset, gain)} for the RAW methods available on this frame."""
    maps = {}
    if anchor is not None:
        t = _truth_linear(card, anchor)
        a = np.asarray(raw_means[anchor])
        maps["raw_card_wb"] = ([0.0] * 3, (t / np.maximum(a, 1e-9)).tolist())
        off = np.asarray(offsets["card"])
        maps["nereus_card"] = (off.tolist(), (t / np.maximum(a - off, 1e-9)).tolist())
    maps.update(table)
    return maps


def score_frame(stem: str, row: dict, q: dict, pat: dict, card: Card, matrix: np.ndarray,
                offsets: dict, table: dict) -> dict[str, Any]:
    keep = {pid for pid, p in q["patches"].items() if p["usable"]}
    excluded = [pid for pid in q["patches"] if pid not in keep]
    anchor = next((a for a in ANCHORS if a in keep), None)
    raw = {pid: s["mean_norm"] for pid, s in pat["raw"]["patches"].items()
           if pid in keep and s.get("mean_norm")}
    maps = frame_maps(raw, anchor, card, offsets, table)
    held = (anchor,) + (("gray_mid",) if anchor == "gray_mid_right" else ())
    out: dict[str, Any] = {"anchor": anchor, "methods": {}}
    for method, (off, gain) in maps.items():
        means = {pid: encode8(apply(v, off, gain, matrix)).tolist() for pid, v in raw.items()}
        neutral = held if method in CLASSES["card_anchored"] else ()
        out["methods"][method] = score_srgb8(means, card, neutralized=neutral,
                                             anchor=anchor or "gray_mid", exclude=excluded)
    jpeg = pat.get("jpeg")
    if jpeg:
        jm = {pid: s["mean"] for pid, s in jpeg["patches"].items() if pid in keep and s.get("mean")}
        out["methods"]["camera_jpeg"] = score_srgb8(jm, card, anchor=anchor or "gray_mid",
                                                    exclude=excluded)
        if anchor is not None:
            lin = {k: srgb8_to_linear(v) for k, v in jm.items()}
            gain = _truth_linear(card, anchor) / np.maximum(lin[anchor], 1e-9)
            wb = {k: encode8(v * gain).tolist() for k, v in lin.items()}
            out["methods"]["jpeg_card_wb"] = score_srgb8(wb, card, neutralized=held,
                                                         anchor=anchor, exclude=excluded)
    for m in out["methods"].values():
        m.pop("de2000", None)  # per-patch detail stays out of the frame summary
    return out


def render(raw_path: Path, maps: dict, matrix: np.ndarray, out_dir: Path, stem: str,
           sidecar: dict, reader: Callable[[Path], RawFrame]) -> list[str]:
    """Write one L3 JPEG + JSON sidecar per method (2×2-binned resolution)."""
    frame = reader(raw_path)
    linear, sat, cfa = normalize(frame)
    binned, _ = bin2x2(linear, cfa, sat)
    binned = binned / frame.exposure_factor()
    written = []
    for method, (off, gain) in maps.items():
        img = encode8(apply(binned, off, gain, matrix)).astype(np.uint8)
        d = out_dir / method
        d.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(d / f"{stem}.jpg"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                    [cv2.IMWRITE_JPEG_QUALITY, 90])
        (d / f"{stem}.json").write_text(json.dumps(
            {**sidecar, "method": method, "offset": off, "gain": gain,
             "color_matrix": matrix.tolist(), "space": "exposure-normalized linear camera RGB"
             " → offset, gain → color_matrix → clip → sRGB"}, indent=1) + "\n")
        written.append(method)
    return written


def correct(fit_dir: Path, calibration: Path, dataset_config: Path, card_path: Path,
            raw_reader: Optional[Callable[[Path], RawFrame]] = None,
            workers: int | None = None) -> dict[str, Any]:
    verify_fresh(fit_dir)
    root = fit_dir.parent
    dataset_dir = Path(verify_fresh(root / "ingest")["params"]["dataset_dir"])
    rows = {r["stem"]: r for r in csv.DictReader((root / "ingest" / "manifest.csv").open())}
    params = json.loads((fit_dir / "params.json").read_text())
    qc = json.loads((root / "qc" / "qc.json").read_text())
    patches = json.loads((root / "patches" / "patches.json").read_text())
    dist = json.loads((root / "distance" / "distances.json").read_text())
    card = load_card(card_path)
    matrix = np.asarray(load_yaml(calibration)["color_matrix"]["matrix"], dtype=np.float64)
    cfg = load_yaml(dataset_config)
    default_z = float(cfg.get("default_scene_z_m", 2.0))
    no_card = set(cfg.get("no_card_categories", ["4_no_card"]))
    torch = set(cfg.get("torch_frames") or [])

    scores, frames, jobs = {}, {}, []
    for stem, row in rows.items():
        d = dist.get(stem, {})
        tp = table_params(params, row["dive_id"])
        if row["category"] in no_card:
            if row["has_raw"] != "True" or tp is None or stem in torch:
                continue
            table = {"nereus_table_default_z": table_map(tp, float(row["depth_m"]), default_z)}
            frames[stem] = {"z_m": None, "table_source": tp["source_dives"]}
            jobs.append((dataset_dir / row["file"], table, stem, frames[stem]))
            continue
        q = qc.get(stem)
        if (q is None or not q["usable"] or "raw" not in patches.get(stem, {})
                or d.get("medium") != "water" or d.get("z_m") is None):
            continue
        z, depth = d["z_m"], float(row["depth_m"])
        offsets = {"card": backscatter(params, row["dive_id"], row["sweep_id"], z)}
        table = {}
        if tp is not None:
            table = {"nereus_table": table_map(tp, depth, z),
                     "nereus_table_default_z": table_map(tp, depth, default_z)}
        scores[stem] = score_frame(stem, row, q, patches[stem], card, matrix, offsets, table)
        raw_means = {pid: s["mean_norm"] for pid, s in patches[stem]["raw"]["patches"].items()
                     if s.get("mean_norm")}
        maps = frame_maps(raw_means, scores[stem]["anchor"], card, offsets, table)
        frames[stem] = {"z_m": z, "anchor": scores[stem]["anchor"],
                        "table_source": tp["source_dives"] if tp else None}
        jobs.append((dataset_dir / row["file"], maps, stem, frames[stem]))

    out_dir = root / "correct"
    img_dir = out_dir / "images"
    if raw_reader is not None:
        fn = _Render(matrix, img_dir, raw_reader)
        run_parallel(fn, [(p, m, s, f) for p, m, s, f in jobs], workers or os.cpu_count() or 1)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scores.json").write_text(json.dumps(scores, indent=1) + "\n")
    (out_dir / "frames.json").write_text(json.dumps(frames, indent=1) + "\n")
    summary = {"scored_frames": len(scores), "rendered_frames": len(jobs) if raw_reader else 0,
               "no_card_frames": sum(1 for s in frames if s not in scores)}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_stage(out_dir, "correct", configs=[calibration, dataset_config, card_path],
                upstream=[fit_dir], params={"anchors": list(ANCHORS),
                                            "table_source_dives": list(TABLE_SOURCE_DIVES),
                                            "default_scene_z_m": default_z})
    summary["out_dir"] = str(out_dir)
    return summary


class _Render:
    """Picklable render job for ``run_parallel``."""

    def __init__(self, matrix, out_dir, reader):
        self.matrix, self.out_dir, self.reader = matrix, out_dir, reader

    def __call__(self, raw_path, maps, stem, sidecar):
        return render(raw_path, maps, self.matrix, self.out_dir, stem, sidecar, self.reader)
