"""Stage ``ingest`` — build the tool's own manifest for a dataset (SPEC §4 Phase 8 S1, §20).

Input: a read-only dataset folder (``raw/<category>/<stem>.<raw ext|jpg>``) and its config
``configs/datasets/<dataset>.yaml``. Output (never inside the dataset):
``results/color/<dataset_id>/ingest/{manifest.csv, summary.json, stage.json}``.

Capture metadata comes from a ``read_metadata(paths) -> list[dict]`` callable supplied by the
caller (the Mac CLI passes the exiftool reader), so this module stays reader-agnostic. Each
dict needs: path, time_utc (ISO 8601), depth_m, exposure_s, iso, fnumber, flash_fired;
optional: focal_length_mm, black_level2, model.

- ``dive_id``: split the time-sorted shots at gaps > ``dive_gap_minutes``; each group must
  fall inside the configured dive window, in order, or ingest fails loudly.
- ``sweep_id``: reference-category frames only, chained to the previous reference frame
  while < ``sweep_gap_s`` apart and within ``sweep_depth_m`` in depth (brief §7 P1.1).
- A hand-made ``manifest.csv`` inside the dataset, if present, is read only to carry its
  notes and to cross-check category / depth / flash; mismatches are reported, not fixed.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..config import ConfigError, load_yaml
from .stages import write_stage
from .sun import solar_elevation_deg

RAW_EXTENSIONS = (".orf", ".dng")
IMAGE_EXTENSIONS = (".jpg", ".jpeg")
COLUMNS = ["stem", "file", "jpeg", "has_raw", "category", "camera", "dive_id", "site",
           "sweep_id", "time_utc", "sun_elevation_deg", "depth_m", "exposure_s", "iso",
           "fnumber", "focal_length_mm", "flash_fired", "black_level", "notes"]
DEFAULTS = {"reference_category": "1_reference_A_iso100", "dive_gap_minutes": 45,
            "sweep_gap_s": 90, "sweep_depth_m": 1.5}


def discover(dataset_dir: Path) -> list[dict[str, Any]]:
    """One entry per shot stem: its category and its RAW and/or JPEG file (relative)."""
    shots: dict[str, dict[str, Any]] = {}
    for f in sorted((dataset_dir / "raw").glob("*/*")):
        ext = f.suffix.lower()
        if ext not in RAW_EXTENSIONS + IMAGE_EXTENSIONS:
            continue
        shot = shots.setdefault(f.stem, {"stem": f.stem, "category": f.parent.name,
                                         "raw": None, "jpeg": None})
        if shot["category"] != f.parent.name:
            raise ConfigError(f"{f.stem} appears in two categories: {shot['category']}, "
                              f"{f.parent.name}")
        shot["raw" if ext in RAW_EXTENSIONS else "jpeg"] = f.relative_to(dataset_dir)
    return list(shots.values())


def dataset_id(name: str, dataset_dir: Path, shots: list[dict[str, Any]]) -> str:
    """``<name>-<8 hex>``: a short hash of the dataset's file list (paths + sizes)."""
    h = hashlib.sha256()
    for shot in shots:
        for rel in (shot["raw"], shot["jpeg"]):
            if rel is not None:
                h.update(f"{rel}:{(dataset_dir / rel).stat().st_size}\n".encode())
    return f"{name}-{h.hexdigest()[:8]}"


def assign_dives(rows: list[dict[str, Any]], cfg: dict[str, Any], gap_min: float) -> None:
    dives = cfg.get("dives") or {}
    rows.sort(key=lambda r: r["time_utc"])
    groups: list[list[dict]] = []
    for row in rows:
        t = datetime.fromisoformat(row["time_utc"])
        if not groups or (t - datetime.fromisoformat(groups[-1][-1]["time_utc"])
                          ).total_seconds() > gap_min * 60:
            groups.append([])
        groups[-1].append(row)
    if dives and len(groups) != len(dives):
        raise ConfigError(f"found {len(groups)} dives by time gap (> {gap_min} min) but the "
                          f"config lists {len(dives)}")
    for n, group in enumerate(groups, start=1):
        window = dives.get(n, {})
        if window:
            start = datetime.fromisoformat(window["start_utc"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(window["end_utc"].replace("Z", "+00:00"))
            first = datetime.fromisoformat(group[0]["time_utc"])
            last = datetime.fromisoformat(group[-1]["time_utc"])
            if first < start or last > end:
                raise ConfigError(f"dive {n} ({first.isoformat()} … {last.isoformat()}) is "
                                  f"outside its configured window {start} … {end}")
        for row in group:
            row["dive_id"], row["site"] = n, window.get("site", "")


def assign_sweeps(rows: list[dict[str, Any]], category: str, gap_s: float, depth_m: float):
    sweep, prev = 0, None
    for row in rows:  # already time-sorted
        if row["category"] != category:
            continue
        t = datetime.fromisoformat(row["time_utc"])
        if (prev is None or row["dive_id"] != prev["dive_id"]
                or (t - datetime.fromisoformat(prev["time_utc"])).total_seconds() >= gap_s
                or abs(float(row["depth_m"]) - float(prev["depth_m"])) > depth_m):
            sweep += 1
        row["sweep_id"] = sweep
        prev = row
    return sweep


def _cross_check(rows, dataset_dir: Path) -> list[str]:
    hand = dataset_dir / "manifest.csv"
    if not hand.is_file():
        return []
    by_stem = {r["stem"]: r for r in csv.DictReader(hand.open())}
    issues = []
    for row in rows:
        h = by_stem.get(row["stem"])
        if h is None:
            issues.append(f"{row['stem']}: not in the hand-made manifest")
            continue
        row["notes"] = h.get("notes", "").strip()
        if h.get("category") != row["category"]:
            issues.append(f"{row['stem']}: category {row['category']} vs hand {h['category']}")
        if h.get("water_depth_m") and abs(float(h["water_depth_m"]) - row["depth_m"]) > 0.05:
            issues.append(f"{row['stem']}: depth {row['depth_m']} vs hand {h['water_depth_m']}")
        if h.get("flash_fired") and (h["flash_fired"] == "True") != row["flash_fired"]:
            issues.append(f"{row['stem']}: flash_fired disagrees with the hand-made manifest")
    return issues


def ingest(dataset_dir: Path, config_path: Path, out_root: Path,
           read_metadata: Callable[[list[Path]], list[dict[str, Any]]]) -> dict[str, Any]:
    cfg = {**DEFAULTS, **load_yaml(config_path)}
    shots = discover(dataset_dir)
    if not shots:
        raise ConfigError(f"{dataset_dir}: no RAW/JPEG files under raw/<category>/")
    did = dataset_id(cfg.get("dataset", dataset_dir.name), dataset_dir, shots)
    sources = [dataset_dir / (s["raw"] or s["jpeg"]) for s in shots]
    meta = {Path(m["path"]).resolve(): m for m in read_metadata(sources)}

    rows = []
    for shot, src in zip(shots, sources):
        m = meta[src.resolve()]
        black = m.get("black_level2")
        rows.append({
            "stem": shot["stem"], "file": str(shot["raw"] or shot["jpeg"]),
            "jpeg": str(shot["jpeg"] or ""), "has_raw": shot["raw"] is not None,
            "category": shot["category"], "camera": cfg.get("camera", m.get("model", "")),
            "dive_id": "", "site": "", "sweep_id": "", "time_utc": m["time_utc"],
            "depth_m": m["depth_m"], "exposure_s": m["exposure_s"], "iso": m["iso"],
            "fnumber": m["fnumber"], "focal_length_mm": m.get("focal_length_mm", ""),
            "flash_fired": bool(m["flash_fired"]),
            "black_level": " ".join(str(v) for v in black) if black else "", "notes": "",
        })
    assign_dives(rows, cfg, float(cfg["dive_gap_minutes"]))
    sites = cfg.get("sites") or {}
    for row in rows:
        site = sites.get(row["site"])
        row["sun_elevation_deg"] = (round(solar_elevation_deg(
            datetime.fromisoformat(row["time_utc"]), site["lat"], site["lon"]), 2)
            if site else "")
    n_sweeps = assign_sweeps(rows, cfg["reference_category"], float(cfg["sweep_gap_s"]),
                             float(cfg["sweep_depth_m"]))
    issues = _cross_check(rows, dataset_dir)

    out_dir = out_root / did / "ingest"
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "manifest.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    by_category: dict[str, int] = {}
    for row in rows:
        by_category[row["category"]] = by_category.get(row["category"], 0) + 1
    summary = {"dataset_id": did, "dataset_dir": str(dataset_dir), "shots": len(rows),
               "with_raw": sum(r["has_raw"] for r in rows), "by_category": by_category,
               "dives": len({r["dive_id"] for r in rows}), "sweeps": n_sweeps,
               "flash_fired": sum(r["flash_fired"] for r in rows),
               "cross_check_issues": issues}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_stage(out_dir, "ingest", configs=[config_path],
                params={"dataset_dir": str(dataset_dir), "dataset_id": did,
                        **{k: cfg[k] for k in DEFAULTS}})
    summary["out_dir"] = str(out_dir)
    return summary
