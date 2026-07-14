"""Shared data models — Spec §5, §13.

Plain dataclasses that define the behavior contracts shared across all cameras and
analysis code, independent of any hardware. Hardware adapters and analysis modules
produce/consume these; the controller never needs to know device-specific details.

Kept deliberately simple (CLAUDE.md §6, §7): only genuinely shared structure lives
here. Unsupported capabilities are represented explicitly (e.g. ``None`` fields)
rather than by omission.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Optional


def _utc_stamp_placeholder() -> str:
    """Default timestamp is intentionally empty; callers set it explicitly.

    We avoid capturing wall-clock time at dataclass-construction time so that
    records are reproducible and timestamps are always set deliberately by the
    capture layer (see ``capture.naming``).
    """
    return ""


@dataclass
class CameraIdentity:
    """Identity of a physical camera/platform — Spec §5, §8.

    ``driver`` is the registry key (e.g. ``"imx708"``, ``"openmv_usb"``).
    OpenMV-only fields stay ``None`` for the Pi camera and vice versa.
    """

    driver: str
    platform: str  # "raspberry_pi" | "openmv"
    device_id: Optional[str] = None
    board: Optional[str] = None  # "n6" | "ae3" for OpenMV; None for Pi
    sensor: Optional[str] = None  # e.g. "imx708"
    firmware: Optional[str] = None
    serial_number: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class CaptureRequest:
    """A request to capture a still or short video — Spec §8, §9.

    ``settings`` carries device-interpreted options (resolution, crop, quality,
    exposure, gain, white balance, focus, warm-up, timeout, ...). The controller
    passes it through opaquely; each adapter interprets what it supports and
    records unsupported settings rather than silently dropping them.
    """

    kind: str  # "image" | "video"
    settings: dict[str, Any] = field(default_factory=dict)
    warmup_frames: Optional[int] = None
    timeout_seconds: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class CaptureResult:
    """Outcome of a single capture — Spec §5, §8.

    ``status`` is ``"completed"`` or ``"failed"``. On failure, ``error`` holds a
    ``{"code", "message"}`` mapping and ``output_path`` is ``None``. Fields follow
    the reusable result field set surfaced in the prior-art review.
    """

    camera: CameraIdentity
    request: CaptureRequest
    status: str  # "completed" | "failed"
    output_path: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    image_format: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    duration_seconds: Optional[float] = None
    sensor_metadata: dict[str, Any] = field(default_factory=dict)
    error: Optional[dict[str, str]] = None

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class DetectionResult:
    """Reference-card / AprilTag analysis result — Spec §13.

    Mirrors the machine-readable JSON schema in Spec §13. Pass rule (MVP): all
    expected tags detected, card boundary computable, nonempty crop saved.
    """

    status: str  # "pass" | "fail"
    tags_detected: list[int] = field(default_factory=list)
    expected_tags: list[int] = field(default_factory=list)
    all_expected_tags_found: bool = False
    card_crop_created: bool = False
    crop_width: Optional[int] = None
    crop_height: Optional[int] = None
    annotated_path: Optional[str] = None
    crop_path: Optional[str] = None
    processing_time_ms: Optional[float] = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class ExperimentRecord:
    """Self-contained record of one experiment run — Spec §5, §12, §13.

    A future reviewer should understand the run from this record alone, without
    reading chat history (CLAUDE.md §12).
    """

    experiment_id: str
    timestamp: str = field(default_factory=_utc_stamp_placeholder)
    environment_label: str = ""
    operator_notes: str = ""
    experiment_type: str = ""
    cameras: list[CameraIdentity] = field(default_factory=list)
    captures: list[CaptureResult] = field(default_factory=list)
    analyses: list[DetectionResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)
