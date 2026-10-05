"""Exposure sweep (pool tool, 2026-10-05): toggle, scoring and the best-frame pick (pure logic;
the capture side is verified on nereus002)."""

import numpy as np

from nereus_camera_test_rig.capture.coordinator import DEFAULT_SWEEP_SHUTTERS_US, sweep_settings
from nereus_camera_test_rig.color.exposure_sweep import pick, score_frame
from nereus_camera_test_rig.color.raw_io import RawFrame


def _frames(sharp, clipped=None):
    clipped = clipped or [False] * len(sharp)
    return [{"shutter_us": s, "scores": {"sharpness": {"centre": v}, "clipped": c,
                                         "level_p995": 0.5}}
            for s, v, c in zip((4000, 8000, 16667, 33333, 66667), sharp, clipped)]


def test_sweep_is_off_by_default_and_on_by_flag():
    assert sweep_settings({}, None) is None
    assert sweep_settings({"exposure_sweep": {"enabled": False}}, None) is None
    s = sweep_settings({}, {"enabled": True})
    assert s["shutters_us"] == list(DEFAULT_SWEEP_SHUTTERS_US) and s["tolerance"] == 0.10
    assert sweep_settings({"exposure_sweep": {"enabled": True, "shutters_us": [1000]}},
                          None)["shutters_us"] == [1000]


def test_pick_takes_the_longest_shutter_that_stays_sharp():
    p = pick(_frames([1.00, 0.99, 0.95, 0.70, 0.40]))
    assert p["shutter_us"] == 16667 and p["metric"] == "centre"
    assert "blurred" in p["reason"]


def test_pick_skips_clipped_frames():
    p = pick(_frames([1.0, 1.0, 1.0, 1.0, 1.0], [False, False, False, True, True]))
    assert p["shutter_us"] == 16667 and "clipped" in p["reason"]


def test_pick_with_nothing_usable():
    assert pick(_frames([1.0] * 5, [True] * 5))["index"] is None


def test_too_dark_frames_are_not_judged():
    f = _frames([3.0, 2.0, 1.0, 1.0, 0.98])
    for x in f[:2]:
        x["scores"]["level_p995"] = 0.01   # dark, noisy frames must not set the bar
    p = pick(f)
    assert p["shutter_us"] == 66667 and "too dark" in p["reason"]


def _mosaic(img):
    """Grey image (H, W) in 0..1 -> 10-bit RGGB RawFrame (all sites the same value)."""
    m = (64 + img * (1023 - 64)).astype(np.uint16)
    return RawFrame(mosaic=m, cfa="RGGB", black_level=(64.0,) * 4, white_level=1023)


def test_motion_blur_lowers_sharpness():
    rng = np.random.default_rng(0)
    img = np.clip(0.4 + 0.2 * (rng.random((400, 600)) > 0.5), 0, 1)
    img = np.kron(img[:50, :75], np.ones((8, 8)))         # blocky texture, 400x600
    k = 15
    blurred = np.mean([np.roll(img, s, axis=1) for s in range(k)], axis=0)  # horizontal motion
    sharp = score_frame(_mosaic(img))["sharpness"]["centre"]
    soft = score_frame(_mosaic(blurred))["sharpness"]["centre"]
    assert soft < 0.7 * sharp   # horizontal motion: vertical edges go, horizontal ones stay


def test_noise_alone_does_not_look_sharp():
    rng = np.random.default_rng(2)
    flat = np.full((400, 600), 0.02) + rng.normal(0, 0.004, (400, 600))
    assert not score_frame(_mosaic(np.clip(flat, 0, 1)))["sharpness"]["centre"]   # None


def test_sharpness_does_not_grow_with_brightness():
    rng = np.random.default_rng(1)
    img = np.kron(0.2 + 0.1 * (rng.random((50, 75)) > 0.5), np.ones((8, 8)))
    a = score_frame(_mosaic(img))["sharpness"]["centre"]
    b = score_frame(_mosaic(img * 2))["sharpness"]["centre"]
    assert abs(a / b - 1) < 0.05


def test_roi_replaces_the_centre_fallback():
    img = np.zeros((400, 600))
    img[:, :300] = 1.0                                    # a clipped "light panel", left half
    img[100:300, 350:550] = 0.3                           # dimmer scene on the right
    centre = score_frame(_mosaic(img))
    roi = score_frame(_mosaic(img), roi=(340, 90, 220, 220))
    assert centre["clipped"]                              # the centre sees the panel
    assert roi["clip_frac"] == 0 and roi["level_p995"] < 0.5


def test_roi_outside_the_frame_falls_back_to_the_centre():
    img = np.full((400, 600), 0.3)
    r = score_frame(_mosaic(img), roi=(1504, 846, 1600, 900))   # IMX708 px on a small frame
    assert r["fallback"].startswith("frame centre")


def test_per_camera_roi():
    s = sweep_settings({}, {"enabled": True, "roi": {"imx708": [1, 2, 3, 4]}}, "openmv_n6")
    assert s["roi"] is None
    assert sweep_settings({}, {"enabled": True, "roi": {"imx708": [1, 2, 3, 4]}},
                          "imx708")["roi"] == [1, 2, 3, 4]


def test_per_camera_ladder_and_repeats():
    o = {"enabled": True, "repeats": 3,
         "shutters_us": {"openmv_ae3": [250, 500], "_default": [4000, 8000]}}
    ae3 = sweep_settings({}, o, "openmv_ae3")
    assert ae3["shutters_us"] == [250, 500] and ae3["repeats"] == 3
    assert sweep_settings({}, o, "imx708")["shutters_us"] == [4000, 8000]
