"""Pool spec §5.1: the depth-reader interface and its fake source."""

import pytest

from nereus_camera_test_rig.sensors.depth import (
    FakeDepthSensor,
    build_depth_sensor,
    safe_read,
)


def test_none_means_no_sensor():
    assert build_depth_sensor(None) is None
    assert build_depth_sensor("none") is None
    assert safe_read(None) is None


def test_fake_is_labelled_and_empty_by_default():
    r = build_depth_sensor("fake").read().to_dict()
    assert r["ok"] and r["source"] == "fake" and r["depth_m"] is None


def test_fake_with_value_for_dry_runs():
    assert build_depth_sensor("fake:1.8").read().depth_m == 1.8


def test_unknown_sensor_is_refused():
    with pytest.raises(ValueError, match="only 'fake'"):
        build_depth_sensor("ms5837:i2c-1")


def test_a_raising_driver_becomes_a_failed_reading():
    class Broken(FakeDepthSensor):
        def read(self):
            raise OSError("i2c timeout")
    r = safe_read(Broken())
    assert r["ok"] is False and "i2c timeout" in r["error"]
