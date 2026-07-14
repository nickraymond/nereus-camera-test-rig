"""Per-capture metadata sidecar — Spec §5, §12.

Writes a self-contained JSON sidecar next to each capture so a future reviewer
understands the run without any chat history (CLAUDE.md §12). Atomic write via a
temp file + replace so a crash never leaves a half-written sidecar.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..models import CaptureResult


def write_json(path: str | Path, obj: dict[str, Any]) -> Path:
    """Write ``obj`` as pretty JSON to ``path`` atomically. Returns the path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str))
    os.replace(tmp, p)
    return p


def capture_sidecar(result: CaptureResult) -> dict[str, Any]:
    """Build the sidecar dict for a capture (device + request + result + sensor meta)."""
    return {
        "camera": result.camera.to_dict(),
        "request": result.request.to_dict(),
        "status": result.status,
        "output": {
            "path": result.output_path,
            "width": result.width,
            "height": result.height,
            "format": result.image_format,
            "size_bytes": result.size_bytes,
            "sha256": result.sha256,
        },
        "duration_seconds": result.duration_seconds,
        "sensor_metadata": result.sensor_metadata,
        "error": result.error,
    }


def write_capture_metadata(path: str | Path, result: CaptureResult) -> Path:
    """Write a capture's sidecar JSON to ``path``."""
    return write_json(path, capture_sidecar(result))
