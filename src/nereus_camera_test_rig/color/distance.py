"""Stage ``distance`` — camera-to-card distance per located frame (SPEC §4 Phase 8 S1.4).

The card pose comes from PnP on the tag-centre quad that ``locate`` stored (RAW-mosaic
pixels) against the physical tag-centre rectangle from the card YAML (``physical_mm``,
364.900 × 91.566 mm for V2). ``cv2.SOLVEPNP_IPPE`` is the planar solver and is exact for
four coplanar points.

Intrinsics come from ``configs/calibration/<camera_id>.yaml``: focal length in pixels =
EXIF focal length / pixel pitch, × ``water_focal_factor`` (flat port, ~1.33) when the
camera looked through water. The medium per frame is dataset config (``medium``), because
depth cannot tell a shot at the surface in air from one just under it. While the
calibration is ``provisional`` every distance carries ``z_provisional: true``.

Per frame: ``z_m`` (along the optical axis — the ``z`` of the brief), ``range_m`` (straight
line to the card centre, the water path length), ``tilt_deg`` (card normal vs optical axis)
and ``reproj_rms_px``. Frames without a quad, or shot through a split waterline, get
``z_m: null`` and a reason — nothing is dropped.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import ConfigError, load_yaml
from .card import Card, load_card
from .stages import verify_fresh, write_stage

MEDIA = ("water", "air", "split")


def card_object_points(card: Card) -> np.ndarray:
    """Tag-centre rectangle in mm, TL, TR, BR, BL (the order ``locate`` stores quads in)."""
    sx = card.physical_mm.get("tag_center_spacing_x")
    sy = card.physical_mm.get("tag_center_spacing_y")
    if not sx or not sy:
        raise ConfigError(f"{card.path}: physical_mm needs tag_center_spacing_x/y for distance")
    return np.array([[0, 0, 0], [sx, 0, 0], [sx, sy, 0], [0, sy, 0]], dtype=np.float64)


def medium_for(stem: str, cfg: dict[str, Any]) -> str:
    """The medium the camera looked through for ``stem`` (dataset config ``medium``)."""
    spec = cfg.get("medium") or {}
    for medium in ("air", "split"):
        if stem in (spec.get(medium) or []):
            return medium
    default = spec.get("default", "water")
    if default not in MEDIA:
        raise ConfigError(f"medium.default must be one of {MEDIA}, got {default!r}")
    return default


def focal_px(focal_mm: float, intrinsics: dict[str, Any], medium: str) -> float:
    f = float(focal_mm) / float(intrinsics["pixel_pitch_mm"])
    return f * float(intrinsics.get("water_focal_factor", 1.33)) if medium == "water" else f


def solve_pose(quad: np.ndarray, obj: np.ndarray, f_px: float,
               principal: tuple[float, float]) -> dict[str, float]:
    """PnP (IPPE, no distortion) of one tag-centre quad → distance, tilt and fit error."""
    K = np.array([[f_px, 0, principal[0]], [0, f_px, principal[1]], [0, 0, 1]], dtype=np.float64)
    img = np.asarray(quad, dtype=np.float64).reshape(4, 1, 2)
    ok, rvec, tvec = cv2.solvePnP(obj.reshape(4, 1, 3), img, K, None, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        raise ValueError("solvePnP failed")
    R, _ = cv2.Rodrigues(rvec)
    centre = R @ obj.mean(axis=0) + tvec.ravel()  # card centre in camera coords, mm
    proj, _ = cv2.projectPoints(obj.reshape(4, 1, 3), rvec, tvec, K, None)
    rms = float(np.sqrt(np.mean(np.sum((proj - img) ** 2, axis=2))))
    return {"z_m": round(float(centre[2]) / 1000, 4),
            "range_m": round(float(np.linalg.norm(centre)) / 1000, 4),
            "tilt_deg": round(float(np.degrees(np.arccos(min(1.0, abs(R[2, 2]))))), 2),
            "reproj_rms_px": round(rms, 3)}


def frame_distance(rec: dict, row: dict, medium: str, obj: np.ndarray,
                   intrinsics: dict, provisional: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"medium": medium, "locate_method": rec.get("locate_method"),
                           "z_provisional": provisional, "z_m": None}
    if not rec.get("located"):
        return {**out, "reason": "card not located"}
    if medium == "split":
        return {**out, "reason": "lens split at the waterline — no single focal length"}
    try:
        f_mm = float(row["focal_length_mm"])
    except (KeyError, TypeError, ValueError):
        return {**out, "reason": "no EXIF focal length"}
    f = focal_px(f_mm, intrinsics, medium)
    pp = tuple(float(v) for v in intrinsics["principal_point_raw"])
    try:
        pose = solve_pose(np.asarray(rec["quad_raw"]), obj, f, pp)
    except (ValueError, cv2.error) as exc:
        return {**out, "reason": f"PnP failed: {exc}"}
    return {**out, "focal_px": round(f, 1), **pose}


def distance(locate_dir: Path, calibration: Path, dataset_config: Path,
             card_path: Path) -> dict[str, Any]:
    """Run ``distance`` over every frame ``locate`` recorded."""
    verify_fresh(locate_dir)
    ingest_dir = locate_dir.parent / "ingest"
    rows = {r["stem"]: r for r in csv.DictReader((ingest_dir / "manifest.csv").open())}
    corners = json.loads((locate_dir / "corners.json").read_text())
    cfg = load_yaml(dataset_config)
    calib = load_yaml(calibration)
    intrinsics = calib.get("intrinsics") or {}
    for key in ("pixel_pitch_mm", "principal_point_raw"):
        if key not in intrinsics:
            raise ConfigError(f"{calibration}: intrinsics.{key} missing")
    provisional = bool(calib.get("provisional", True))
    obj = card_object_points(load_card(card_path))

    out = {stem: frame_distance(rec, rows[stem], medium_for(stem, cfg), obj, intrinsics,
                                provisional)
           for stem, rec in corners.items()}
    zs = np.array([r["z_m"] for r in out.values() if r["z_m"] is not None])
    summary: dict[str, Any] = {
        "frames": len(out), "with_z": int(zs.size),
        "without_z": {}, "z_provisional": provisional,
        "z_m_percentiles": ({p: round(float(np.percentile(zs, p)), 3) for p in (0, 10, 50, 90, 100)}
                            if zs.size else None),
        "by_medium": {m: sum(r["medium"] == m for r in out.values()) for m in MEDIA},
    }
    for r in out.values():
        if r["z_m"] is None:
            summary["without_z"][r["reason"]] = summary["without_z"].get(r["reason"], 0) + 1

    out_dir = locate_dir.parent / "distance"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "distances.json").write_text(json.dumps(out, indent=1) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_stage(out_dir, "distance", configs=[calibration, dataset_config, card_path],
                upstream=[locate_dir],
                params={"solver": "cv2.SOLVEPNP_IPPE", "distortion": intrinsics.get("distortion"),
                        "intrinsics_source": intrinsics.get("source"),
                        "provisional": provisional})
    summary["out_dir"] = str(out_dir)
    return summary


def load_distances(distance_dir: Path) -> dict[str, dict]:
    """Downstream helper: verified-fresh ``distances.json``."""
    verify_fresh(distance_dir)
    return json.loads((Path(distance_dir) / "distances.json").read_text())

