"""Camera driver registry — Spec §5, §7.

Adapters register a factory under a driver name; the controller looks them up by
name. This keeps platform selection in one place instead of scattering
``if platform == "pi" / elif "n6" ...`` branches through the codebase (CLAUDE.md §6).
"""

from __future__ import annotations

from typing import Callable

from .base import CameraDevice

CameraFactory = Callable[..., CameraDevice]

_REGISTRY: dict[str, CameraFactory] = {}


def register(name: str, factory: CameraFactory) -> None:
    """Register ``factory`` under driver ``name``.

    Raises ``ValueError`` on a duplicate name so registration mistakes fail loudly.
    """
    if not name:
        raise ValueError("driver name must be a non-empty string")
    if name in _REGISTRY:
        raise ValueError(f"driver already registered: {name!r}")
    _REGISTRY[name] = factory


def create(name: str, *args, **kwargs) -> CameraDevice:
    """Instantiate the adapter registered under ``name``.

    Raises ``KeyError`` (listing known drivers) if the name is unknown.
    """
    try:
        factory = _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise KeyError(f"unknown camera driver {name!r}; known drivers: {known}") from None
    return factory(*args, **kwargs)


def available() -> list[str]:
    """Return the sorted list of registered driver names."""
    return sorted(_REGISTRY)


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def unregister(name: str) -> None:
    """Remove a driver registration (primarily for tests)."""
    _REGISTRY.pop(name, None)


def clear() -> None:
    """Clear all registrations (primarily for tests)."""
    _REGISTRY.clear()
