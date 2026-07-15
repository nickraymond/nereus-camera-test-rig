"""Unit tests for controller.capture_once — Spec §11 (single-capture slice)."""

from __future__ import annotations

import json

import pytest
from _capture_helpers import FakeRunner

from nereus_camera_test_rig.cameras import registry
from nereus_camera_test_rig.cameras.imx708 import Imx708Camera
from nereus_camera_test_rig.controller import capture_once


def _fake_factory(runner):
    def factory(settings=None):
        return Imx708Camera(settings=settings, still_command="rpicam-still", runner=runner)

    return factory


@pytest.fixture
def config():
    return {
        "rig": {"id": "test-rig"},
        "cameras": {"imx708": {"enabled": True, "driver": "imx708"}},
    }


@pytest.fixture(autouse=True)
def _fake_imx708():
    # Register an imx708 driver backed by a fake runner so capture_once needs no hardware.
    registry.clear()
    registry.register("imx708", _fake_factory(FakeRunner()))
    yield
    registry.clear()


def test_capture_once_writes_artifact_and_metadata(config, tmp_path):
    outcome = capture_once(config, "imx708", "image", tmp_path)
    assert outcome.ok
    assert outcome.output_path and outcome.output_path.endswith(".jpg")
    assert outcome.metadata_path and outcome.metadata_path.endswith(".json")

    # Sidecar is real, self-contained, and records sensor metadata (Spec §5).
    meta = json.loads(open(outcome.metadata_path).read())
    assert meta["status"] == "completed"
    assert meta["output"]["width"] == 4608
    assert meta["camera"]["sensor"] == "imx708"
    assert meta["sensor_metadata"]["ExposureTime"] == 13539


def test_capture_once_unknown_camera_raises(config, tmp_path):
    with pytest.raises(KeyError):
        capture_once(config, "does_not_exist", "image", tmp_path)


def test_capture_once_failure_is_partial_not_exception(config, tmp_path):
    # A failing camera returns a failed outcome, never raises (Spec §11).
    registry.clear()
    registry.register("imx708", _fake_factory(FakeRunner(returncode=1)))
    outcome = capture_once(config, "imx708", "image", tmp_path)
    assert not outcome.ok
    assert outcome.result.error["code"] == "capture_failed"
