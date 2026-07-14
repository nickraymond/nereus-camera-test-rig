"""Configuration loading + validation — Spec §12.

Loads the rig YAML and validates the top-level shape, failing loudly with a clear
message (CLAUDE.md §17) rather than letting a malformed config surface as an
obscure error deep in a capture run. Adapted in spirit from bm_rpi_camera_module's
``common/config.py`` (load + resolve helpers), re-scoped to this rig's schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when a config file is missing, unreadable, or structurally invalid."""


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file into a dict.

    Raises ``ConfigError`` if the file is missing or does not parse to a mapping.
    """
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as exc:  # pragma: no cover - message passthrough
        raise ConfigError(f"failed to parse YAML {p}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping, got {type(data).__name__}: {p}")
    return data


# Top-level sections expected in the rig config (Spec §12).
_REQUIRED_TOP_LEVEL = ("rig", "cameras")
_OPTIONAL_TOP_LEVEL = ("analysis", "web")


def validate_rig_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate the rig config shape (Spec §12). Returns the config unchanged on success.

    Checks presence/typing of the top-level sections and the minimal fields the rig
    depends on. Deliberately light-touch: per-device profile validation belongs to
    the adapters that consume them.
    """
    if not isinstance(config, dict):
        raise ConfigError(f"rig config must be a mapping, got {type(config).__name__}")

    for key in _REQUIRED_TOP_LEVEL:
        if key not in config:
            raise ConfigError(f"rig config missing required top-level section: {key!r}")

    rig = config["rig"]
    if not isinstance(rig, dict) or "id" not in rig:
        raise ConfigError("rig config 'rig' section must be a mapping containing 'id'")

    cameras = config["cameras"]
    if not isinstance(cameras, dict) or not cameras:
        raise ConfigError("rig config 'cameras' section must be a non-empty mapping")

    for name, cam in cameras.items():
        if not isinstance(cam, dict):
            raise ConfigError(f"camera {name!r} entry must be a mapping")
        if "driver" not in cam:
            raise ConfigError(f"camera {name!r} entry missing required 'driver' key")

    for key in _OPTIONAL_TOP_LEVEL:
        if key in config and not isinstance(config[key], dict):
            raise ConfigError(f"rig config '{key}' section must be a mapping if present")

    return config


def load_rig_config(path: str | Path) -> dict[str, Any]:
    """Load and validate the rig config from ``path``."""
    return validate_rig_config(load_yaml(path))


def enabled_cameras(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the subset of cameras with ``enabled: true`` (default true if omitted)."""
    cameras = config.get("cameras", {})
    return {name: cam for name, cam in cameras.items() if cam.get("enabled", True)}
