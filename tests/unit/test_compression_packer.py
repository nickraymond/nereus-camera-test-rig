"""Lossless Bayer-plane packer (compression study): Python reference round trip, C == Python
byte for byte, the LIMIT-bits worst-case bound, and (opt-in) real S4 frames.

Real-data check: ``NEREUS_S4_DATA=<repo>/data/s4_20260930 pytest -s ...`` prints bits/sample.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import pytest
from compression_study.methods import packer

DEPTHS = (1, 8, 10, 12, 16)
HAS_CC = shutil.which("cc") is not None


def _planes(b: int) -> dict[str, np.ndarray]:
    """Noise (worst case, exercises the escape path), gradients, constants, odd shapes."""
    rng = np.random.default_rng(1000 + b)
    top = (1 << b) - 1
    yy, xx = np.mgrid[0:29, 0:41]
    ramp = ((xx * 7 + yy * 3) * top // (7 * 40 + 3 * 28)).astype(np.uint16)
    return {
        "noise": rng.integers(0, top + 1, size=(48, 64), dtype=np.uint16),
        "gradient": ramp,
        "gradient_noisy": np.clip(ramp + rng.integers(-2, 3, ramp.shape), 0, top)
        .astype(np.uint16),
        "const_zero": np.zeros((9, 13), np.uint16),
        "const_top": np.full((9, 13), top, np.uint16),
        "one": np.array([[top]], np.uint16),
        "row": rng.integers(0, top + 1, size=(1, 37), dtype=np.uint16),
        "column": rng.integers(0, top + 1, size=(23, 1), dtype=np.uint16),
        "odd": rng.integers(0, top + 1, size=(17, 31), dtype=np.uint16),
        # extremes alternating: residuals at the modulo wrap edges
        "checker": (((xx + yy) % 2) * top).astype(np.uint16),
    }


CASES = [(b, name) for b in DEPTHS for name in _planes(b)]


@pytest.fixture(scope="module")
def c_binary():
    if not HAS_CC:
        pytest.skip("no C compiler (cc) on PATH")
    return packer.build_c()


@pytest.mark.parametrize("b,name", CASES)
def test_py_round_trip_and_bound(b, name):
    plane = _planes(b)[name]
    data = packer.encode_plane(plane, b, impl="py")
    h, w = plane.shape
    assert len(data) <= packer.max_plane_bytes(w, h, b)
    back = packer.decode_plane(data, w, h, b, impl="py")
    assert back.dtype == np.uint16
    np.testing.assert_array_equal(back, plane)


@pytest.mark.parametrize("b,name", CASES)
def test_c_matches_python_and_round_trips(c_binary, b, name):
    plane = _planes(b)[name]
    h, w = plane.shape
    data_c = packer.encode_plane(plane, b, impl="c")
    assert data_c == packer.encode_plane(plane, b, impl="py")
    np.testing.assert_array_equal(packer.decode_plane(data_c, w, h, b, impl="c"), plane)


def test_noise_hits_escape_and_stays_near_bound():
    # Uniform 16-bit noise costs more than b bits per sample but never more than LIMIT.
    plane = _planes(16)["noise"]
    h, w = plane.shape
    data = packer.encode_plane(plane, 16, impl="py")
    assert 16 * w * h / 8 < len(data) <= packer.max_plane_bytes(w, h, 16)


def test_constant_plane_is_about_one_bit_per_sample():
    plane = np.full((32, 32), 77, np.uint16)
    assert len(packer.encode_plane(plane, 8, impl="py")) <= 32 * 32 // 8 + 32


@pytest.mark.parametrize("impl", ["py", "c"])
def test_corrupt_streams_fail_loudly(impl, request):
    if impl == "c":
        request.getfixturevalue("c_binary")
    plane = _planes(10)["odd"]
    h, w = plane.shape
    data = packer.encode_plane(plane, 10, impl="py")
    with pytest.raises(packer.PackerError):
        packer.decode_plane(data[:-3], w, h, 10, impl=impl)  # truncated
    with pytest.raises(packer.PackerError):
        packer.decode_plane(data + b"\x00", w, h, 10, impl=impl)  # trailing byte
    with pytest.raises(packer.PackerError):
        packer.decode_plane(b"\x00" * 8 + data, w, h, 10, impl=impl)  # zero run > qmax


def test_rejects_out_of_range_input():
    with pytest.raises(packer.PackerError):
        packer.encode_plane(np.array([[256]], np.uint16), 8, impl="py")
    with pytest.raises(packer.PackerError):
        packer.encode_plane(np.zeros((2, 2), np.uint16), 17, impl="py")


def test_static_memory_and_limit():
    assert packer.limit_bits(8) == 32 and packer.limit_bits(16) == 64
    assert packer.static_memory_bytes(640, 8) == 2 * 640 * 2 + packer.STATE_BYTES


# --------------------------------------------------------------------------- real data (opt-in)

S4 = os.environ.get("NEREUS_S4_DATA")


def _split(mosaic: np.ndarray) -> list[np.ndarray]:
    return [np.ascontiguousarray(mosaic[dy::2, dx::2]) for dy in (0, 1) for dx in (0, 1)]


def _real_frames():
    from nereus_camera_test_rig.color.raw_io import read_dng, read_openmv_bayer

    root = Path(S4)
    n6 = read_openmv_bayer(root / "cool_n6" / "stop_-1_r0.bayer")
    imx = read_dng(root / "cool_imx708" / "stop_-1_r0.dng")
    mosaic, _, _ = imx.active()
    H, W = mosaic.shape
    y0, x0 = (H // 2 - 256) & ~1, (W // 2 - 256) & ~1  # centred, CFA phase kept
    return [("n6", n6.mosaic, int(n6.white_level).bit_length()),
            ("imx708_512", mosaic[y0:y0 + 512, x0:x0 + 512], int(imx.white_level).bit_length())]


@pytest.mark.skipif(not S4, reason="set NEREUS_S4_DATA to the s4_20260930 folder")
def test_real_frames_round_trip(c_binary):
    for name, mosaic, b in _real_frames():
        assert mosaic.max() < (1 << b)
        total = 0
        for plane in _split(mosaic):
            h, w = plane.shape
            data = packer.encode_plane(plane, b, impl="py")
            assert packer.encode_plane(plane, b, impl="c") == data
            np.testing.assert_array_equal(packer.decode_plane(data, w, h, b, impl="py"), plane)
            np.testing.assert_array_equal(packer.decode_plane(data, w, h, b, impl="c"), plane)
            total += len(data)
        print(f"\n{name}: {mosaic.shape[1]}x{mosaic.shape[0]} b={b} -> {total} B, "
              f"{8 * total / mosaic.size:.3f} bits/sample")
