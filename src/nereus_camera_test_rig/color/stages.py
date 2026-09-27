"""Stage framework + ``inspect`` — SPEC §4 Phase 8 S0, §20.

Each stage (``inspect`` now; ``ingest → locate → qc → fit → correct → report`` in S1–S2)
writes its outputs plus a ``stage.json`` provenance record: git SHA + dirty flag, SHA-256 of
every config file it used, and the SHA-256 of each upstream ``stage.json``. Before a stage
reads an upstream stage, ``verify_fresh`` re-checks that chain and fails loudly if a config
changed or an upstream was re-run since — so a result is never silently built on stale input.

RAW files are opened through a small reader registry keyed by extension. ``.dng`` is
built in; Mac-only tools register ``.orf`` (rawpy) — this module never imports rawpy.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import cv2
import numpy as np

from .raw_io import RawFrame, bin2x2, normalize, read_dng

STAGE_FILE = "stage.json"
READERS: dict[str, Callable[[Path], RawFrame]] = {".dng": read_dng}


class StaleInputError(RuntimeError):
    """An upstream stage's inputs changed after it ran; re-run it first."""


def register_reader(extension: str, reader: Callable[[Path], RawFrame]) -> None:
    READERS[extension.lower()] = reader


def open_raw(path: str | Path) -> RawFrame:
    path = Path(path)
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        raise ValueError(f"{path}: no RAW reader for {path.suffix!r} "
                         f"(registered: {sorted(READERS)})")
    return reader(path)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git_state(repo: Optional[Path] = None) -> dict[str, Any]:
    """HEAD SHA and whether the tree is dirty; 'unknown' outside a git checkout."""
    repo = repo or Path(__file__).resolve().parents[3]
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True,
                               text=True, check=True).stdout.strip() != ""
        return {"sha": sha, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"sha": "unknown", "dirty": None}


def write_stage(out_dir: Path, stage: str, *, configs: Iterable[Path] = (),
                upstream: Iterable[Path] = (), params: Optional[dict] = None) -> dict:
    """Write ``out_dir/stage.json`` after a stage's outputs are complete."""
    record = {
        "stage": stage,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": git_state(),
        "configs": {str(p): sha256_file(p) for p in configs},
        "upstream": {str(d): sha256_file(Path(d) / STAGE_FILE) for d in upstream},
        "params": params or {},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / STAGE_FILE).write_text(json.dumps(record, indent=2) + "\n")
    return record


def verify_fresh(stage_dir: Path) -> dict:
    """Raise ``StaleInputError`` if ``stage_dir`` or anything upstream of it is stale."""
    path = Path(stage_dir) / STAGE_FILE
    if not path.is_file():
        raise StaleInputError(f"{stage_dir}: no {STAGE_FILE} — run that stage first")
    record = json.loads(path.read_text())
    for cfg, digest in record["configs"].items():
        if not Path(cfg).is_file() or sha256_file(cfg) != digest:
            raise StaleInputError(f"{stage_dir}: config {cfg} changed since the "
                                  f"{record['stage']!r} stage ran — re-run it")
    for up, digest in record["upstream"].items():
        verify_fresh(Path(up))
        if sha256_file(Path(up) / STAGE_FILE) != digest:
            raise StaleInputError(f"{stage_dir}: upstream {up} was re-run after the "
                                  f"{record['stage']!r} stage — re-run it")
    return record


def _srgb8(linear: np.ndarray) -> np.ndarray:
    x = np.clip(linear, 0.0, 1.0)
    x = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return (x * 255 + 0.5).astype(np.uint8)


def inspect(path: str | Path, out_root: Path) -> dict:
    """Stage ``inspect``: metadata + linear stats of one RAW file, and a quick-look preview.

    The preview applies only the as-shot white balance and a 99th-percentile exposure
    stretch — a sanity check that the decode is right, not a colour rendering.
    """
    path = Path(path)
    frame = open_raw(path)
    linear, saturated, cfa = normalize(frame)
    binned, clip = bin2x2(linear, cfa, saturated)
    pixels = binned.reshape(-1, 3)
    exif = frame.source.get("exif", {})
    summary = {
        "file": str(path),
        "reader": frame.source.get("reader"),
        "mosaic": list(frame.mosaic.shape[::-1]),
        "valid_crop": frame.valid_crop,
        "cfa_active": cfa,
        "black_level_per_position": list(frame.black_level),
        "white_level": frame.white_level,
        "exposure_s": frame.exposure_s,
        "iso": frame.iso,
        "fnumber": frame.fnumber,
        "exposure_factor": frame.exposure_factor() if frame.exposure_s else None,
        "as_shot_wb": frame.as_shot_wb,
        "depth_m": exif.get("depth_m"),
        "time_utc": exif.get("time_utc"),
        "binned_shape": list(binned.shape),
        "linear_mean_rgb": [round(float(v), 5) for v in pixels.mean(axis=0)],
        "clip_pct_rgb": [round(100 * float(v), 4) for v in clip.reshape(-1, 3).mean(axis=0)],
    }
    out_dir = out_root / "inspect" / path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    preview = binned * np.asarray(frame.as_shot_wb or (1.0, 1.0, 1.0), dtype=np.float32)
    preview /= max(float(np.percentile(preview, 99)), 1e-6)
    if not cv2.imwrite(str(out_dir / "preview.png"), cv2.cvtColor(_srgb8(preview),
                                                                  cv2.COLOR_RGB2BGR)):
        raise IOError(f"failed to write {out_dir / 'preview.png'}")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    write_stage(out_dir, "inspect", params={"file": str(path), "sha256": sha256_file(path)})
    summary["out_dir"] = str(out_dir)
    return summary
