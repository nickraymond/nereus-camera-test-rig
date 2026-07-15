"""Capture coordination helpers — Spec §11 (single-capture slice used in Phase 1).

Full multi-camera experiment orchestration and the experiment-folder layout arrive
in Phase 5. Phase 1 needs only a single-camera capture that produces a validated
artifact + metadata sidecar, which ``capture_once`` provides. The CLI is a thin
wrapper over this.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import config as config_mod
from .cameras import registry
from .cameras.builtin import register_builtin
from .capture import naming
from .models import CaptureRequest, CaptureResult
from .storage.metadata import write_capture_metadata


@dataclass
class CaptureOutcome:
    """Result of a single capture plus where its artifacts landed."""

    camera_name: str
    result: CaptureResult
    output_path: Optional[str]
    metadata_path: Optional[str]

    @property
    def ok(self) -> bool:
        return self.result.ok


def _load_camera_profile(camera_cfg: dict[str, Any]) -> dict[str, Any]:
    """Load a camera's profile YAML if the entry points at one; else {}."""
    profile_path = camera_cfg.get("profile")
    if not profile_path or not Path(profile_path).is_file():
        return {}
    return config_mod.load_yaml(profile_path)


def capture_once(
    config: dict[str, Any],
    camera_name: str,
    kind: str,
    out_dir: str | Path,
) -> CaptureOutcome:
    """Capture one still/video from ``camera_name`` into ``out_dir``.

    Writes the artifact and a ``*.json`` metadata sidecar. Never raises for a
    capture failure — returns a ``CaptureOutcome`` whose ``result.status`` is
    "failed" so callers can report partial success (Spec §11, CLAUDE.md §17).
    """
    register_builtin()
    cameras = config.get("cameras", {})
    if camera_name not in cameras:
        raise KeyError(f"camera {camera_name!r} not in config; known: {sorted(cameras)}")
    camera_cfg = cameras[camera_name]
    driver = camera_cfg["driver"]
    profile = _load_camera_profile(camera_cfg)

    device = registry.create(driver, settings=profile)

    out_dir = Path(out_dir)
    # Video default is MJPEG on the Pi 5 (no H.264 encoder; see OQ-17).
    ext = "jpg" if kind == "image" else "mjpeg"
    filename = naming.capture_filename(camera_name, kind, ext)
    dest = out_dir / filename

    request = CaptureRequest(kind=kind, settings=dict(profile))
    if kind == "image":
        result = device.capture_image(str(dest), request)
    else:
        result = device.capture_video(str(dest), request)

    metadata_path = None
    if result.output_path:
        meta = Path(result.output_path).with_suffix(".json")
        write_capture_metadata(meta, result)
        metadata_path = str(meta)

    return CaptureOutcome(
        camera_name=camera_name,
        result=result,
        output_path=result.output_path,
        metadata_path=metadata_path,
    )
