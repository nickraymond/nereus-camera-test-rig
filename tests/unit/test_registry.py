"""Unit tests for the camera driver registry — Spec §5, §7."""

from __future__ import annotations

import pytest

from nereus_camera_test_rig.cameras import registry
from nereus_camera_test_rig.cameras.base import CameraDevice


class _FakeCamera(CameraDevice):
    driver = "fake"

    def __init__(self, tag="default"):
        self.tag = tag


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


def test_register_and_create():
    registry.register("fake", _FakeCamera)
    cam = registry.create("fake", tag="hello")
    assert isinstance(cam, _FakeCamera)
    assert cam.tag == "hello"


def test_available_is_sorted():
    registry.register("zeta", _FakeCamera)
    registry.register("alpha", _FakeCamera)
    assert registry.available() == ["alpha", "zeta"]


def test_duplicate_registration_raises():
    registry.register("fake", _FakeCamera)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("fake", _FakeCamera)


def test_empty_name_raises():
    with pytest.raises(ValueError, match="non-empty"):
        registry.register("", _FakeCamera)


def test_unknown_driver_raises_with_known_list():
    registry.register("fake", _FakeCamera)
    with pytest.raises(KeyError, match="fake"):
        registry.create("does_not_exist")


def test_is_registered_and_unregister():
    registry.register("fake", _FakeCamera)
    assert registry.is_registered("fake")
    registry.unregister("fake")
    assert not registry.is_registered("fake")
