"""IMX708 (Raspberry Pi CSI) camera adapter — Spec §9 (Phase 1).

Phase 0 placeholder. Implementation will ADAPT the proven rpicam-still /
libcamera-still capture path from bm_cam_legacy's ``process_image_v2.py``
(camera-binary selection, control-flag builder, --metadata parsing, timeout/
retry/progressive-fallback) and PORT its crop/downsample + HEIC helpers, with all
Bristlemouth/Spotter telemetry and hardcoded /home/pi paths stripped. See
docs/implementation_plan.md. Blocked-until-verified items: OQ-7, OQ-8, OQ-10.

Not Picamera2 — Spec §9 prefers the rpicam/libcamera CLI path.
"""

from __future__ import annotations

from .base import CameraDevice

__phase0_placeholder__ = True


class Imx708Camera(CameraDevice):
    """Raspberry Pi IMX708 adapter (CSI). Not yet implemented (Phase 1)."""

    driver = "imx708"
