"""Reference-card analysis orchestrator + writer — Spec §13.

Ties the pieces together: detect AprilTags -> localize the card -> rectify/crop ->
metrics -> write ``detection.json`` + ``annotated.jpg`` + ``card_crop.jpg``, and
returns the shared ``DetectionResult`` model.

**Pass rule (MVP, Spec §13):** all required AprilTags detected, card boundary
computable, and a nonempty crop produced and saved without error. Secondary metrics
(sharpness, clipping, ...) are recorded but never block the pass.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from ..models import DetectionResult
from . import image_metrics
from .apriltag_detector import DEFAULT_FAMILY, DEFAULT_SCALES, detect_tags
from .crop import make_card_crop, save_image
from .reference_card import (
    DEFAULT_CORNER_MAP,
    DEFAULT_EXPAND_X,
    DEFAULT_EXPAND_Y,
    DEFAULT_RECTIFIED_H,
    DEFAULT_RECTIFIED_W,
    CardLocalizationError,
    localize_card,
)


@dataclass
class AnalysisConfig:
    """Card-specific analysis parameters (defaults = V2 canonical geometry)."""

    expected_tag_ids: list[int] = field(default_factory=lambda: [0, 1, 2, 3])
    corner_map: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_CORNER_MAP))
    family: str = DEFAULT_FAMILY
    scales: tuple[int, ...] = DEFAULT_SCALES
    expand_x: float = DEFAULT_EXPAND_X
    expand_y: float = DEFAULT_EXPAND_Y
    rectified_w: int = DEFAULT_RECTIFIED_W
    rectified_h: int = DEFAULT_RECTIFIED_H
    tag_min_side_px: float = 10.0  # below this a tag is too small to trust (OQ-12)

    @classmethod
    def from_dict(cls, cfg: Optional[dict[str, Any]]) -> "AnalysisConfig":
        """Build from a rig config's ``analysis.apriltag`` block (partial is fine)."""
        cfg = cfg or {}
        ap = cfg.get("apriltag", cfg)  # accept either the analysis block or the apriltag block
        kwargs: dict[str, Any] = {}
        if "expected_tag_ids" in ap:
            kwargs["expected_tag_ids"] = list(ap["expected_tag_ids"])
        for key in ("corner_map", "family", "expand_x", "expand_y", "rectified_w", "rectified_h"):
            if key in ap:
                kwargs[key] = ap[key]
        return cls(**kwargs)


def _annotate(image: np.ndarray, outcome, location) -> np.ndarray:
    """Draw detected tag corners and the card quad on a copy of the image."""
    out = image.copy()
    for tag in outcome.tags.values():
        pts = tag.corners.astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(out, [pts], True, (0, 255, 0), 3)
        cx, cy = map(int, tag.center)
        cv2.putText(out, str(tag.tag_id), (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 255), 4)
    if location is not None:
        cv2.polylines(out, [location.card_quad.astype(np.int32).reshape(-1, 1, 2)], True,
                      (255, 0, 0), 3)
    return out


def analyze_reference_card(
    image_path: str | Path,
    out_dir: str | Path,
    config: Optional[AnalysisConfig | dict] = None,
) -> DetectionResult:
    """Run the full reference-card pipeline and write §13 artifacts to ``out_dir``.

    Never raises for an analysis failure — returns a ``DetectionResult`` with
    ``status="fail"`` and populated ``errors`` so a caller can report per-camera
    partial success (Spec §11).
    """
    cfg = config if isinstance(config, AnalysisConfig) else AnalysisConfig.from_dict(config)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    result = DetectionResult(status="fail", expected_tags=list(cfg.expected_tag_ids))

    image = cv2.imread(str(image_path))
    if image is None:
        result.errors.append(f"could not read image: {image_path}")
        result.processing_time_ms = (time.monotonic() - started) * 1000
        _write_json(out_dir / "detection.json", result, extra={})
        return result

    outcome = detect_tags(image, family=cfg.family, scales=cfg.scales)
    result.tags_detected = outcome.tag_ids
    found = set(outcome.tag_ids)
    result.all_expected_tags_found = set(cfg.expected_tag_ids).issubset(found)

    # Warn on tags too small to trust (non-blocking; OQ-12).
    small = [t.tag_id for t in outcome.tags.values() if t.side_px_min < cfg.tag_min_side_px]
    if small:
        result.warnings.append(f"tags below {cfg.tag_min_side_px}px min side: {sorted(small)}")

    location = None
    if result.all_expected_tags_found:
        try:
            location = localize_card(
                outcome, corner_map=cfg.corner_map, expand_x=cfg.expand_x, expand_y=cfg.expand_y
            )
        except CardLocalizationError as exc:
            result.errors.append(str(exc))
    else:
        result.errors.append(
            f"missing expected tags: have {result.tags_detected}, need {cfg.expected_tag_ids}"
        )

    metrics: dict[str, float] = {}
    if location is not None:
        annotated = _annotate(image, outcome, location)
        save_image(annotated, out_dir / "annotated.jpg")
        result.annotated_path = str(out_dir / "annotated.jpg")
        try:
            crop = make_card_crop(image, location, cfg.rectified_w, cfg.rectified_h)
            w, h, _ = save_image(crop, out_dir / "card_crop.jpg")
            result.card_crop_created = True
            result.crop_width, result.crop_height = w, h
            result.crop_path = str(out_dir / "card_crop.jpg")
            metrics = image_metrics.card_metrics(crop)
        except IOError as exc:
            result.errors.append(f"crop failed: {exc}")

    # Pass rule: all expected tags + boundary computed + nonempty crop saved.
    if result.all_expected_tags_found and location is not None and result.card_crop_created:
        result.status = "pass"

    result.processing_time_ms = (time.monotonic() - started) * 1000
    _write_json(
        out_dir / "detection.json",
        result,
        extra={
            "scale_used": outcome.scale_used,
            "tags": {
                str(t.tag_id): {
                    "corners": t.corners.tolist(),
                    "center": list(t.center),
                    "side_px_min": t.side_px_min,
                }
                for t in outcome.tags.values()
            },
            "metrics": metrics,
        },
    )
    return result


def _write_json(path: Path, result: DetectionResult, extra: dict[str, Any]) -> None:
    payload = result.to_dict()
    payload.update(extra)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    os.replace(tmp, path)
