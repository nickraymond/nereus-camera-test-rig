"""Pool spec §5.2: exposure-lock parsing and merging (pure logic; metering itself is
hardware, verified on nereus002)."""

import json

import pytest

from nereus_camera_test_rig.capture.exposure_lock import (
    imx708_override,
    load_lock,
    merge_settings,
    openmv_override,
    overrides_from_lock,
)

IMX_SUMMARY = {"passed": True, "gain_applied": 1.122807, "locked_exposure_us_at_stop0": 85838,
               "meter": {"ColourGains": [1.9715, 2.1234], "LensPosition": 0.2408},
               "shots": [{"stop": 0, "card": {"level": 0.79}}]}
OMV_SUMMARY = {"passed": True, "locked": {"requested_us": 8000, "exposure_us": 7990,
                                          "gain_db": 3.15, "reference": {"level": 0.85}}}


def test_imx708_override_locks_shutter_gain_awb_focus():
    o = imx708_override(IMX_SUMMARY)["camera_controls"]
    assert o["exposure"] == {"shutter_us": 85838, "analogue_gain": 1.122807}
    assert o["white_balance"] == {"red_gain": 1.9715, "blue_gain": 2.1234}
    assert o["focus"] == {"mode": "manual", "lens_position": 0.2408}


def test_imx708_override_without_lens_position_leaves_focus_alone():
    s = {**IMX_SUMMARY, "meter": {"ColourGains": [2.0, 2.0]}}
    assert "focus" not in imx708_override(s)["camera_controls"]


def test_openmv_override_uses_readback_values():
    assert openmv_override(OMV_SUMMARY) == {"exposure_us": 7990, "gain_db": 3.15}


def test_merge_is_deep_and_does_not_mutate():
    base = {"warmup_ms": 2000, "camera_controls": {"exposure": {"shutter_us": None},
                                                   "white_balance": {"mode": "auto"}}}
    out = merge_settings(base, imx708_override(IMX_SUMMARY))
    assert out["warmup_ms"] == 2000
    assert out["camera_controls"]["exposure"]["shutter_us"] == 85838
    assert out["camera_controls"]["white_balance"] == {"mode": "auto", "red_gain": 1.9715,
                                                       "blue_gain": 2.1234}
    assert base["camera_controls"]["exposure"]["shutter_us"] is None
    assert merge_settings(base, None) == base


def test_overrides_only_for_locked_cameras(tmp_path):
    lock = {"cameras": {"imx708": {"locked": True, "settings": {"a": 1}},
                        "openmv_ae3": {"locked": False, "reason": "card not found"}}}
    p = tmp_path / "exposure_lock.json"
    p.write_text(json.dumps(lock))
    assert overrides_from_lock(load_lock(p)) == {"imx708": {"a": 1}}


def test_load_lock_rejects_other_json(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{}")
    with pytest.raises(ValueError):
        load_lock(p)
