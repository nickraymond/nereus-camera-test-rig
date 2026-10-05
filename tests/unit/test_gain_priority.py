"""Nick's "ISO 100" rule (2026-10-05): lowest gain, shutter up to a cap, then gain."""

from nereus_camera_test_rig.color.raw_meter import gain_priority


def test_under_the_cap_keeps_the_gain_floor():
    r = gain_priority(10_000, 16_667, 1.1228, 16.0)
    assert r == {"exposure_us": 10_000, "gain": 1.1228, "capped": False,
                 "rule": "gain floor, shutter only"}


def test_over_the_cap_moves_the_rest_into_gain_same_total_exposure():
    r = gain_priority(85_838, 16_667, 1.1228, 16.0)
    assert r["capped"] and r["exposure_us"] == 16_667
    assert abs(r["gain"] * 16_667 - 1.1228 * 85_838) < 1e-6 * 85_838
    assert not r["gain_clamped"]


def test_gain_is_clamped_at_the_sensor_ceiling():
    r = gain_priority(1_000_000, 16_667, 1.1228, 16.0)
    assert r["gain"] == 16.0 and r["gain_clamped"]


def test_no_cap_means_shutter_only():
    assert not gain_priority(500_000, 0, 1.1228, 16.0)["capped"]
