"""OpenMV USB camera host adapter (N6 / AE3) — Spec §8, §10 (Phase 3/4).

Phase 0 placeholder. NO prior art exists for the OpenMV side — every USB/serial and
OpenMV API is UNVERIFIED. Do not implement until OQ-1..OQ-6 (docs/open_questions.md)
are resolved against official OpenMV docs. Devices must be identified by USB
identity / handshake, NOT by a fixed /dev/ttyACM* path (Spec §12).

Board-specific behavior stays isolated (Spec §4 Phase 4): no shared
``if board == "n6" / elif "ae3"`` branching in this adapter.
"""

from __future__ import annotations

from .base import CameraDevice

__phase0_placeholder__ = True


class OpenMvUsbCamera(CameraDevice):
    """Host-side adapter that drives an OpenMV board over USB. Not implemented (Phase 3/4)."""

    driver = "openmv_usb"
