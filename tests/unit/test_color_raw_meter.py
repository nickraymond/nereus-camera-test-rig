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


@pytest.mark.parametrize("crop", [None, (1, 1, 22, 14)])
def test_decimate_cells_keeps_cfa_phase_and_levels(crop):
    import numpy as np

    from nereus_camera_test_rig.color.raw_io import RawFrame
    from nereus_camera_test_rig.color.raw_meter import decimate_cells

    # Value = its CFA position code (0..3) + 10 x black-position, so a phase slip shows up.
    pos = (np.arange(16)[:, None] % 2) * 2 + (np.arange(24)[None, :] % 2)
    frame = RawFrame(mosaic=(100 + pos).astype(np.uint16), cfa="GRBG",
                     black_level=(10, 11, 12, 13), white_level=1023, valid_crop=crop)
    small = decimate_cells(frame, max_pixels=100)
    assert small.source["decimated_cells"] >= 2
    raw, cfa, black = frame.active()
    assert small.cfa == cfa  # phase follows the valid-area origin
    assert small.mosaic[0, 0] == raw[0, 0] and small.mosaic[1, 1] == raw[1, 1]
    assert small.black_level == tuple(float(v) for v in black.ravel())
    assert small.mosaic.size <= 100 and decimate_cells(frame) is frame  # small frame: as is
