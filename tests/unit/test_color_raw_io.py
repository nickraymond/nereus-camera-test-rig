"""RAW contract + L0 decode (SPEC §4 Phase 8 S0): synthetic mosaics with known answers for
every Bayer pattern, per-position black levels, clipping, crop phase and exposure."""

from __future__ import annotations

import numpy as np
import pytest

from nereus_camera_test_rig.color.raw_io import (
    PATTERNS,
    RawFrame,
    bin2x2,
    cfa_shift,
    demosaic_bilinear,
    normalize,
)

WHITE = 4095.0
CH = {"R": 0, "G": 1, "B": 2}


def make_mosaic(rgb_fn, cfa, shape=(8, 12), black=(256, 257, 258, 259), white=WHITE):
    """Mosaic whose pixel (y, x) samples channel cfa[y%2, x%2] of rgb_fn(y, x) (0..1 linear)."""
    h, w = shape
    out = np.zeros(shape, dtype=np.uint16)
    for y in range(h):
        for x in range(w):
            pos = (y % 2) * 2 + (x % 2)
            value = rgb_fn(y, x)[CH[cfa[pos]]]
            out[y, x] = round(black[pos] + value * (white - black[pos]))
    return out


def frame(mosaic, cfa, black=(256, 257, 258, 259), **kw):
    return RawFrame(mosaic=mosaic, cfa=cfa, black_level=black, white_level=WHITE, **kw)


FLAT = (0.20, 0.45, 0.10)


@pytest.mark.parametrize("cfa", PATTERNS)
def test_flat_field_recovered_by_both_paths_for_every_pattern(cfa):
    f = frame(make_mosaic(lambda y, x: FLAT, cfa), cfa)
    linear, saturated, active_cfa = normalize(f)
    assert active_cfa == cfa and not saturated.any()

    binned, clip = bin2x2(linear, cfa, saturated)
    assert binned.shape == (4, 6, 3) and not clip.any()
    np.testing.assert_allclose(binned, np.broadcast_to(FLAT, binned.shape), atol=2e-4)

    full = demosaic_bilinear(linear, cfa)  # exact at the borders too (normalized conv.)
    np.testing.assert_allclose(full, np.broadcast_to(FLAT, full.shape), atol=2e-4)


@pytest.mark.parametrize("cfa", PATTERNS)
def test_demosaic_keeps_native_samples_and_is_exact_on_a_linear_ramp(cfa):
    def ramp(y, x):
        return (0.01 * x + 0.02 * y, 0.3 + 0.015 * x, 0.5 - 0.01 * y)

    linear, _, _ = normalize(frame(make_mosaic(ramp, cfa, shape=(10, 10)), cfa))
    full = demosaic_bilinear(linear, cfa)
    for y in range(10):
        for x in range(10):
            native = CH[cfa[(y % 2) * 2 + (x % 2)]]
            assert full[y, x, native] == pytest.approx(linear[y, x], abs=1e-6)
    expected = np.array([[ramp(y, x) for x in range(10)] for y in range(10)])
    np.testing.assert_allclose(full[1:-1, 1:-1], expected[1:-1, 1:-1], atol=5e-4)


def test_per_position_black_level_is_applied_per_position():
    black = (250, 260, 270, 280)
    mosaic = np.array([[250, 260], [270, 280]] * 2, dtype=np.uint16).reshape(4, 2)
    mosaic = np.tile(mosaic, (1, 2))
    linear, _, _ = normalize(frame(mosaic, "GRBG", black=black))
    assert np.abs(linear).max() == 0.0  # every pixel sits exactly on its own black level


def test_negatives_below_black_are_kept_for_unbiased_means():
    mosaic = np.full((4, 4), 250, dtype=np.uint16)  # 6-9 counts below black
    linear, _, _ = normalize(frame(mosaic, "RGGB"))
    assert (linear < 0).all()


def test_saturated_pixels_flag_the_right_binned_channel():
    cfa = "RGGB"
    mosaic = make_mosaic(lambda y, x: FLAT, cfa)
    mosaic[0, 1] = int(WHITE)  # a G sample in binned cell (0, 0)
    mosaic[3, 3] = int(WHITE)  # the B sample in binned cell (1, 1)
    linear, saturated, _ = normalize(frame(mosaic, cfa))
    _, clip = bin2x2(linear, cfa, saturated)
    assert clip[0, 0].tolist() == [False, True, False]
    assert clip[1, 1].tolist() == [False, False, True]
    assert clip.sum() == 2


def test_valid_crop_with_odd_offset_shifts_the_pattern_and_black_grid():
    cfa, black = "RGGB", (256, 257, 258, 259)
    mosaic = make_mosaic(lambda y, x: FLAT, cfa, shape=(9, 13), black=black)
    f = frame(mosaic, cfa, black=black, valid_crop=(1, 1, 12, 8))
    raw, active_cfa, grid = f.active()
    assert raw.shape == (8, 12) and active_cfa == "BGGR" == cfa_shift(cfa, 1, 1)
    assert grid.tolist() == [[259, 258], [257, 256]]
    linear, _, active = normalize(f)
    binned, _ = bin2x2(linear, active)
    np.testing.assert_allclose(binned, np.broadcast_to(FLAT, binned.shape), atol=2e-4)


def test_exposure_factor():
    f = frame(np.zeros((2, 2), np.uint16), "RGGB", exposure_s=0.01, iso=200, fnumber=2.0)
    assert f.exposure_factor() == pytest.approx(0.5)
    with pytest.raises(ValueError, match="missing"):
        frame(np.zeros((2, 2), np.uint16), "RGGB", iso=100).exposure_factor()


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"cfa": "RGBG"}, "cfa must be one of"),
        ({"black_level": (256, 257, 258, 5000)}, "below white"),
        ({"valid_crop": (0, 0, 5, 2)}, "outside mosaic"),
        ({"mosaic": np.zeros((2, 2), np.float32)}, "integer"),
    ],
)
def test_invalid_frames_fail_loudly(kwargs, message):
    base = dict(mosaic=np.zeros((2, 4), np.uint16), cfa="RGGB",
                black_level=(256, 257, 258, 259), white_level=WHITE)
    with pytest.raises(ValueError, match=message):
        RawFrame(**{**base, **kwargs})
