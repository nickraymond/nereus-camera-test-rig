"""Built-in camera driver registration — Spec §5, §7.

Registering here (rather than at package import) keeps imports side-effect-free and
puts driver selection in exactly one place. ``register_builtin()`` is idempotent.
"""

from __future__ import annotations

from . import registry
from .imx708 import Imx708Camera
from .openmv_usb import OpenMvUsbCamera


def register_builtin() -> None:
    """Register all built-in camera drivers. Safe to call more than once."""
    if not registry.is_registered("imx708"):
        registry.register("imx708", Imx708Camera)
    if not registry.is_registered("openmv_usb"):
        registry.register("openmv_usb", OpenMvUsbCamera)
