"""Common camera interface — Spec §5, §7.

Every camera adapter implements this small, shared contract. The controller talks
only to this interface and never to device-specific APIs (CLAUDE.md §6). Adapters
are free to implement each method differently; unsupported capabilities are
represented explicitly rather than faked.

Methods here raise ``NotImplementedError`` so that a not-yet-implemented adapter
fails loudly and usefully (CLAUDE.md §17) instead of silently doing nothing.
"""

from __future__ import annotations

from typing import Any

from ..models import CaptureRequest, CaptureResult


class CameraDevice:
    """Conceptual interface shared by all camera adapters (Spec §5).

    Concrete adapters: ``Imx708Camera`` (CSI) and ``OpenMvUsbCamera`` (USB).
    """

    #: Registry driver key, e.g. "imx708" or "openmv_usb". Set by subclasses.
    driver: str = ""

    def get_device_info(self) -> dict[str, Any]:
        """Return identity + capabilities (Spec §8 device info)."""
        raise NotImplementedError

    def configure(self, settings: dict[str, Any]) -> None:
        """Apply device configuration prior to capture (Spec §9)."""
        raise NotImplementedError

    def capture_image(self, destination: str, request: CaptureRequest) -> CaptureResult:
        """Capture a still to ``destination`` and return a structured result."""
        raise NotImplementedError

    def capture_video(self, destination: str, request: CaptureRequest) -> CaptureResult:
        """Capture a short video to ``destination`` and return a structured result."""
        raise NotImplementedError

    def health_check(self) -> dict[str, Any]:
        """Return a health/status snapshot for the rig-status view (Spec §14)."""
        raise NotImplementedError
