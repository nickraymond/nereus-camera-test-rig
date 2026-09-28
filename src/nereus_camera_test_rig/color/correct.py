"""Stage ``correct`` — apply every method to every usable frame (SPEC §4 Phase 8 S2a, v0.2).

Each RAW method is one per-channel affine map in exposure-normalized linear camera RGB, then
the camera colour matrix (L1), clip, sRGB encode (L3)::

    out = encode(clip(M · ((I − haze) · gain)))

| method | column name | haze | gain | card |
|---|---|---|---|---|
| ``raw_card_wb`` | RAW + card WB | 0 | anchor grey → its truth | yes |
| ``raw_card_slope_wb`` | RAW + card slope WB | 0 | grey-ramp slope (light colour) | yes |
| ``raw_card_wb_haze`` | RAW + card WB − haze | grey-ramp intercept, capped at the dark floor | anchor grey (after haze) → truth | yes |
| ``raw_depth_wb_haze`` | RAW + depth WB − haze (no card) | the dark floor | light colour from depth; brightness from the image | **no** |

JPEG baselines score the camera's 8-bit output: ``camera_jpeg`` (A-mode as-shot) and
``olympus_preset_jpeg`` (the Olympus underwater preset, ``2_underwater_preset`` frames) — kept
apart (SPEC §20 baselines b, c) — and ``jpeg_card_wb`` (the decoded camera JPEG white-balanced
on the anchor grey; a channel the camera clipped to 0 stays 0). ``grvi_cheeca_v3`` is the
backend GRVI correction (baseline a, OQ-31), read from a fresh ``grvi`` stage when one exists
(``host_tools.color grvi``); GRVI solves on every card patch, so its scores are in-sample, and
where it found no card its output is the camera JPEG (scored as such, ``grvi_no_card``).
Flash frames (the Olympus preset fired its flash) are scored on the camera's outputs only
(``flash: true``), since the flash breaks the water model; every preset frame records its
nearest A-mode reference frame in the same dive (``pair_a_mode``) for the preset comparison.

- Anchor grey: the card's ``roles.wb_anchors``, first one qc kept (V2: grey 128, its right half,
  grey 74).
- Card slope WB: white balance on the **slope** ``A`` of the grey ramp (the light reaching
  the card, haze-free) instead of on the anchor grey, which also carries the haze: in turbid
  water grey 128 is mostly blue-green backscatter, so balancing on it over-boosts red. No haze
  is subtracted; green gets the same gain as ``raw_card_wb``, so brightness is unchanged.
- Card haze: the intercept of a straight line through the usable greys (``ramp_fit``), per
  channel, capped at the **dark floor** — the ``DARK_PERCENTILE`` of the image centre
  (``CENTRE`` of each side; the corners are vignetted). How often the cap binds is logged.
- Depth WB table: ``ln(R/G)``, ``ln(B/G)`` of the light vs depth, pooled over the dataset's
  ``fit.table_source_dives`` **leaving the frame's own dive out**; brightness puts the image's
  99th percentile of green at ``TARGET_P99``. Distance is not used (run A: colour barely
  changes over 0.5–3 m). ``raw_depth_wb_haze_loso`` is the same table validated
  **leave-one-sweep-out** (only the frame's own sweep, or the frame itself outside a sweep, is
  left out — the frame's dive stays in when it is a source dive); scored, not rendered.

Scoring (SPEC §20): the map is applied to the patch means (and their stds, through the same
map) and encoded to 8-bit. Within a comparison class every method is scored on the same
patches: the card-anchored class holds out **every grey** (the ramp fit uses them, so ψ there
would be circular) and is scored on ΔE2000 of the 12 colour patches; the card-free class
scores ψ on all usable greys and ΔE2000. Per-patch ΔE is kept.

Images: a pixel with any channel clipped at the sensor's white level is rendered neutral
(highlight handling; otherwise white balance tints clipped whites magenta).

Output: ``correct/{scores.json, frames.json, images/<method>/<stem>.jpg|.json, summary.json,
stage.json}``; ``images/`` is rebuilt on every run.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np

from ..config import load_yaml
from .card import Card, load_card
from .ccm import MIN_AFFINE_PATCHES, affine_leave_one_out, fit_affine, leave_one_dive_out, matrix_at
from .metrics import score_linear, score_srgb8, srgb8_to_linear
from .raw_io import RawFrame, bin2x2, normalize
from .stages import run_parallel, verify_fresh, write_stage
from .water_model import fit_settings, grey_reflectance, ramp_fit

DARK_PERCENTILE = 0.5
CENTRE = 0.6
TARGET_P99 = 0.8
COLUMNS = {  # method → the name used in sheets and reports
    "camera_jpeg": "Camera JPEG",
    "olympus_preset_jpeg": "Olympus underwater preset JPEG",
    "jpeg_card_wb": "JPEG + card WB",
    "raw_card_wb": "RAW + card WB",
    "raw_card_slope_wb": "RAW + card slope WB",
    "raw_card_wb_haze": "RAW + card WB − haze",
    "raw_depth_wb_haze": "RAW + depth WB − haze (no card)",
    "grvi_cheeca_v3": "GRVI cheeca_v3 (backend)",
    "raw_depth_wb_haze_loso": "RAW + depth WB − haze (no card, leave-one-sweep-out)",
    "raw_card_wb_ccm": "RAW + card WB + depth matrix (v0.3)",
    "raw_depth_wb_haze_ccm": "RAW + depth WB − haze + depth matrix (no card, v0.3)",
    "raw_card_affine": "RAW + per-frame card affine (leave-one-patch-out)",
}
NOT_RENDERED = {"raw_depth_wb_haze_loso"}  # a validation variant: scored, no images
CLASSES = {"card_anchored": ("grvi_cheeca_v3", "jpeg_card_wb", "raw_card_wb",
                             "raw_card_slope_wb", "raw_card_wb_haze", "raw_card_wb_ccm",
                             "raw_card_affine"),
           "card_free": ("camera_jpeg", "olympus_preset_jpeg", "raw_depth_wb_haze",
                         "raw_depth_wb_haze_loso", "raw_depth_wb_haze_ccm")}
PRESET_CATEGORY = "2_underwater_preset"


def encode8(linear: np.ndarray) -> np.ndarray:
    x = np.clip(linear, 0.0, 1.0)
    x = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return np.round(x * 255)


def apply(I, haze, gain, matrix: np.ndarray) -> np.ndarray:
    return ((np.asarray(I, dtype=np.float64) - np.asarray(haze)) * np.asarray(gain)) @ matrix.T


def apply_std(std, gain, matrix: np.ndarray) -> np.ndarray:
    """Per-channel std through the same map (channels treated as independent)."""
    s = np.asarray(std, dtype=np.float64) * np.asarray(gain)
    return np.sqrt((s ** 2) @ (matrix ** 2).T)


def dark_floor(image: np.ndarray) -> np.ndarray:
    """Per-channel ``DARK_PERCENTILE`` of the central ``CENTRE`` of the image."""
    h, w = image.shape[:2]
    y0, x0 = int(h * (1 - CENTRE) / 2), int(w * (1 - CENTRE) / 2)
    return np.percentile(image[y0:h - y0, x0:w - x0].reshape(-1, 3), DARK_PERCENTILE, axis=0)


def depth_table(points_csv: Path, sources: list[str], held_out: Optional[str],
                exclude_stems=frozenset()) -> Optional[dict]:
    """Pooled ln(R/G), ln(B/G) vs depth over the source dives, leaving out the dive
    ``held_out`` (leave-one-dive-out) or only the frames ``exclude_stems`` (leave-one-sweep-out:
    the frame's own sweep)."""
    pts = [p for p in csv.DictReader(points_csv.open())
           if p["dive"] in sources and p["dive"] != held_out and p.get("stem") not in exclude_stems]
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


def frame_maps(job: dict, image: np.ndarray) -> tuple[dict[str, tuple], dict[str, Any]]:
    """({method: (haze, gain)}, diagnostics) for this frame."""
    dark = dark_floor(image)
    maps: dict[str, tuple] = {}
    diag: dict[str, Any] = {"dark_floor": dark.tolist()}
    anchor, raw = job.get("anchor"), job.get("raw_means") or {}
    if anchor:
        t = job["anchor_truth"]
        a = np.asarray(raw[anchor])
        maps["raw_card_wb"] = ([0.0] * 3, (t / np.maximum(a, 1e-9)).tolist())
        ramp = job.get("ramp")
        if ramp is not None and min(ramp["A"]) > 0:
            A = np.asarray(ramp["A"])
            maps["raw_card_slope_wb"] = ([0.0] * 3,
                                         (t * A[1] / max(a[1], 1e-9) / A).tolist())
        if ramp is None:
            haze, diag["haze_source"] = dark, "dark floor (fewer than 2 usable greys)"
        else:
            h = np.maximum(np.asarray(ramp["H"]), 0.0)
            diag["haze_cap_bound"] = (h > dark).tolist()
            haze, diag["haze_source"] = np.minimum(h, dark), "grey ramp"
        maps["raw_card_wb_haze"] = (np.asarray(haze).tolist(),
                                    (t / np.maximum(a - haze, 1e-9)).tolist())
    for method, key in (("raw_depth_wb_haze", "table"), ("raw_depth_wb_haze_loso", "table_loso")):
        table = job.get(key)
        if table:
            d = job["depth_m"]
            colour = np.array([np.exp(table["ln_rg"][0] + table["ln_rg"][1] * d), 1.0,
                               np.exp(table["ln_bg"][0] + table["ln_bg"][1] * d)])
            green = (image[..., 1] - dark[1]).ravel()
            scale = TARGET_P99 / max(float(np.percentile(green, 99)), 1e-9)
            maps[method] = (dark.tolist(), (scale / colour).tolist())
    if job.get("affine"):  # per-frame card affine A x + c, as (haze, gain, M): haze = −A⁻¹c
        A, c = np.asarray(job["affine"]["A"]), np.asarray(job["affine"]["c"])
        maps["raw_card_affine"] = ((-np.linalg.solve(A, c)).tolist(), [1.0] * 3, A.tolist())
    if job.get("ccm") is not None:  # v0.3: the same maps with the depth matrix (3rd element)
        for base in ("raw_card_wb", "raw_depth_wb_haze"):
            if base in maps:
                maps[f"{base}_ccm"] = (*maps[base], job["ccm"])
    return maps, diag


def _parts(entry, matrix: np.ndarray) -> tuple:
    """(haze, gain, matrix) of a map entry; the camera matrix unless the entry carries one."""
    haze, gain, *m = entry
    return haze, gain, (np.asarray(m[0]) if m else matrix)


def score(job: dict, maps: dict, card: Card, matrix: np.ndarray) -> dict[str, Any]:
    anchor, excluded = job["anchor"], job["excluded"]
    lm = anchor or "gray_mid"
    out: dict[str, Any] = {}
    for method, entry in maps.items():
        haze, gain, M = _parts(entry, matrix)
        means = {pid: srgb8_to_linear(encode8(apply(v, haze, gain, M)))
                 for pid, v in job["raw_means"].items()}
        if method == "raw_card_affine":  # colour patches: predicted from the other patches
            means.update({pid: srgb8_to_linear(encode8(v))
                          for pid, v in job["affine"]["loo"].items()})
        stds = {pid: apply_std(job["raw_stds"][pid], gain, M) for pid in means}
        neutral = card.grey_ids if method in CLASSES["card_anchored"] else ()
        out[method] = score_linear(means, card, neutralized=neutral, anchor=lm,
                                   exclude=excluded, stds=stds)
    jm, js = job.get("jpeg_means"), job.get("jpeg_stds")
    if jm:
        jpeg_method = "olympus_preset_jpeg" if job["category"] == PRESET_CATEGORY else "camera_jpeg"
        out[jpeg_method] = score_srgb8(jm, card, neutralized=(), anchor=lm, exclude=excluded,
                                       stds=js)
        if anchor and anchor in jm:
            lin = {k: srgb8_to_linear(v) for k, v in jm.items()}
            gain = job["anchor_truth"] / np.maximum(lin[anchor], 1e-9)
            out["jpeg_card_wb"] = score_srgb8({k: encode8(v * gain).tolist()
                                               for k, v in lin.items()},
                                              card, neutralized=card.grey_ids, anchor=anchor,
                                              exclude=excluded)
    gm = job.get("grvi_means") or (jm if job.get("grvi_no_card") else None)
    if gm:
        out["grvi_cheeca_v3"] = score_srgb8(gm, card, neutralized=card.grey_ids, anchor=lm,
                                            exclude=excluded)
        out["grvi_cheeca_v3"]["grvi_no_card"] = bool(job.get("grvi_no_card"))
    return out


def card_job(stem: str, row: dict, q: dict, patches: dict, grvi: Optional[dict], card: Card,
             rho) -> dict[str, Any]:
    """Scoring inputs of one card frame: qc-kept patch means per source (RAW, camera JPEG,
    GRVI output), the anchor grey and the card-ramp fit."""
    keep = {pid for pid, p in q["patches"].items() if p["usable"]}
    anchor = next((a for a in card.roles.wb_anchors if a in keep), None)
    jpeg = (patches.get("jpeg") or {"patches": {}})["patches"]
    job: dict[str, Any] = {
        "stem": stem, "category": row["category"], "depth_m": float(row["depth_m"]),
        "dive_id": row["dive_id"],
        "anchor": anchor, "excluded": [pid for pid in q["patches"] if pid not in keep],
        "anchor_truth": _truth_linear(card, anchor) if anchor else None,
        "jpeg_means": {pid: s["mean"] for pid, s in jpeg.items() if pid in keep and s.get("mean")},
        "jpeg_stds": {pid: s["std"] for pid, s in jpeg.items() if pid in keep and s.get("mean")},
        "card_condition": q["card_condition"]}
    if "raw" in patches:
        raw_stats = patches["raw"]["patches"]
        k = patches["raw"]["exposure_factor"]
        job.update(
            ramp=ramp_fit(raw_stats, keep, rho, card.roles.ramp),
            raw_means={pid: s["mean_norm"] for pid, s in raw_stats.items()
                       if pid in keep and s.get("mean_norm")},
            raw_stds={pid: (np.asarray(s["std"]) / k).tolist() for pid, s in
                      raw_stats.items() if pid in keep and s.get("mean_norm")})
    # per-frame affine on the card's own patches; each grey 128 half only stands in for a
    # damaged whole (no patch counted twice)
    raw = job.get("raw_means") or {}
    ids = [p.id for p in card.patches if p.id in raw]
    if "gray_mid" not in raw:
        ids += [s.id for s in card.sub_patches if s.id in raw][:1]
    if len(ids) >= MIN_AFFINE_PATCHES + 1:
        x = {pid: raw[pid] for pid in ids}
        t = {pid: srgb8_to_linear(card.patch(next((s.parent for s in card.sub_patches
                                                     if s.id == pid), pid)).truth)
             for pid in ids}
        A, c = fit_affine(list(x.values()), list(t.values()))
        loo = affine_leave_one_out(x, t, [p.id for p in card.group("color")])
        job["affine"] = {"A": A.tolist(), "c": c.tolist(),
                         "loo": {k: v.tolist() for k, v in loo.items()}}
    if (grvi or {}).get("no_card"):
        job["grvi_no_card"] = True
    elif grvi and "patches" in grvi:
        job["grvi_means"] = {pid: s["mean"] for pid, s in grvi["patches"].items()
                             if pid in keep and s.get("mean")}
    return job


def nearest_a_mode(stem: str, rows: dict, candidates, category: str) -> Optional[dict]:
    """The nearest ``category`` (A-mode reference) frame in the same dive, by capture time."""
    me = rows[stem]
    t = datetime.fromisoformat(me["time_utc"])
    best = min(((abs((datetime.fromisoformat(rows[c]["time_utc"]) - t).total_seconds()), c)
                for c in candidates if rows[c]["category"] == category
                and rows[c]["dive_id"] == me["dive_id"]), default=None)
    if best is None:
        return None
    return {"stem": best[1], "dt_s": round(best[0], 1),
            "depth_diff_m": round(float(rows[best[1]]["depth_m"]) - float(me["depth_m"]), 2)}


def ccm_observations(jobs: list[dict], card: Card) -> list[dict]:
    """v0.3 training data: per usable card frame, its colour patches white-balanced on the
    anchor grey (the ``raw_card_wb`` map, camera RGB) and their design values (linear sRGB)."""
    obs = []
    colours = [p.id for p in card.group("color")]
    for job in jobs:
        raw, anchor = job.get("raw_means") or {}, job.get("anchor")
        ids = [pid for pid in colours if pid in raw]
        if not anchor or anchor not in raw or len(ids) < 6:
            continue
        gain = job["anchor_truth"] / np.maximum(np.asarray(raw[anchor]), 1e-9)
        obs.append({"stem": job["stem"], "dive": job["dive_id"], "depth_m": job["depth_m"],
                    "x": [(np.asarray(raw[pid]) * gain).tolist() for pid in ids],
                    "t": [srgb8_to_linear(card.patch(pid).truth).tolist() for pid in ids]})
    return obs


class _Frame:
    """Picklable per-frame worker: read the RAW once, derive maps, score, render."""

    def __init__(self, card: Card, matrix: np.ndarray, out_dir: Path,
                 reader: Callable[[Path], RawFrame]):
        self.card, self.matrix, self.out_dir, self.reader = card, matrix, out_dir, reader

    def __call__(self, job: dict) -> dict[str, Any]:
        frame = self.reader(Path(job["raw_path"]))
        linear, sat, cfa = normalize(frame)
        image, clipped = bin2x2(linear, cfa, sat)
        image = image / frame.exposure_factor()
        clipped = clipped.any(axis=-1)
        maps, diag = frame_maps(job, image)
        result = {"maps": maps, "diag": diag,
                  "scores": score(job, maps, self.card, self.matrix)
                  if job.get("raw_means") else {}}
        for method, entry in maps.items():
            if method in NOT_RENDERED:
                continue
            haze, gain, M = _parts(entry, self.matrix)
            out = apply(image, haze, gain, M)
            # A pixel with any channel at the sensor's white level has lost its colour; white
            # balance would tint it (clipped G/B whites turn magenta once red is boosted).
            # Render it neutral at its brightest channel. Scoring is unaffected: qc already
            # excludes clipped patches.
            out[clipped] = out[clipped].max(axis=-1, keepdims=True)
            img = encode8(out).astype(np.uint8)
            d = self.out_dir / method
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{job['stem']}.jpg"
            if not cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                               [cv2.IMWRITE_JPEG_QUALITY, 90]):
                raise IOError(f"failed to write {path}")
            (d / f"{job['stem']}.json").write_text(json.dumps(
                {"stem": job["stem"], "method": method, "column": COLUMNS[method],
                 "haze": haze, "gain": gain, "color_matrix": M.tolist(),
                 "depth_m": job["depth_m"], "anchor": job.get("anchor"),
                 "table": job.get("table"), **diag,
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
    matrix = np.asarray(load_yaml(calibration)["color_matrix"]["matrix"], dtype=np.float64)
    cfg = load_yaml(dataset_config)
    settings = fit_settings(dataset_config)
    sources = settings["table_source_dives"]
    no_card = set(cfg.get("no_card_categories", ["4_no_card"]))
    torch = set(cfg.get("torch_frames") or [])
    rho = grey_reflectance(card)
    grvi_dir = root / "grvi"
    grvi = None
    if (grvi_dir / "stage.json").is_file():
        grvi_sha = verify_fresh(grvi_dir)["params"]["backend_sha"]
        grvi = json.loads((grvi_dir / "patches.json").read_text())
    tables = {d: depth_table(fit_dir / "wb_points.csv", sources, d)
              for d in {r["dive_id"] for r in rows.values()}}
    sweeps: dict[str, set] = {}
    for stem, row in rows.items():
        if row["sweep_id"]:
            sweeps.setdefault(row["sweep_id"], set()).add(stem)

    jobs, flash_jobs = [], []
    for stem, row in rows.items():
        if row["has_raw"] != "True" or stem in torch:
            continue
        q, d = qc.get(stem), dist.get(stem, {})
        if row["flash_fired"] == "True":
            # the flash breaks the water model: only the camera's own outputs are scored,
            # and reported apart (SPEC §4 S2a baseline b)
            if q is not None and "jpeg" in patches.get(stem, {}) and d.get("medium") == "water":
                flash_jobs.append(card_job(stem, row, q, patches[stem], (grvi or {}).get(stem),
                                           card, rho))
            continue
        sweep = sweeps.get(row["sweep_id"], {stem}) if row["sweep_id"] else {stem}
        job: dict[str, Any] = {"stem": stem, "raw_path": str(dataset_dir / row["file"]),
                               "depth_m": float(row["depth_m"]), "category": row["category"],
                               "table": tables[row["dive_id"]],
                               "table_loso": depth_table(fit_dir / "wb_points.csv", sources,
                                                         None, frozenset(sweep))}
        if row["category"] not in no_card:
            if (q is None or not q["usable"] or "raw" not in patches.get(stem, {})
                    or d.get("medium") != "water"):
                continue
            job.update(card_job(stem, row, q, patches[stem], (grvi or {}).get(stem), card, rho))
        jobs.append(job)

    # v0.3 depth matrix, leave-one-dive-out: fitted on the other dives' card frames
    obs = ccm_observations(jobs, card)
    ccm = leave_one_dive_out(obs, sorted({r["dive_id"] for r in rows.values()}))
    for job in jobs:
        m = matrix_at(ccm[rows[job["stem"]]["dive_id"]], job["depth_m"])
        job["ccm"] = None if m is None else m.tolist()

    out_dir = root / "correct"
    shutil.rmtree(out_dir / "images", ignore_errors=True)  # never mix runs
    results = run_parallel(_Frame(card, matrix, out_dir / "images", raw_reader),
                           [(j,) for j in jobs], workers or os.cpu_count() or 1)
    scores, frames = {}, {}
    cap_bound = [0, 0]
    for job, res in zip(jobs, results):
        frames[job["stem"]] = {"anchor": job.get("anchor"), "depth_m": job["depth_m"],
                               "category": job["category"],
                               "table_source": (job["table"] or {}).get("source_dives"),
                               "maps": res["maps"], **res["diag"]}
        if "haze_cap_bound" in res["diag"]:
            cap_bound[0] += any(res["diag"]["haze_cap_bound"])
            cap_bound[1] += 1
        if res["scores"]:
            row = rows[job["stem"]]
            scores[job["stem"]] = {"anchor": job.get("anchor"), "category": job["category"],
                                   "dive_id": row["dive_id"], "sweep_id": row["sweep_id"],
                                   "card_condition": job["card_condition"],
                                   "methods": res["scores"]}
    for job in flash_jobs:
        row = rows[job["stem"]]
        scores[job["stem"]] = {"anchor": job["anchor"], "category": job["category"],
                               "dive_id": row["dive_id"], "sweep_id": row["sweep_id"],
                               "card_condition": job["card_condition"], "flash": True,
                               "methods": score(job, {}, card, matrix)}
    a_mode = [s for s, v in scores.items() if not v.get("flash")]
    for stem, v in scores.items():
        if v["category"] == PRESET_CATEGORY:
            v["pair_a_mode"] = nearest_a_mode(stem, rows, a_mode, settings["reference_category"])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scores.json").write_text(json.dumps(scores, indent=1) + "\n")
    (out_dir / "frames.json").write_text(json.dumps(frames, indent=1) + "\n")
    summary = {"scored_frames": len(scores), "rendered_frames": len(frames),
               "flash_frames_jpeg_only": sorted(j["stem"] for j in flash_jobs),
               "no_card_frames": sum(1 for j in jobs if j["category"] in no_card),
               "card_haze_cap_bound_frames": f"{cap_bound[0]} of {cap_bound[1]}",
               "grvi_backend_sha": grvi_sha if grvi else None,
               "grvi_no_card_frames": sorted(s for s, v in scores.items()
                                             if v["methods"].get("grvi_cheeca_v3", {})
                                             .get("grvi_no_card")),
               "columns": COLUMNS}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out_dir / "ccm.json").write_text(json.dumps(
        {"method": "3x3 per depth tercile, rows sum to 1, leave-one-dive-out",
         "training_frames": len(obs), "held_out_dive": ccm}, indent=1) + "\n")
    write_stage(out_dir, "correct", configs=[calibration, dataset_config, card_path],
                upstream=[fit_dir] + ([grvi_dir] if grvi else []),
                params={"version": "v0.3", "anchors": list(card.roles.wb_anchors),
                        "table_source_dives": sources,
                        "dark_percentile": DARK_PERCENTILE,
                        "centre": CENTRE, "target_p99": TARGET_P99})
    summary["out_dir"] = str(out_dir)
    return summary
