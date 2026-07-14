"""Shared pytest fixtures for host-side unit tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.fixture
def fixed_utc() -> datetime:
    """A fixed, timezone-aware UTC instant for deterministic naming tests."""
    return datetime(2026, 7, 14, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def minimal_rig_config() -> dict:
    """A minimal valid rig config (Spec §12)."""
    return {
        "rig": {"id": "nereus-camera-rig-001", "results_directory": "./results"},
        "cameras": {
            "imx708": {"enabled": True, "driver": "imx708"},
            "openmv_n6": {"enabled": False, "driver": "openmv_usb", "board": "n6"},
        },
        "analysis": {"apriltag": {"enabled": True, "expected_tag_ids": [0, 1, 2, 3]}},
        "web": {"host": "0.0.0.0", "port": 8080},
    }
