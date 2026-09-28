"""Card-metered exposure math (S3 locked-exposure recipe): pure logic, no camera."""

import pytest

from nereus_camera_test_rig.color.raw_meter import card_reference, exposure_for_target


def test_linear_scaling_onto_target():
    plan = exposure_for_target(6000, level=0.6, target=0.8)
    assert plan["exposure_us"] == 8000 and not plan["clamped"] and not plan["remeter"]


def test_clipped_reference_halves_and_remeters():
    plan = exposure_for_target(6000, level=1.0, target=0.8, clipped=True)
    assert plan["exposure_us"] == 3000 and plan["remeter"]


def test_clamped_to_sensor_floor():
    plan = exposure_for_target(100, level=0.9, target=0.1)
    assert plan["exposure_us"] == 80 and plan["clamped"] and plan["wanted_us"] == 11


def test_zero_level_rejected():
    with pytest.raises(ValueError):
        exposure_for_target(6000, level=0.0)


def test_card_reference_picks_brightest_white_channel():
    levels = {"patches": {"gray_light": {"mean": [0.3, 0.5, 0.4]},
                          "gray_white": {"mean": [0.4, 0.62, 0.5], "clip_frac": [0, 0.02, 0]}}}
    ref = card_reference(levels)
    assert (ref["patch"], ref["channel"], ref["level"], ref["clipped"]) == (
        "gray_white", "G", 0.62, True)
