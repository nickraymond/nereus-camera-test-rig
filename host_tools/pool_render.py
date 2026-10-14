"""Host tool: pool-run render, best frames and cut sheet (Mac side; the run folder is read only).

Purpose
    Turn a pulled pool run (written by the Pi) into review artifacts: score every frame per
    camera, pick the sharpest top-K per camera, render those from RAW at full resolution, cut
    the IMX708 production frame (the Spotter units' 1600x900 region) from every set, and build
    a cut sheet (HTML + PNG) that also puts this rig's IMX708 crop next to the Spotter units'.

Inputs
    <run_dir> ...        pulled run folder(s): run_manifest.json, sets.jsonl, pool.log (all
                         optional here) and set folders ``<run>/<YYYY-MM-DD>/exp_<UTC>_<type>/``
                         with experiment.json and ``captures/{imx708,openmv_n6,openmv_ae3}/``.
                         Any camera may be missing in a set (camera failed): logged, never fatal.
    --spotter-root DIR   optional, READ ONLY: unit folders ``bmcam*/`` holding (recursively)
                         4608x2592 full-native JPEGs (+ ``<stem>.metadata.json`` from rpicam).

Outputs (``--out``, default ``<run parent>/<run name>_render/``; refused inside a run folder or
a Spotter unit folder — raw captures are evidence)
    scores.csv            one row per set x camera: files, exposure/gain, metrics, PASS/WARN/FAIL
    crops.csv             one row per IMX708 production crop (every set)
    best_images.html/.png cut sheet; sheet/ holds its image assets
    render_manifest.json  inputs (+sha256), outputs, method strings, settings, timings, warnings
    render.log            everything printed
    <run>/<set>/          renders: IMX708 DNG -> sRGB 16-bit TIFF + 8-bit PNG; OpenMV Bayer ->
                          sRGB PNG + 16-bit TIFF + the board JPEG copied unmodified; production
                          crops (PNG lossless + JPEG q95 re-encode)
    *_captioned.png|.jpg  a COPY of every 8-bit render / crop / board JPEG (and of the rpicam
                          JPEG shown on the sheet) at full resolution with a black caption bar
                          appended BELOW it (time Pacific, depth, water temp, camera, W x H,
                          source file; see host_tools/pool_caption.py). PNG for PNGs, JPEG q95
                          for JPEGs. The sheet shows the captioned versions.

Example
    .venv/bin/python -m host_tools.pool_render ~/NereusPool/20261014/pool_<run> \\
        --spotter-root ~/NereusPool/20261014 --top-k 3

Method (also written to render_manifest.json "method")
    Scoring is on luma (Rec.601, cv2 BGR2GRAY) of the *viewable* image: IMX708 = the rpicam
    JPEG paired with the DNG (``imx708_raw_*.jpg``, else the auto still ``imx708_image_*.jpg``);
    OpenMV = this tool's Mac render of the Bayer (not the board JPEG). Sharpness = variance of
    the Laplacian (3x3), plus Tenengrad (mean Sobel gradient energy) for reference.

    IMX708 DNG render reuses ``color.raw_io`` (read_dng, normalize, demosaic_bilinear). No
    display render with a CCM existed in the repo (``stages.inspect`` is a WB-only quick look),
    so the minimal one is here. The CCM comes from the DNG: rpicam writes
    ColorMatrix1 = inv(RGB2XYZ . CCM . diag(WB)) and AsShotNeutral = 1/WB, so
    CCM = XYZ_TO_SRGB . inv(ColorMatrix1) . diag(1/AsShotWB) — checked on the 2026-09-29
    fixture against the rpicam metadata's ColourCorrectionMatrix (agrees to ~5e-4).

Known limitations
    - The Mac DNG render has no lens-shading correction, denoise, sharpening or libcamera tone
      curve (plain sRGB OETF): expect darker corners and flatter contrast than the rpicam JPEG.
      Highlights are clipped to white after WB (no reconstruction), so blown areas are white.
    - OpenMV renders are 8-bit sensor data; the 16-bit TIFF carries no extra precision.
    - "Sharpest" is a metric pick; on dark, high-gain frames sensor noise inflates it.
    - Spotter 1600x900 JPEGs are used only when their sidecar ScalerCrop proves they are the
      native (1504, 846) crop; otherwise skipped (a downscaled full frame is not comparable 1:1).
    - 1:1 on the sheet means one image pixel per CSS pixel at 100 % browser zoom (a Retina
      display still doubles it); the PNG sheet is exact. A 1:1 tile is the unresampled crop
      with a caption bar appended below it (the bar is not part of the crop).
    - 16-bit TIFFs are not captioned (data products; the 8-bit PNG of the same render is).
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import logging
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
for _p in (REPO, REPO / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from host_tools import pool_caption as pc  # noqa: E402
from nereus_camera_test_rig.color.metrics import SRGB_TO_XYZ  # noqa: E402
from nereus_camera_test_rig.color.raw_io import (  # noqa: E402
    demosaic_bilinear,
    normalize,
    read_dng,
    read_openmv_bayer,
)
from nereus_camera_test_rig.color.stages import git_state, sha256_file  # noqa: E402

LOG = logging.getLogger("pool_render")

CAMERAS = ("imx708", "openmv_n6", "openmv_ae3")
NATIVE_IMX708 = (4608, 2592)  # (w, h) full-native IMX708 JPEG/DNG
# Spotter production frame region, native/JPEG px of the 4608x2592 frame: x, y, w, h.
PROD_CROP = (1504, 846, 1600, 900)
CLIP_HI_LEVEL, CLIP_LO_LEVEL = 250, 5  # luma levels (0..255) counted as clipped
FAIL_CLIP_HI_PCT, WARN_CLIP_HI_PCT = 5.0, 1.0
FAIL_MEAN, WARN_MEAN_LO, WARN_MEAN_HI = 15.0, 40.0, 210.0
BLOCK = {"imx708": 1024, "openmv_n6": 512, "openmv_ae3": 512}  # 1:1 crop edge, native px
THUMB_W = {"imx708": 960, "openmv_n6": 640, "openmv_ae3": 640}  # sheet full-frame width
XYZ_TO_SRGB = np.linalg.inv(SRGB_TO_XYZ)
JPEG_FLAGS = cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION  # keep native coordinates
ROLE_LABEL = {"raw_jpg": "rpicam JPEG (same exposure as DNG)",
              "still_jpg": "rpicam auto still JPEG", "bayer": "Mac render of Bayer"}

METHOD = {
    "scoring": "luma = Rec.601 (cv2 BGR2GRAY) of the viewable image; sharpness_lapvar = "
               "var(cv2.Laplacian 3x3); tenengrad = mean(Sobel_x^2 + Sobel_y^2); clip_hi = "
               f"% luma >= {CLIP_HI_LEVEL}; clip_lo = % luma <= {CLIP_LO_LEVEL}. FAIL: file "
               f"missing/unreadable, clip_hi > {FAIL_CLIP_HI_PCT} % or mean < {FAIL_MEAN}; "
               f"WARN: clip_hi > {WARN_CLIP_HI_PCT} % or mean < {WARN_MEAN_LO} or mean > "
               f"{WARN_MEAN_HI}; else PASS. IMX708 viewable = imx708_raw_*.jpg (rpicam JPEG, "
               "same exposure as the DNG), else imx708_image_*.jpg; OpenMV viewable = the Mac "
               "Bayer render below (not the board JPEG).",
    "best": "per camera, top-K by sharpness_lapvar among non-FAIL frames; if every frame "
            "FAILs, top-K of the readable FAIL frames, marked fallback_all_fail.",
    "imx708_dng": "read_dng (tifffile) -> normalize: (DN - DNG BlackLevel) / (DNG WhiteLevel "
                  "- black) -> demosaic_bilinear with the DNG CFAPattern -> x WB (r, 1, b) "
                  "from the paired .rpicam.json ColourGains (fallback DNG AsShotNeutral) -> "
                  "clip at 1.0 (saturated -> white, not magenta) -> x DigitalGain from the "
                  ".rpicam.json (the DNG is pre-digital-gain) -> CCM = "
                  "XYZ_TO_SRGB . inv(DNG ColorMatrix1) . diag(1/DNG AsShotWB) (rpicam's "
                  "ColorMatrix1 = inv(RGB2XYZ . CCM . diag(WB))) -> clip [0, 1] -> sRGB OETF "
                  "(IEC 61966-2-1, 16-bit LUT) -> 16-bit TIFF (zlib) + 8-bit PNG. No lens "
                  "shading, denoise, sharpening or tone curve.",
    "imx708_prod_crop": f"crop the 4608x2592 rpicam JPEG (decoded ignoring EXIF orientation) "
                        f"at native/JPEG px x={PROD_CROP[0]} y={PROD_CROP[1]} w={PROD_CROP[2]} "
                        f"h={PROD_CROP[3]} -> PNG (lossless) + JPEG q95 (re-encode, 2nd "
                        "generation). Done for both rpicam JPEGs of every set.",
    "openmv": "read_openmv_bayer (sidecar W/H/CFA/black/white, sha256 checked) -> normalize "
              "-> demosaic_bilinear with the sidecar CFA -> Mac gray-world AWB (gains = mean "
              "G / mean C over pixels with no channel >= 0.98) -> clip -> sRGB OETF -> rotate "
              "mount_rotation_deg counter-clockwise -> 8-bit PNG + 16-bit TIFF. Board JPEG "
              "copied unmodified as the board ISP render.",
    "sheet": "full frames are resized with cv2.INTER_AREA and labelled with the scale; 1:1 "
             "crops are array slices (no resampling) of the sharpest block (max Laplacian "
             "variance on a block/4 grid) of the scored image; for IMX708 the Mac DNG render "
             "is cut at the same native coordinates (same 4608x2592 geometry, ScalerCrop full).",
    "caption": "every 8-bit image this tool writes or shows gets a COPY <stem>_captioned.png "
               "(PNG) / .jpg (JPEG q95) = the image unchanged at full resolution + a black bar "
               "appended below: capture time in America/Los_Angeles (sets.jsonl clock.pi_utc; + "
               "run_manifest mac_time_at_start - start_clock.pi_utc when both exist = "
               "'Mac-anchored', else 'Pi clock'; fallbacks experiment.json timestamp, then the "
               "exp_<UTC> folder name), depth = mean of the ok Bar30 start/end depth_m (vs the "
               "arm-time surface; else depth_m_run_start, 'vs run start'; fresh-water pressure "
               "proxy computed on the Pi), water = mean Bar30 temp_c, camera, the captioned "
               "image's own W x H, source file. 'n/a' when absent. Font = width/32 px (min 14; "
               "down to width/36 to keep 2 lines); long names wrap or are middle-ellipsized.",
    "spotter": "each 4608x2592 JPEG under <spotter-root>/bmcam*/ is cut at the production "
               "coordinates and scored with the same metrics; best per unit by "
               "sharpness_lapvar (non-FAIL first). 1600x900 JPEGs only when the sidecar "
               "ScalerCrop is (1504, 846)/1600x900.",
}

SCORE_FIELDS = ["run", "set", "set_rel", "camera", "scored_role", "file", "bytes", "width",
                "height", "raw_file", "raw_bytes", "exposure_us", "analogue_gain",
                "digital_gain", "gain_db", "lens_position", "lux", "colour_gains",
                "mean_luma", "clip_hi_pct", "clip_lo_pct", "sharpness_lapvar", "tenengrad",
                "status", "reason", "selected_rank", "rendered"]
CROP_FIELDS = ["run", "set", "source", "source_bytes", "lens_position", "mean_luma",
               "clip_hi_pct", "clip_lo_pct", "sharpness_lapvar", "tenengrad", "status",
               "reason", "png", "jpg", "png_captioned", "jpg_captioned"]


# --- context: warnings, inputs, outputs ---------------------------------------------------

@dataclass
class Ctx:
    out: Path
    warnings: list[str] = field(default_factory=list)
    inputs: dict[str, dict] = field(default_factory=dict)
    outputs: list[dict] = field(default_factory=list)
    timings: dict[str, Any] = field(default_factory=lambda: {"per_set_s": {}, "dng_render_s": {}})
    run_meta: dict[Path, pc.RunMeta] = field(default_factory=dict)  # caption sources per run
    captions: dict[Path, pc.SetCaption] = field(default_factory=dict)  # per set folder

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        LOG.warning("WARN %s", msg)

    def use(self, path: Path, records: Optional[dict] = None) -> None:
        """Record an input actually used: sha256, bytes, and agreement with experiment.json."""
        key = str(path)
        if key in self.inputs:
            return
        digest = sha256_file(path)
        entry = {"path": key, "bytes": path.stat().st_size, "sha256": digest}
        rec = (records or {}).get(path.name)
        if rec and rec.get("sha256"):
            entry["matches_experiment_json"] = rec["sha256"] == digest
            if rec["sha256"] != digest:
                self.warn(f"{path}: sha256 differs from experiment.json record")
        self.inputs[key] = entry

    def wrote(self, path: Path, kind: str, shape: Optional[tuple] = None) -> Path:
        size = path.stat().st_size if path.is_file() else 0
        if size == 0:
            raise IOError(f"output {path} missing or empty after write")
        entry = {"path": str(path.relative_to(self.out)), "kind": kind, "bytes": size}
        if shape is not None:
            entry["width"], entry["height"] = int(shape[1]), int(shape[0])
        self.outputs.append(entry)
        return path


# --- discovery ----------------------------------------------------------------------------

def discover_sets(run_dir: Path) -> list[Path]:
    """Set folders ``<run>/<date>/exp_*``, sorted by folder name (UTC timestamp first)."""
    sets = [p for p in run_dir.glob("*/exp_*") if p.is_dir()]
    return sorted(sets, key=lambda p: (p.name, str(p)))


def _one(folder: Path, pattern: str, ctx: Ctx) -> Optional[Path]:
    hits = sorted(p for p in folder.glob(pattern) if p.is_file())
    if len(hits) > 1:
        ctx.warn(f"{folder}: {len(hits)} files match {pattern}; using {hits[0].name}")
    return hits[0] if hits else None


def locate_files(set_dir: Path, camera: str, ctx: Ctx) -> dict[str, Optional[Path]]:
    """Per-camera file roles in one set; a missing file is None (never an exception)."""
    d = set_dir / "captures" / camera
    if camera == "imx708":
        files = {"dng": _one(d, "imx708_raw_*.dng", ctx),
                 "raw_jpg": _one(d, "imx708_raw_*.jpg", ctx),
                 "still_jpg": _one(d, "imx708_image_*.jpg", ctx)}
        for role in ("raw_jpg", "still_jpg"):
            side = Path(f"{files[role]}.rpicam.json") if files[role] else None
            files[f"{role}_meta"] = side if side and side.is_file() else None
        return files
    bayer = _one(d, f"{camera}_raw_*.bayer", ctx)
    side = bayer.with_suffix(".json") if bayer else None
    return {"bayer": bayer, "bayer_json": side if side and side.is_file() else None,
            "board_jpg": _one(d, f"{camera}_image_*.jpg", ctx)}


def experiment_records(set_dir: Path, ctx: Ctx) -> dict[str, dict]:
    """experiment.json capture records keyed by file basename (output_path is a Pi path)."""
    path = set_dir / "experiment.json"
    if not path.is_file():
        ctx.warn(f"{set_dir.name}: no experiment.json")
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        ctx.warn(f"{path}: unreadable ({exc})")
        return {}
    ctx.use(path)
    return {Path(r["output_path"]).name: r for key in ("captures", "raw_captures")
            for r in data.get(key) or [] if r.get("output_path")}


def read_json(path: Optional[Path], ctx: Ctx) -> dict:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        ctx.warn(f"{path}: unreadable sidecar ({exc})")
        return {}
    ctx.use(path)
    return data


# --- metrics + status ---------------------------------------------------------------------

def to_gray(bgr: np.ndarray) -> np.ndarray:
    return bgr if bgr.ndim == 2 else cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


def frame_metrics(gray: np.ndarray) -> dict[str, float]:
    """Luma metrics on an 8-bit gray image (see METHOD['scoring'])."""
    g = gray.astype(np.float32)
    lap = cv2.Laplacian(g, cv2.CV_32F)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    n = float(gray.size)
    return {"mean_luma": round(float(gray.mean(dtype=np.float64)), 3),
            "clip_hi_pct": round(100.0 * np.count_nonzero(gray >= CLIP_HI_LEVEL) / n, 4),
            "clip_lo_pct": round(100.0 * np.count_nonzero(gray <= CLIP_LO_LEVEL) / n, 4),
            "sharpness_lapvar": round(float(lap.var(dtype=np.float64)), 3),
            "tenengrad": round(float(np.mean(gx * gx + gy * gy, dtype=np.float64)), 3)}


def classify(mean_luma: float, clip_hi_pct: float) -> tuple[str, str]:
    """PASS / WARN / FAIL from mean luma (0..255) and % of pixels with luma >= 250."""
    clip = f"clip_hi {clip_hi_pct:.2f} %"
    fails = [f"{clip} > {FAIL_CLIP_HI_PCT:g} %"] if clip_hi_pct > FAIL_CLIP_HI_PCT else []
    fails += [f"mean {mean_luma:.1f} < {FAIL_MEAN:g}"] if mean_luma < FAIL_MEAN else []
    if fails:
        return "FAIL", "; ".join(fails)
    warns = [f"{clip} > {WARN_CLIP_HI_PCT:g} %"] if clip_hi_pct > WARN_CLIP_HI_PCT else []
    warns += [f"mean {mean_luma:.1f} < {WARN_MEAN_LO:g}"] if mean_luma < WARN_MEAN_LO else []
    warns += [f"mean {mean_luma:.1f} > {WARN_MEAN_HI:g}"] if mean_luma > WARN_MEAN_HI else []
    return ("WARN", "; ".join(warns)) if warns else ("PASS", "")


def sharpest_block(gray: np.ndarray, size: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the block with the highest Laplacian variance, on a size/4 grid."""
    h, w = gray.shape
    bw, bh = min(size, w), min(size, h)
    lap = cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F)
    step = max(size // 4, 1)
    xs = sorted(set(range(0, w - bw + 1, step)) | {w - bw})
    ys = sorted(set(range(0, h - bh + 1, step)) | {h - bh})
    best = max(((float(lap[y:y + bh, x:x + bw].var()), x, y) for y in ys for x in xs))
    return best[1], best[2], bw, bh


# --- sRGB encode + renders ------------------------------------------------------------------

def _srgb_oetf(x: np.ndarray) -> np.ndarray:
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


_LUT16 = np.round(_srgb_oetf(np.arange(65536, dtype=np.float64) / 65535) * 65535).astype(np.uint16)


def encode_srgb16(linear: np.ndarray) -> np.ndarray:
    """Linear RGB (clipped to 0..1) -> 16-bit sRGB through a 65536-entry LUT."""
    idx = (np.clip(linear, 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)
    return _LUT16[idx]


def to8(srgb16: np.ndarray) -> np.ndarray:
    return ((srgb16.astype(np.uint32) + 128) // 257).astype(np.uint8)


def rotate_ccw(img: np.ndarray, degrees, ctx: Ctx, what: str) -> np.ndarray:
    deg = int(degrees or 0)
    if deg % 90:
        ctx.warn(f"{what}: mount_rotation_deg={degrees} is not a multiple of 90; not rotated")
        return img
    return np.ascontiguousarray(np.rot90(img, k=(deg // 90) % 4))


def gray_world_gains(rgb: np.ndarray) -> np.ndarray:
    """(r, g, b) gains making the frame mean neutral, ignoring near-clipped pixels."""
    ok = (rgb < 0.98).all(axis=2)
    pixels = rgb[ok] if ok.any() else rgb.reshape(-1, 3)
    means = np.maximum(pixels.mean(axis=0, dtype=np.float64), 1e-6)
    return (means[1] / means).astype(np.float32)


def render_openmv(bayer: Path, ctx: Ctx) -> tuple[np.ndarray, dict]:
    """Bayer + sidecar -> (16-bit sRGB RGB array, info). See METHOD['openmv']."""
    frame = read_openmv_bayer(bayer)
    linear, _sat, cfa = normalize(frame)
    rgb = demosaic_bilinear(linear, cfa)
    gains = gray_world_gains(rgb)
    srgb16 = encode_srgb16(rgb * gains)
    rot = frame.source.get("mount_rotation_deg")
    srgb16 = rotate_ccw(srgb16, rot, ctx, bayer.name)
    return srgb16, {"cfa": cfa, "gray_world_gains": [round(float(g), 4) for g in gains],
                    "mount_rotation_deg": rot, "awb": "Mac gray-world AWB"}


def dng_ccm(color_matrix, as_shot_wb) -> Optional[np.ndarray]:
    """White-balanced camera RGB -> linear sRGB from rpicam's DNG ColorMatrix1 (XYZ -> camera,
    = inv(RGB2XYZ . CCM . diag(WB))) and AsShotNeutral-derived WB. None if either is absent."""
    if color_matrix is None or as_shot_wb is None:
        return None
    inv = np.linalg.inv(np.asarray(color_matrix, dtype=np.float64))
    return XYZ_TO_SRGB @ inv @ np.diag(1.0 / np.asarray(as_shot_wb, dtype=np.float64))


def apply_wb_clip(rgb: np.ndarray, wb) -> np.ndarray:
    """In place: x WB per channel, then clip at 1.0 (sensor white in G-relative units).

    Without the clip a saturated pixel (raw 1, 1, 1) becomes (r, 1, b) after WB and renders
    magenta; clipping makes it white (dcraw's default highlight 'clip' mode)."""
    rgb *= np.asarray(wb, dtype=np.float32)
    np.minimum(rgb, 1.0, out=rgb)
    return rgb


def imx708_matrix(digital_gain: float, ccm: Optional[np.ndarray]) -> np.ndarray:
    """The 3x3 applied to white-balanced camera RGB: CCM x digital gain."""
    return (ccm if ccm is not None else np.eye(3)) * float(digital_gain)


def imx708_settings(meta: dict, frame, ctx: Ctx, what: str) -> dict:
    """WB / digital gain / CCM for the DNG render, with provenance + cross-checks."""
    dng_wb = frame.as_shot_wb
    if meta.get("ColourGains"):
        r, b = meta["ColourGains"]
        wb, wb_src = (float(r), 1.0, float(b)), "rpicam.json ColourGains"
        if dng_wb and max(abs(wb[0] / dng_wb[0] - 1), abs(wb[2] / dng_wb[2] - 1)) > 0.01:
            ctx.warn(f"{what}: ColourGains {wb} vs DNG AsShotNeutral WB {dng_wb} differ > 1 %")
    elif dng_wb:
        wb, wb_src = tuple(dng_wb), "DNG AsShotNeutral (no rpicam ColourGains)"
        ctx.warn(f"{what}: no rpicam ColourGains; WB from DNG AsShotNeutral")
    else:
        wb, wb_src = (1.0, 1.0, 1.0), "none"
        ctx.warn(f"{what}: no WB available; rendered without white balance")
    dg = meta.get("DigitalGain")
    if dg is None:
        ctx.warn(f"{what}: no DigitalGain in metadata; using 1.0")
    ccm = dng_ccm(frame.color_matrix, dng_wb)
    if ccm is None:
        ctx.warn(f"{what}: DNG has no ColorMatrix1/AsShotNeutral; rendered WITHOUT a CCM")
    info = {"wb": [round(v, 6) for v in wb], "wb_source": wb_src,
            "digital_gain": float(dg or 1.0),
            "ccm_source": "DNG ColorMatrix1 + AsShotNeutral" if ccm is not None else "none",
            "ccm": None if ccm is None else np.round(ccm, 6).tolist(),
            "black_level": list(frame.black_level), "white_level": frame.white_level,
            "cfa": frame.cfa}
    if ccm is not None and meta.get("ColourCorrectionMatrix"):
        ref = np.asarray(meta["ColourCorrectionMatrix"], dtype=np.float64).reshape(3, 3)
        info["ccm_max_abs_diff_vs_rpicam_json"] = round(float(np.abs(ccm - ref).max()), 6)
    return {"wb": wb, "dg": float(dg or 1.0), "ccm": ccm, "info": info}


def render_imx708_dng(dng: Path, meta: dict, ctx: Ctx) -> tuple[np.ndarray, dict]:
    """DNG -> (16-bit sRGB RGB array, info). See METHOD['imx708_dng']."""
    frame = read_dng(dng)
    s = imx708_settings(meta, frame, ctx, dng.name)
    linear, _sat, cfa = normalize(frame)
    rgb = apply_wb_clip(demosaic_bilinear(linear, cfa), s["wb"])
    del linear
    m = imx708_matrix(s["dg"], s["ccm"]).astype(np.float32)
    out = cv2.transform(rgb, m)  # per-pixel 3x3 (channel order RGB in, RGB out)
    del rgb
    return encode_srgb16(out), s["info"]


def production_crop(img: np.ndarray) -> np.ndarray:
    """The Spotter production region of a full-native 4608x2592 frame (raises otherwise)."""
    h, w = img.shape[:2]
    if (w, h) != NATIVE_IMX708:
        raise ValueError(f"source is {w}x{h}, production crop needs "
                         f"{NATIVE_IMX708[0]}x{NATIVE_IMX708[1]}")
    x, y, cw, ch = PROD_CROP
    return img[y:y + ch, x:x + cw]


# --- writers --------------------------------------------------------------------------------

def write_png(ctx: Ctx, path: Path, bgr_or_gray: np.ndarray, kind: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), bgr_or_gray):
        raise IOError(f"cv2.imwrite failed: {path}")
    return ctx.wrote(path, kind, bgr_or_gray.shape)


def write_jpg(ctx: Ctx, path: Path, bgr: np.ndarray, kind: str, quality: int = 95) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), bgr, [cv2.IMWRITE_JPEG_QUALITY, quality]):
        raise IOError(f"cv2.imwrite failed: {path}")
    return ctx.wrote(path, kind, bgr.shape)


def write_tiff16(ctx: Ctx, path: Path, rgb16: np.ndarray, kind: str) -> Path:
    import tifffile

    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, rgb16, photometric="rgb", compression="zlib",
                     compressionargs={"level": 1})
    return ctx.wrote(path, kind, rgb16.shape)


def rgb_to_bgr(rgb: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(rgb[..., ::-1])


# --- caption bars (see host_tools/pool_caption.py) ------------------------------------------

def set_caption(ctx: Ctx, run: Path, set_dir: Path) -> pc.SetCaption:
    """Caption values for a set (time / depth / water), read once per run and set."""
    if run not in ctx.run_meta:
        ctx.run_meta[run] = pc.load_run(run, ctx.warn)
    if set_dir not in ctx.captions:
        ctx.captions[set_dir] = pc.set_caption(ctx.run_meta[run], set_dir, ctx.warn)
    return ctx.captions[set_dir]


def captioned_path(path: Path) -> Path:
    """``<stem>_captioned.png`` next to a PNG, ``<stem>_captioned.jpg`` next to a JPEG."""
    ext = ".jpg" if path.suffix.lower() in (".jpg", ".jpeg") else ".png"
    return path.with_name(f"{path.stem}_captioned{ext}")


def write_captioned(ctx: Ctx, path: Path, captioned: np.ndarray, kind: str) -> Path:
    """Write an already captioned array next to ``path`` (the uncaptioned output)."""
    dst = captioned_path(path)
    if dst.suffix == ".jpg":
        return write_jpg(ctx, dst, captioned, f"{kind}_captioned", 95)
    return write_png(ctx, dst, captioned, f"{kind}_captioned")


# --- pass 1: score every set ----------------------------------------------------------------

def _set_out(ctx: Ctx, run: Path, set_dir: Path) -> Path:
    return ctx.out / run.name / set_dir.name


def _base_row(run: Path, set_dir: Path, camera: str) -> dict:
    return {"run": run.name, "set": set_dir.name, "set_rel": str(set_dir.relative_to(run)),
            "camera": camera, "status": "FAIL", "reason": "", "selected_rank": "",
            "rendered": False, "_set_dir": set_dir, "_run": run}


def _imx708_meta(files: dict, role: str, records: dict, ctx: Ctx) -> dict:
    meta = read_json(files.get(f"{role}_meta"), ctx)
    if not meta and files.get(role) is not None:
        meta = (records.get(files[role].name) or {}).get("sensor_metadata") or {}
    return meta


def score_imx708(ctx: Ctx, row: dict, files: dict,
                 records: dict) -> tuple[dict, Optional[np.ndarray]]:
    """Score the rpicam JPEG paired with the DNG (fallback: the auto still)."""
    role = "raw_jpg" if files.get("raw_jpg") else "still_jpg" if files.get("still_jpg") else None
    if files.get("dng"):
        row["raw_file"], row["raw_bytes"] = files["dng"].name, files["dng"].stat().st_size
    if role is None:
        row["reason"] = "no rpicam JPEG (imx708_raw_*.jpg / imx708_image_*.jpg)"
        return row, None
    path = files[role]
    bgr = cv2.imread(str(path), JPEG_FLAGS)
    row.update(scored_role=role, file=path.name, bytes=path.stat().st_size, _path=path)
    if bgr is None:
        row["reason"] = f"unreadable {path.name}"
        return row, None
    ctx.use(path, records)
    meta = _imx708_meta(files, role, records, ctx)
    row.update(width=bgr.shape[1], height=bgr.shape[0], exposure_us=meta.get("ExposureTime"),
               analogue_gain=meta.get("AnalogueGain"), digital_gain=meta.get("DigitalGain"),
               lens_position=meta.get("LensPosition"), lux=meta.get("Lux"),
               colour_gains=meta.get("ColourGains"))
    row.update(frame_metrics(to_gray(bgr)))
    row["status"], row["reason"] = classify(row["mean_luma"], row["clip_hi_pct"])
    return row, bgr


def score_openmv(ctx: Ctx, row: dict, files: dict, records: dict) -> dict:
    """Score this tool's Mac render of the Bayer (not the board JPEG)."""
    bayer = files.get("bayer")
    if bayer is None:
        row["reason"] = "no Bayer frame"
        return row
    row.update(scored_role="bayer", file=bayer.name, raw_file=bayer.name,
               bytes=bayer.stat().st_size, raw_bytes=bayer.stat().st_size, _path=bayer)
    try:
        srgb16, _info = render_openmv(bayer, ctx)
    except (ValueError, OSError, KeyError) as exc:
        row["reason"] = f"unreadable Bayer: {exc}"
        return row
    ctx.use(bayer, records)
    side = read_json(files.get("bayer_json"), ctx)
    row.update(width=srgb16.shape[1], height=srgb16.shape[0],
               exposure_us=side.get("exposure_us"), gain_db=side.get("gain_db"))
    row.update(frame_metrics(to_gray(rgb_to_bgr(to8(srgb16)))))
    row["status"], row["reason"] = classify(row["mean_luma"], row["clip_hi_pct"])
    return row


def crop_rows(ctx: Ctx, run: Path, set_dir: Path, files: dict, records: dict,
              decoded: dict[str, np.ndarray]) -> list[dict]:
    """Production crop of every rpicam JPEG in the set (cheap; done for every set)."""
    rows = []
    for role in ("raw_jpg", "still_jpg"):
        path = files.get(role)
        if path is None:
            continue
        bgr = decoded.get(role)
        if bgr is None:
            bgr = cv2.imread(str(path), JPEG_FLAGS)
        if bgr is None:
            ctx.warn(f"{set_dir.name}: {path.name} unreadable; no production crop")
            continue
        try:
            crop = production_crop(bgr)
        except ValueError as exc:
            ctx.warn(f"{set_dir.name}: {path.name}: {exc}; production crop skipped")
            continue
        ctx.use(path, records)
        stem = f"{path.stem}_prodcrop_{PROD_CROP[0]}_{PROD_CROP[1]}_{PROD_CROP[2]}x{PROD_CROP[3]}"
        out_dir = _set_out(ctx, run, set_dir)
        png = write_png(ctx, out_dir / f"{stem}.png", crop, "imx708_prod_crop_png")
        jpg = write_jpg(ctx, out_dir / f"{stem}.jpg", crop, "imx708_prod_crop_jpg_q95")
        cap = pc.add_caption(crop, set_caption(ctx, run, set_dir), "imx708",
                             f"production crop x{PROD_CROP[0]} y{PROD_CROP[1]} of {path.name}")
        cpng = write_captioned(ctx, png, cap, "imx708_prod_crop_png")
        cjpg = write_captioned(ctx, jpg, cap, "imx708_prod_crop_jpg_q95")
        m = frame_metrics(to_gray(crop))
        status, reason = classify(m["mean_luma"], m["clip_hi_pct"])
        meta = _imx708_meta(files, role, records, ctx)
        rows.append({"run": run.name, "set": set_dir.name, "source": path.name,
                     "source_bytes": path.stat().st_size,
                     "lens_position": meta.get("LensPosition"), **m, "status": status,
                     "reason": reason, "png": str(png.relative_to(ctx.out)),
                     "jpg": str(jpg.relative_to(ctx.out)),
                     "png_captioned": str(cpng.relative_to(ctx.out)),
                     "jpg_captioned": str(cjpg.relative_to(ctx.out)), "_png": png,
                     "_cap_png": cpng, "_cap_wh": (cap.shape[1], cap.shape[0])})
    return rows


def score_set(ctx: Ctx, run: Path, set_dir: Path) -> tuple[list[dict], list[dict]]:
    records = experiment_records(set_dir, ctx)
    rows, crops = [], []
    for camera in CAMERAS:
        files = locate_files(set_dir, camera, ctx)
        row = _base_row(run, set_dir, camera)
        if not any(v for k, v in files.items() if not k.endswith("_meta")):
            ctx.warn(f"{set_dir.name}: {camera} has no files (camera failed?); skipped")
            row["reason"] = "camera missing in set"
            rows.append(row)
            continue
        row["_files"], row["_records"] = files, records
        if camera == "imx708":
            row, bgr = score_imx708(ctx, row, files, records)
            decoded = {row["scored_role"]: bgr} if bgr is not None else {}
            crops = crop_rows(ctx, run, set_dir, files, records, decoded)
        else:
            row = score_openmv(ctx, row, files, records)
        rows.append(row)
    return rows, crops


# --- selection --------------------------------------------------------------------------------

def top_k(rows: list[dict], k: int) -> tuple[list[dict], bool]:
    """Top-k rows by sharpness among non-FAIL ones; (rows, fallback_all_fail)."""
    scored = [r for r in rows if r.get("sharpness_lapvar") is not None]
    ok = [r for r in scored if r["status"] != "FAIL"]
    pool, fallback = (ok, False) if ok else (scored, bool(scored))
    return sorted(pool, key=lambda r: -r["sharpness_lapvar"])[:k], fallback


def best_of(rows: list[dict]) -> Optional[dict]:
    """The single best row (as ``top_k``), with ``fallback_all_fail`` set on it."""
    picked, fallback = top_k(rows, 1)
    if not picked:
        return None
    picked[0]["fallback_all_fail"] = fallback
    return picked[0]


def _fallback_note(row: dict) -> str:
    return " (FALLBACK: every candidate FAILs)" if row.get("fallback_all_fail") else ""


# --- pass 2: renders + sheet assets ------------------------------------------------------------

@dataclass
class Tile:
    path: Path
    label: str
    w: int
    h: int


@dataclass
class Block:
    heading: str
    caption: list[str]
    tiles: list[Tile]


def _tile(ctx: Ctx, img_bgr: np.ndarray, name: str, label: str) -> Tile:
    path = write_png(ctx, ctx.out / "sheet" / name, img_bgr, "sheet_asset")
    return Tile(path, label, img_bgr.shape[1], img_bgr.shape[0])


def thumb_tile(ctx: Ctx, bgr: np.ndarray, width: int, name: str, what: str,
               bar_h: int = 0) -> Tile:
    """Scaled full frame; ``bar_h`` = rows of caption bar at the bottom of ``bgr``."""
    h, w = bgr.shape[:2]
    tw = min(width, w)
    th = max(1, round(h * tw / w))
    small = bgr if tw == w else cv2.resize(bgr, (tw, th), interpolation=cv2.INTER_AREA)
    bar = " + caption bar" if bar_h else ""
    return _tile(ctx, small, name,
                 f"{what}: full frame {w}x{h - bar_h}{bar}, scaled to {100 * tw / w:.1f} %")


def crop_tile(ctx: Ctx, bgr: np.ndarray, box: tuple, name: str, what: str,
              cap: Optional[tuple] = None) -> Tile:
    """1:1 array slice (no resampling); ``cap`` = (SetCaption, camera, source file name) appends
    a caption bar below the slice (the bar is not part of the crop)."""
    x, y, w, h = box
    crop = np.ascontiguousarray(bgr[y:y + h, x:x + w])
    if cap is None:
        return _tile(ctx, crop, name, f"{what}: 1:1 crop, native px {x},{y},{w},{h}")
    info, camera, src = cap
    tile = pc.add_caption(crop, info, camera, f"1:1 crop x{x} y{y} of {src}")
    return _tile(ctx, tile, name, f"{what}: 1:1 crop, native px {x},{y},{w},{h} "
                                  "(+ caption bar below, not part of the crop)")


def _fmt(v, nd=1) -> str:
    return "n/a" if v is None or v == "" else f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def caption(row: dict) -> list[str]:
    exp = f"exposure {_fmt(row.get('exposure_us'))} us"
    if row["camera"] == "imx708":
        exp += (f", AG {_fmt(row.get('analogue_gain'), 2)}, DG {_fmt(row.get('digital_gain'), 3)}"
                f", LensPosition {_fmt(row.get('lens_position'), 3)}")
    else:
        exp += f", gain {_fmt(row.get('gain_db'), 2)} dB"
    scored_on = ROLE_LABEL.get(row.get("scored_role"), "?")
    return [f"{row['run']} / {row['set_rel']}",
            f"{row.get('file')} ({row.get('bytes')} B; scored on {scored_on})",
            exp,
            f"sharpness {_fmt(row.get('sharpness_lapvar'))}, "
            f"tenengrad {_fmt(row.get('tenengrad'), 0)}, mean luma {_fmt(row.get('mean_luma'))}, "
            f"clip hi {_fmt(row.get('clip_hi_pct'), 2)} % / lo {_fmt(row.get('clip_lo_pct'), 2)} %",
            f"{row['status']}{(' - ' + row['reason']) if row.get('reason') else ''}"]


def render_imx708_row(ctx: Ctx, row: dict, rank: Optional[int]) -> Optional[Block]:
    files, records = row["_files"], row["_records"]
    set_out = _set_out(ctx, row["_run"], row["_set_dir"])
    cinfo = set_caption(ctx, row["_run"], row["_set_dir"])
    dng_bgr = None
    if files.get("dng"):
        meta = _imx708_meta(files, "raw_jpg", records, ctx)
        if not meta:
            ctx.warn(f"{row['set']}: no metadata paired with {files['dng'].name}")
        t0 = time.monotonic()
        try:
            srgb16, info = render_imx708_dng(files["dng"], meta, ctx)
        except (ValueError, OSError, KeyError) as exc:
            ctx.warn(f"{row['set']}: DNG render failed for {files['dng'].name}: {exc}")
            srgb16 = None
        if srgb16 is not None:
            ctx.use(files["dng"], records)
            stem = f"{files['dng'].stem}_mac_srgb"
            write_tiff16(ctx, set_out / f"{stem}_16bit.tif", srgb16, "imx708_dng_render_tiff16")
            dng_bgr = rgb_to_bgr(to8(srgb16))
            del srgb16
            png = write_png(ctx, set_out / f"{stem}_8bit.png", dng_bgr, "imx708_dng_render_png8")
            write_captioned(ctx, png, pc.add_caption(dng_bgr, cinfo, "imx708", files["dng"].name),
                            "imx708_dng_render_png8")
            secs = round(time.monotonic() - t0, 2)
            ctx.timings["dng_render_s"][f"{row['run']}/{row['set']}"] = secs
            ctx.timings.setdefault("dng_render_info", {})[f"{row['run']}/{row['set']}"] = info
            row["rendered"] = True
            LOG.info("    imx708 DNG render %s: %.2f s", files["dng"].name, secs)
    else:
        ctx.warn(f"{row['set']}: imx708 has no DNG; nothing to render")
    if rank is None or row.get("_path") is None:
        return None
    bgr = cv2.imread(str(row["_path"]), JPEG_FLAGS)
    box = sharpest_block(to_gray(bgr), BLOCK["imx708"])
    pre, jpg_name = f"imx708_r{rank}", row["_path"].name
    # The rpicam JPEG is an original (never copied); its captioned copy is what the sheet shows.
    cap = pc.add_caption(bgr, cinfo, "imx708", jpg_name)
    write_captioned(ctx, set_out / jpg_name, cap, "imx708_rpicam_jpeg")
    tiles = [thumb_tile(ctx, cap, THUMB_W["imx708"], f"{pre}_full.png", "rpicam JPEG",
                        bar_h=cap.shape[0] - bgr.shape[0]),
             crop_tile(ctx, bgr, box, f"{pre}_crop_jpeg.png", "rpicam JPEG",
                       (cinfo, "imx708", jpg_name))]
    del cap
    if dng_bgr is not None and dng_bgr.shape[:2] == bgr.shape[:2]:
        tiles.append(crop_tile(ctx, dng_bgr, box, f"{pre}_crop_dng.png", "Mac DNG render",
                               (cinfo, "imx708", files["dng"].name)))
    elif dng_bgr is not None:
        ctx.warn(f"{row['set']}: DNG render {dng_bgr.shape[1]}x{dng_bgr.shape[0]} != JPEG; "
                 "no same-coordinate DNG crop")
    return Block(f"imx708 #{rank}", caption(row), tiles)


def render_openmv_row(ctx: Ctx, row: dict, rank: Optional[int]) -> Optional[Block]:
    files, camera = row["_files"], row["camera"]
    set_out = _set_out(ctx, row["_run"], row["_set_dir"])
    cinfo = set_caption(ctx, row["_run"], row["_set_dir"])
    srgb16, info = render_openmv(files["bayer"], ctx)
    stem = f"{files['bayer'].stem}_mac_grayworld"
    bgr = rgb_to_bgr(to8(srgb16))
    png = write_png(ctx, set_out / f"{stem}_8bit.png", bgr, f"{camera}_bayer_render_png8")
    cap = pc.add_caption(bgr, cinfo, camera, files["bayer"].name)
    write_captioned(ctx, png, cap, f"{camera}_bayer_render_png8")
    write_tiff16(ctx, set_out / f"{stem}_16bit.tif", srgb16, f"{camera}_bayer_render_tiff16")
    board, board_cap = files.get("board_jpg"), None
    if board is not None:
        dst = set_out / f"{board.stem}_board_isp_render.jpg"
        shutil.copy2(board, dst)
        ctx.use(board, row["_records"])
        ctx.wrote(dst, f"{camera}_board_isp_jpeg_copy")
        board_bgr = cv2.imread(str(board), JPEG_FLAGS)
        if board_bgr is None:
            ctx.warn(f"{row['set']}: {board.name} unreadable; no captioned board JPEG")
        else:
            board_cap = pc.add_caption(board_bgr, cinfo, camera, board.name)
            write_captioned(ctx, dst, board_cap, f"{camera}_board_isp_jpeg")
            board_bar = board_cap.shape[0] - board_bgr.shape[0]
    else:
        ctx.warn(f"{row['set']}: {camera} has no board JPEG to copy")
    row["rendered"] = True
    ctx.timings.setdefault("openmv_render_info", {})[f"{row['run']}/{row['set']}/{camera}"] = info
    if rank is None:
        return None
    pre, what = f"{camera}_r{rank}", "Mac gray-world AWB render"
    tiles = [thumb_tile(ctx, cap, THUMB_W[camera], f"{pre}_full.png", what,
                        bar_h=cap.shape[0] - bgr.shape[0]),
             crop_tile(ctx, bgr, sharpest_block(to_gray(bgr), BLOCK[camera]), f"{pre}_crop.png",
                       what, (cinfo, camera, files["bayer"].name))]
    if board_cap is not None:
        tiles.append(thumb_tile(ctx, board_cap, THUMB_W[camera], f"{pre}_board_full.png",
                                "board ISP render (board JPEG, auto AWB)", bar_h=board_bar))
    return Block(f"{camera} #{rank}", caption(row), tiles)


def render_rows(ctx: Ctx, rows: list[dict], selected: dict[str, list[dict]],
                render_all: bool) -> dict[str, list[Block]]:
    ranks = {id(r): i + 1 for picks in selected.values() for i, r in enumerate(picks)}
    for r in rows:
        r["selected_rank"] = ranks.get(id(r), "")
    todo = [r for r in rows if (render_all or id(r) in ranks) and r.get("_files")]
    blocks: dict[str, list[Block]] = {c: [] for c in CAMERAS}
    for i, r in enumerate(todo, 1):
        LOG.info("  render %d/%d %s %s%s", i, len(todo), r["set"], r["camera"],
                 f" (top-{ranks[id(r)]})" if id(r) in ranks else "")
        rank = ranks.get(id(r))
        try:
            if r["camera"] == "imx708":
                block = render_imx708_row(ctx, r, rank)
            elif r.get("_path") is not None:
                block = render_openmv_row(ctx, r, rank)
            else:
                block = None
        except (ValueError, OSError, KeyError) as exc:
            ctx.warn(f"{r['set']}: {r['camera']} render failed: {exc}")
            block = None
        if block is not None:
            blocks[r["camera"]].append(block)
    for c in CAMERAS:
        blocks[c].sort(key=lambda b: b.heading)
    return blocks


# --- Spotter comparison --------------------------------------------------------------------

def find_spotter_units(root: Path) -> dict[str, list[Path]]:
    """{unit: JPEGs} for folders under ``root`` whose name starts with bmcam<digits>."""
    units: dict[str, list[Path]] = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        m = re.match(r"^(bmcam\d+)", d.name)
        if m:
            jpgs = [p for p in d.rglob("*")
                    if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")]
            units.setdefault(m.group(1), []).extend(sorted(jpgs))
    return units


def spotter_sidecar(jpg: Path) -> dict:
    side = jpg.with_name(f"{jpg.stem}.metadata.json")
    try:
        return json.loads(side.read_text()) if side.is_file() else {}
    except (OSError, ValueError):
        return {}


def spotter_crop(jpg: Path) -> tuple[Optional[np.ndarray], str]:
    """(production crop or None, note). 1600x900 only when ScalerCrop proves it is native."""
    from PIL import Image

    with Image.open(jpg) as im:
        size = im.size
    if size == NATIVE_IMX708:
        bgr = cv2.imread(str(jpg), JPEG_FLAGS)
        if bgr is None:
            return None, "unreadable"
        return production_crop(bgr), "cropped from 4608x2592"
    if size == PROD_CROP[2:]:
        sc = str(spotter_sidecar(jpg).get("ScalerCrop", ""))
        if sc.replace(" ", "") == f"({PROD_CROP[0]},{PROD_CROP[1]})/{PROD_CROP[2]}x{PROD_CROP[3]}":
            bgr = cv2.imread(str(jpg), JPEG_FLAGS)
            return bgr, "native 1600x900 (ScalerCrop verified)"
        return None, f"1600x900 with ScalerCrop {sc or 'unknown'} (not proven native crop)"
    return None, f"size {size[0]}x{size[1]}"


def spotter_best(ctx: Ctx, root: Path) -> tuple[list[dict], list[str]]:
    """Best production crop per Spotter unit (read only under ``root``)."""
    notes, best = [], []
    units = find_spotter_units(root)
    if not units:
        return [], [f"no Spotter files found under {root}"]
    for unit, jpgs in units.items():
        cands, skipped = [], {}
        for jpg in jpgs:
            crop, note = spotter_crop(jpg)
            if crop is None:
                skipped[note] = skipped.get(note, 0) + 1
                continue
            ctx.use(jpg)
            m = frame_metrics(to_gray(crop))
            status, reason = classify(m["mean_luma"], m["clip_hi_pct"])
            cands.append({"unit": unit, "path": jpg, "bytes": jpg.stat().st_size, **m,
                          "status": status, "reason": reason, "note": note,
                          "meta": spotter_sidecar(jpg)})
        for note, n in skipped.items():
            notes.append(f"{unit}: skipped {n} JPEG(s): {note}")
        LOG.info("  spotter %s: %d JPEG(s), %d usable", unit, len(jpgs), len(cands))
        pick = best_of(cands)
        if pick is None:
            notes.append(f"{unit}: no usable 4608x2592 JPEG under {root}")
            continue
        crop, _note = spotter_crop(pick["path"])  # re-decode the winner only (memory)
        out = ctx.out / "spotter" / f"{unit}__{pick['path'].stem}_prodcrop.png"
        pick["png"] = write_png(ctx, out, crop, "spotter_prod_crop_png")
        src = pick["path"].name
        cap = pc.add_caption(crop, pc.unknown_caption(f"Spotter {unit} file"), f"IMX708 {unit}",
                             src if pick["note"].startswith("native") else
                             f"production crop x{PROD_CROP[0]} y{PROD_CROP[1]} of {src}")
        pick["cap_png"] = write_captioned(ctx, out, cap, "spotter_prod_crop_png")
        pick["cap_wh"] = (cap.shape[1], cap.shape[0])
        best.append(pick)
    return best, notes


def spotter_section(ctx: Ctx, crops: list[dict], spotter: list[dict], notes: list[str],
                    root: Optional[Path], rig_lens: list) -> tuple[list[str], list[Block]]:
    lens = sorted({round(v, 3) for v in rig_lens if isinstance(v, (int, float))})
    head = ["Production frame = native px x=1504 y=846 w=1600 h=900 of the 4608x2592 frame; "
            "all crops here are 1:1 (no resampling).",
            "Spotter units: lens 0.8 manual. This rig: LensPosition from its rpicam metadata "
            f"= {lens if lens else 'n/a'} (as the camera reported it: manual when the run's "
            "profile locks it, AF otherwise; per frame in the captions)."]
    blocks = []
    ours = best_of(crops)
    if ours is not None:
        blocks.append(Block("this rig: best IMX708 production crop",
                            [f"{ours['run']} / {ours['set']} / {ours['source']} "
                             f"({ours['source_bytes']} B)",
                             f"LensPosition {_fmt(ours.get('lens_position'), 3)}, sharpness "
                             f"{_fmt(ours['sharpness_lapvar'])}, mean {_fmt(ours['mean_luma'])}, "
                             f"clip hi {_fmt(ours['clip_hi_pct'], 2)} %, {ours['status']}"
                             f"{_fallback_note(ours)}"],
                            [Tile(ours["_cap_png"], "rig IMX708: 1:1 production crop "
                                  "(+ caption bar below)", *ours["_cap_wh"])]))
    else:
        head.append("this rig: no 4608x2592 IMX708 JPEG to crop")
    if root is None:
        return head + ["no --spotter-root given"], blocks
    head += notes
    for s in spotter:
        meta = s["meta"]
        blocks.append(Block(f"Spotter {s['unit']}: best production crop",
                            [f"{s['path']} ({s['bytes']} B; {s['note']})",
                             f"LensPosition {_fmt(meta.get('LensPosition'), 3)}, exposure "
                             f"{_fmt(meta.get('ExposureTime'))} us, "
                             f"AG {_fmt(meta.get('AnalogueGain'), 2)}, "
                             f"sharpness {_fmt(s['sharpness_lapvar'])}, "
                             f"mean {_fmt(s['mean_luma'])}, "
                             f"clip hi {_fmt(s['clip_hi_pct'], 2)} %, {s['status']}"
                             f"{_fallback_note(s)}"],
                            [Tile(s["cap_png"], f"{s['unit']}: 1:1 production crop "
                                  "(+ caption bar below)", *s["cap_wh"])]))
    if not spotter:
        head.append(f"no Spotter files found under {root}")
    return head, blocks


# --- cut sheet (HTML + PNG from the same blocks) -------------------------------------------

Section = tuple[str, list[str], list[Block]]


def sheet_html(ctx: Ctx, title: list[str], sections: list[Section]) -> Path:
    e = html.escape
    parts = ["<!doctype html><html><head><meta charset='utf-8'><title>Pool best images</title>",
             "<style>body{font:14px -apple-system,Helvetica,sans-serif;margin:16px;background:#fff;"
             "color:#111}h1{font-size:20px}h2{margin-top:28px;border-top:2px solid #333;"
             "padding-top:8px}.row{display:flex;gap:12px;align-items:flex-start}"
             ".tile p{margin:2px 0;"
             "font-size:12px;color:#444}.cap{font-family:monospace;font-size:12px;margin:6px 0}"
             "img{display:block;max-width:none;height:auto}.PASS{color:#070}.WARN{color:#a60}"
             ".FAIL{color:#b00}</style></head><body>",
             f"<h1>{e(title[0])}</h1>"] + [f"<div class='cap'>{e(t)}</div>" for t in title[1:]]
    for name, notes, blocks in sections:
        parts.append(f"<h2>{e(name)}</h2>")
        parts += [f"<div class='cap'>{e(n)}</div>" for n in notes]
        for b in blocks:
            parts.append(f"<h3>{e(b.heading)}</h3>")
            parts += [f"<div class='cap'>{e(c)}</div>" for c in b.caption]
            parts.append("<div class='row'>")
            for t in b.tiles:
                src = t.path.relative_to(ctx.out).as_posix()
                parts.append(f"<div class='tile'><p>{e(t.label)}</p><img src='{e(src)}' "
                             f"width='{t.w}' height='{t.h}' alt='{e(t.label)}'></div>")
            parts.append("</div>")
    parts.append("</body></html>")
    path = ctx.out / "best_images.html"
    path.write_text("\n".join(parts))
    return ctx.wrote(path, "cut_sheet_html")


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow without a scalable default font
        return ImageFont.load_default()


def sheet_png(ctx: Ctx, title: list[str], sections: list[Section]) -> Path:
    """Same content as the HTML, composited at 1:1 (tiles are pasted, never resampled)."""
    from PIL import Image, ImageDraw

    f_big, f_txt, line, gap, margin = _font(28), _font(18), 24, 16, 20
    items: list[tuple[str, Any]] = [("big", title[0])] + [("txt", t) for t in title[1:]]
    for name, notes, blocks in sections:
        items += [("gap", None), ("big", name)] + [("txt", n) for n in notes]
        for b in blocks:
            items += [("gap", None), ("big", b.heading)] + [("txt", c) for c in b.caption]
            items.append(("tiles", b.tiles))
    width, height = 1200, margin
    for kind, val in items:
        if kind == "tiles":
            width = max(width, margin * 2 + sum(t.w for t in val) + gap * (len(val) - 1))
            height += line + max(t.h for t in val) + gap
        else:
            height += {"big": 36, "txt": line, "gap": gap}[kind]
    canvas = Image.new("RGB", (width, height + margin), "white")
    draw, y = ImageDraw.Draw(canvas), margin
    for kind, val in items:
        if kind == "tiles":
            x = margin
            for t in val:
                draw.text((x, y), t.label, fill=(60, 60, 60), font=f_txt)
                with Image.open(t.path) as im:
                    canvas.paste(im.convert("RGB"), (x, y + line))
                x += t.w + gap
            y += line + max(t.h for t in val) + gap
        elif kind == "gap":
            y += gap
        else:
            draw.text((margin, y), val, fill=(0, 0, 0), font=f_big if kind == "big" else f_txt)
            y += 36 if kind == "big" else line
    path = ctx.out / "best_images.png"
    canvas.save(path, optimize=False, compress_level=6)
    return ctx.wrote(path, "cut_sheet_png", (canvas.height, canvas.width))


# --- CSV / manifest -----------------------------------------------------------------------------

def write_csv(ctx: Ctx, path: Path, rows: list[dict], fields: list[str], kind: str) -> Path:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    return ctx.wrote(path, kind)


def resolve_out(runs: list[Path], out: Optional[Path], spotter_root: Optional[Path]) -> Path:
    """Default ``<first run parent>/<first run name>_render``; never inside a run folder or a
    Spotter unit folder (raw captures are evidence)."""
    out = (out or runs[0].parent / f"{runs[0].name}_render").expanduser().resolve()
    for run in runs:
        if out == run or out.is_relative_to(run):
            raise ValueError(f"--out {out} is inside run folder {run}; raw data is evidence")
    if spotter_root is not None:
        units = [p for p in spotter_root.iterdir()
                 if p.is_dir() and re.match(r"^bmcam\d+", p.name)]
        for unit in units:
            if out == unit.resolve() or out.is_relative_to(unit.resolve()):
                raise ValueError(f"--out {out} is inside Spotter unit folder {unit} (read only)")
    return out


def run_inputs(ctx: Ctx, run: Path) -> None:
    for name in ("run_manifest.json", "sets.jsonl", "soak.jsonl", "pool.log"):
        if (run / name).is_file():
            ctx.use(run / name)


def title_lines(runs, n_sets, rows, seconds, args, run_meta=None) -> list[str]:
    per_cam = []
    for c in CAMERAS:
        cam = [r for r in rows if r["camera"] == c]
        ok = sum(r["status"] != "FAIL" for r in cam)
        per_cam.append(f"{c} {ok}/{len(cam)} OK (PASS/WARN)")
    git = git_state(REPO)
    return [f"Pool best images: {', '.join(r.name for r in runs)}",
            f"{n_sets} set(s); " + "; ".join(per_cam),
            f"top-K {args.top_k}; render time {seconds:.1f} s; generated "
            f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}; git {git['sha'][:10]}"
            f"{' (dirty)' if git.get('dirty') else ''}",
            "'scaled to NN %' = resized for display; '1:1 crop' = native pixels, no resampling "
            "(1 image px per CSS px at 100 % browser zoom).",
            "IMX708 Mac DNG render: no lens shading / denoise / sharpening / tone curve - corners "
            "darker and contrast flatter than the rpicam JPEG by design.",
            f"Status legend (luma of the scored image): FAIL = clip_hi > {FAIL_CLIP_HI_PCT:g} % "
            f"(luma >= {CLIP_HI_LEVEL}) or mean < {FAIL_MEAN:g} or unreadable; WARN = clip_hi > "
            f"{WARN_CLIP_HI_PCT:g} % or mean < {WARN_MEAN_LO:g} or > {WARN_MEAN_HI:g}; else PASS.",
            "Caption bar (appended below each image, never over it): capture time Pacific "
            "(Pi clock or Mac-anchored), depth = Bar30 fresh-water pressure proxy vs the "
            "arm-time surface (else 'vs run start'), water temp = Bar30, camera, image W x H "
            "px, source file; 'n/a' = not recorded."] + [
            f"Caption times {m.run.name}: {'; '.join(m.notes)}"
            for m in (run_meta or {}).values()]


# --- main ----------------------------------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(prog="python -m host_tools.pool_render",
                                description=__doc__.split("\n\n")[0])
    p.add_argument("runs", nargs="+", type=Path, help="pulled pool run folder(s)")
    p.add_argument("--out", type=Path, help="output folder (default <run parent>/<run>_render)")
    p.add_argument("--spotter-root", type=Path, help="folder with bmcam*/ unit folders (read only)")
    p.add_argument("--top-k", type=int, default=3, help="best frames per camera (default 3)")
    p.add_argument("--all", action="store_true", help="render every set, not only the top-K")
    args = p.parse_args(argv)
    if args.top_k < 1:
        p.error("--top-k must be >= 1")
    return args


def setup_logging(out: Path) -> logging.Handler:
    LOG.setLevel(logging.INFO)
    LOG.handlers.clear()
    LOG.propagate = False
    fmt = logging.Formatter("%(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    fileh = logging.FileHandler(out / "render.log", mode="w")
    fileh.setFormatter(fmt)
    LOG.addHandler(stream)
    LOG.addHandler(fileh)
    return fileh


def teardown_logging() -> None:
    for h in list(LOG.handlers):
        LOG.removeHandler(h)
        h.close()


def _set_summary(rows: list[dict]) -> str:
    return ", ".join(f"{r['camera']} {r['status']}"
                     + (f" sharp={r['sharpness_lapvar']:.0f} mean={r['mean_luma']:.0f}"
                        if r.get("sharpness_lapvar") is not None else f" ({r['reason']})")
                     for r in rows)


def main(argv=None) -> int:
    args = parse_args(argv)
    t_start = time.monotonic()
    runs = [r.expanduser().resolve() for r in args.runs]
    missing = [r for r in runs if not r.is_dir()]
    if missing:
        print(f"ERROR: run folder(s) not found: {missing}", file=sys.stderr)
        return 2
    root = args.spotter_root.expanduser().resolve() if args.spotter_root else None
    if root is not None and not root.is_dir():
        print(f"ERROR: --spotter-root {root} is not a folder", file=sys.stderr)
        return 2
    try:
        out = resolve_out(runs, args.out, root)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        pc.font(pc.MIN_FONT_PX)  # caption font: fail before the run, not after 90 s of work
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    setup_logging(out)
    try:
        return _run(Ctx(out), args, argv, runs, root, t_start)
    finally:
        teardown_logging()


def _run(ctx: Ctx, args, argv, runs: list[Path], root: Optional[Path], t_start: float) -> int:
    """Score -> select -> render -> Spotter -> CSV + sheet + manifest. Returns the exit code."""
    out = ctx.out
    LOG.info("pool_render: runs=%s", [str(r) for r in runs])
    LOG.info("  out=%s spotter_root=%s top_k=%d all=%s", out, root, args.top_k, args.all)
    if root is not None and out.is_relative_to(root):
        ctx.warn(f"output folder is inside --spotter-root {root} (outside its bmcam* unit folders)")

    for run in runs:
        ctx.run_meta[run] = pc.load_run(run, ctx.warn)
        LOG.info("  captions %s: %s", run.name, "; ".join(ctx.run_meta[run].notes))
    sets = [(run, s) for run in runs for s in discover_sets(run)]
    LOG.info("found %d set(s)", len(sets))
    rows, crops = [], []
    for i, (run, set_dir) in enumerate(sets, 1):
        run_inputs(ctx, run)
        t0 = time.monotonic()
        set_rows, set_crops = score_set(ctx, run, set_dir)
        rows += set_rows
        crops += set_crops
        secs = round(time.monotonic() - t0, 2)
        ctx.timings["per_set_s"][f"{run.name}/{set_dir.name}"] = secs
        LOG.info("[%d/%d] %s: %s; %d prod crop(s) (%.2f s)", i, len(sets), set_dir.name,
                 _set_summary(set_rows), len(set_crops), secs)
    if not sets:
        LOG.error("ERROR: no set folders (<run>/<date>/exp_*) found in %s", [str(r) for r in runs])

    selected, fallback = {}, {}
    for c in CAMERAS:
        selected[c], fallback[c] = top_k([r for r in rows if r["camera"] == c], args.top_k)
        if fallback[c]:
            ctx.warn(f"{c}: every frame FAILs; showing the best FAIL frames (fallback_all_fail)")
    t0 = time.monotonic()
    blocks = render_rows(ctx, rows, selected, args.all)
    ctx.timings["render_s"] = round(time.monotonic() - t0, 2)

    t0 = time.monotonic()
    spotter, notes = spotter_best(ctx, root) if root is not None else ([], [])
    ctx.timings["spotter_s"] = round(time.monotonic() - t0, 2)

    write_csv(ctx, out / "scores.csv", rows, SCORE_FIELDS, "scores_csv")
    write_csv(ctx, out / "crops.csv", crops, CROP_FIELDS, "crops_csv")
    n_rendered = sum(bool(r["rendered"]) for r in rows) + len(crops)

    sections = []
    for c in CAMERAS:
        note = [f"top-{args.top_k} by sharpness among non-FAIL frames"
                + (" - FALLBACK: every frame FAILs, best FAIL frames shown" if fallback[c] else "")]
        if not blocks[c]:
            note.append("no renderable frames for this camera")
        sections.append((c, note, blocks[c]))
    rig_lens = [r.get("lens_position") for r in rows if r["camera"] == "imx708"]
    head, sblocks = spotter_section(ctx, crops, spotter, notes, root, rig_lens)
    sections.append(("IMX708 vs Spotter production crop", head, sblocks))
    t0 = time.monotonic()
    title = title_lines(runs, len(sets), rows, time.monotonic() - t_start, args, ctx.run_meta)
    sheet_html(ctx, title, sections)
    sheet_png(ctx, title, sections)
    ctx.timings["sheet_s"] = round(time.monotonic() - t0, 2)
    ctx.timings["total_s"] = round(time.monotonic() - t_start, 2)

    manifest = {
        "tool": "host_tools.pool_render",
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": [sys.executable, "-m", "host_tools.pool_render",
                    *(argv if argv is not None else sys.argv[1:])],
        "git": git_state(REPO),
        "runs": [str(r) for r in runs],
        "spotter_root": str(root) if root else None,
        "settings": {"top_k": args.top_k, "all": args.all, "out": str(out),
                     "prod_crop_xywh_native_px": list(PROD_CROP),
                     "thresholds": {"fail_clip_hi_pct": FAIL_CLIP_HI_PCT,
                                    "warn_clip_hi_pct": WARN_CLIP_HI_PCT, "fail_mean": FAIL_MEAN,
                                    "warn_mean_lo": WARN_MEAN_LO, "warn_mean_hi": WARN_MEAN_HI,
                                    "clip_hi_level": CLIP_HI_LEVEL, "clip_lo_level": CLIP_LO_LEVEL},
                     "crop_block_px": BLOCK, "thumb_width_px": THUMB_W},
        "method": METHOD,
        "counts": {"sets": len(sets), "frames_rendered": sum(bool(r["rendered"]) for r in rows),
                   "prod_crops": len(crops),
                   "per_camera": {c: {s: sum(r["camera"] == c and r["status"] == s for r in rows)
                                      for s in ("PASS", "WARN", "FAIL")} for c in CAMERAS}},
        "selected": {c: [f"{r['run']}/{r['set']}/{r.get('file')}" for r in selected[c]]
                     for c in CAMERAS},
        "fallback_all_fail": fallback,
        "spotter": [{"unit": s["unit"], "file": str(s["path"]),
                     "png": str(s["png"].relative_to(out)),
                     "sharpness_lapvar": s["sharpness_lapvar"], "status": s["status"],
                     "fallback_all_fail": s.get("fallback_all_fail"),
                     "lens_position": s["meta"].get("LensPosition")} for s in spotter],
        "spotter_notes": notes,
        "captions": {m.run.name: m.summary() for m in ctx.run_meta.values()},
        "timings": ctx.timings,
        "inputs": list(ctx.inputs.values()),
        "outputs": ctx.outputs,
        "warnings": ctx.warnings,
    }
    (out / "render_manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n")
    LOG.info("done: %d set(s), %d frame render(s), %d production crop(s), %d warning(s), %.1f s",
             len(sets), manifest["counts"]["frames_rendered"], len(crops), len(ctx.warnings),
             ctx.timings["total_s"])
    LOG.info("  sheet: %s", out / "best_images.html")
    LOG.info("  manifest: %s", out / "render_manifest.json")
    status = 0
    if n_rendered == 0:
        LOG.error("ERROR: zero frames rendered (no DNG/Bayer render and no production crop)")
        status = 1
    dng_tried = [r for r in rows if r["camera"] == "imx708" and r.get("raw_file")
                 and r.get("selected_rank")]
    if dng_tried and not ctx.timings["dng_render_s"]:
        LOG.error("ERROR: every IMX708 DNG render failed (%d picked) — see warnings",
                  len(dng_tried))
        status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
