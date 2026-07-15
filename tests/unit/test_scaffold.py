"""Scaffold integrity — every placeholder imports and every shipped YAML parses.

This guards the Phase 0 exit criterion: the tree is importable and internally
consistent even though most modules are not implemented yet.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

# Host-side package modules that must at least import cleanly (Spec §6).
PACKAGE_MODULES = [
    "nereus_camera_test_rig",
    "nereus_camera_test_rig.cli",
    "nereus_camera_test_rig.config",
    "nereus_camera_test_rig.models",
    "nereus_camera_test_rig.logging_config",
    "nereus_camera_test_rig.controller",
    "nereus_camera_test_rig.cameras.base",
    "nereus_camera_test_rig.cameras.registry",
    "nereus_camera_test_rig.cameras.imx708",
    "nereus_camera_test_rig.cameras.openmv_usb",
    "nereus_camera_test_rig.cameras.builtin",
    "nereus_camera_test_rig.capture.naming",
    "nereus_camera_test_rig.capture.image_probe",
    "nereus_camera_test_rig.capture.coordinator",
    "nereus_camera_test_rig.capture.image_capture",
    "nereus_camera_test_rig.capture.video_capture",
    "nereus_camera_test_rig.storage.checksums",
    "nereus_camera_test_rig.storage.experiment_store",
    "nereus_camera_test_rig.storage.metadata",
    "nereus_camera_test_rig.analysis.apriltag_detector",
    "nereus_camera_test_rig.analysis.reference_card",
    "nereus_camera_test_rig.analysis.crop",
    "nereus_camera_test_rig.analysis.image_metrics",
    "nereus_camera_test_rig.analysis.result_writer",
    "nereus_camera_test_rig.web.app",
]


@pytest.mark.parametrize("module", PACKAGE_MODULES)
def test_module_imports(module):
    importlib.import_module(module)


def test_camera_adapters_expose_driver():
    from nereus_camera_test_rig.cameras.imx708 import Imx708Camera
    from nereus_camera_test_rig.cameras.openmv_usb import OpenMvUsbCamera

    assert Imx708Camera.driver == "imx708"
    assert OpenMvUsbCamera.driver == "openmv_usb"


def test_openmv_adapter_fails_loudly_without_device():
    # openmv_usb is implemented in Phase 3. With no board reachable it must fail loudly
    # with a structured OpenMvError, never silently no-op.
    from nereus_camera_test_rig.cameras.openmv_usb import OpenMvError, OpenMvUsbCamera

    with pytest.raises(OpenMvError):
        OpenMvUsbCamera(serial_number="does-not-exist").get_device_info()


@pytest.mark.parametrize("yaml_path", sorted(str(p) for p in Path("configs").rglob("*.yaml")))
def test_shipped_yaml_parses(yaml_path):
    data = yaml.safe_load(Path(yaml_path).read_text())
    assert isinstance(data, dict), f"{yaml_path} should parse to a mapping"
