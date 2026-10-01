"""Raw compression study: header, curves, plane layout, lossless round trips, rate search.

Codec-backed tests skip when the tool is missing (cjxl, cjpeg, the study's imagecodecs).
"""

from __future__ import annotations

import numpy as np
import pytest
from compression_study import rate
from compression_study.common import (
    Header,
    Raw,
    has_tool,
    merge,
    pack,
    split,
    sqrt_inverse,
    sqrt_lut,
    tile,
    unpack,
    untile,
)
from compression_study.methods import raw_planes as rp


def _raw(h=32, w=48, white=1023, black=64, cfa="BGGR", seed=0) -> Raw:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = black + (xx * 7 + yy * 3) % (white - black)
    noise = rng.integers(-3, 4, size=(h, w))
    mos = np.clip(base + noise, black, white).astype(np.uint16)
    bits = 8 if white <= 255 else 10
    return Raw(mosaic=mos, cfa=cfa, black=black, white=white, bits=bits, camera="test")


def test_header_round_trip_and_size():
    h = Header("D2", 4608, 2592, "BGGR", 64, 1023, 12, 0, 5, params=[4096, -123, 7000],
               lengths=[])
    blob = pack(h, [b"abc", b"", b"defg", b"h"])
    h2, payloads = unpack(blob)
    assert (h2.method, h2.w, h2.h, h2.cfa, h2.black, h2.white, h2.b, h2.flags) == \
        ("D2", 4608, 2592, "BGGR", 64, 1023, 12, 5)
    assert h2.params == [4096, -123, 7000]
    assert payloads == [b"abc", b"", b"defg", b"h"]
    assert len(blob) - 8 <= 32  # header ≤ 32 B for a 4-plane method with 3 params


@pytest.mark.parametrize("cfa", ["RGGB", "BGGR", "GRBG", "GBRG"])
def test_split_merge_tile_are_inverses(cfa):
    raw = _raw(cfa=cfa)
    planes = split(raw.mosaic, cfa)
    assert np.array_equal(merge(planes, cfa), raw.mosaic)
    t = untile(tile(planes))
    assert all(np.array_equal(t[k], planes[k]) for k in planes)


def test_sqrt_lut_monotone_and_error_below_half_step():
    black, white, b = 64, 1023, 8
    lut = sqrt_lut(black, white, b)
    assert lut[black] == 0 and lut[white] == 2 ** b - 1
    assert np.all(np.diff(lut.astype(int)) >= 0)
    v = np.arange(black, white + 1)
    back = sqrt_inverse(lut[v], black, white, b)
    # rounding in sqrt space: error ≤ half a code step mapped back, ~ sqrt(v) / S
    step = 2 * np.sqrt(np.maximum(v - black, 1)) * np.sqrt(white - black) / (2 ** b - 1)
    assert np.all(np.abs(back - v) <= step / 2 + 1)


@pytest.mark.parametrize("white", [255, 1023])
def test_packer_lossless_method_is_bit_exact(white):
    raw = _raw(white=white, black=0 if white == 255 else 64)
    blob = rp.encode(raw, rp.RawSpec("C"))
    assert np.array_equal(rp.decode(blob), raw.mosaic.astype(np.float64))


def test_near_lossless_n_round_trip_matches_curve():
    raw = _raw()
    blob = rp.encode(raw, rp.RawSpec("N", "sqrt", 8))
    rec = rp.decode(blob)
    lut = sqrt_lut(raw.black, raw.white, 8)
    expect = sqrt_inverse(lut[raw.mosaic], raw.black, raw.white, 8)
    assert np.allclose(rec, expect)


@pytest.mark.skipif(not has_tool("cjxl"), reason="cjxl not installed")
def test_jxl_lossless_and_lossy_decode_from_bytes_alone():
    raw = _raw()
    lossless = rp.encode(raw, rp.RawSpec("C2", effort=3))
    assert np.array_equal(rp.decode(lossless), raw.mosaic.astype(np.float64))
    lossy = rp.encode(raw, rp.RawSpec("D2", "sqrt", 12, layout="3pl"),
                      {k: 1.0 for k in ("R", "G", "B")})
    rec = rp.decode(lossy)
    assert rec.shape == raw.mosaic.shape and not np.array_equal(rec, raw.mosaic)


@pytest.mark.skipif(not has_tool("cjpeg"), reason="cjpeg not installed")
def test_grayscale_jpeg_planes_round_trip_shape():
    raw = _raw()
    blob = rp.encode(raw, rp.RawSpec("D", "sqrt", 8), {k: 90 for k in ("R", "G1", "G2", "B")})
    assert rp.decode(blob).shape == raw.mosaic.shape


def test_rate_solver_hits_targets_on_monotone_codecs():
    size_f = lambda d: int(100_000 / (d ** 1.3))  # noqa: E731 — fake JPEG XL distance curve
    sol = rate.solve(size_f, 20_000, 0.05, 25.0, -1, False, start=2.0)
    assert sol.reachable and abs(size_f(sol.knobs[0]) / 20_000 - 1) <= rate.TOL_HIT
    size_q = lambda q: 1000 + q * q * 7  # noqa: E731 — fake JPEG quality curve (integer)
    sol = rate.solve(size_q, 40_000, 1, 100, +1, True)
    sizes = [size_q(k) for k in sol.knobs]
    assert min(sizes) <= 40_000 * 1.05 and max(sizes) >= 40_000 * 0.95
    assert not rate.solve(size_q, 10 ** 9, 1, 100, +1, True).reachable


@pytest.mark.skipif(not has_tool("cc"), reason="no C compiler")
def test_wl53_round_trip_is_deterministic_and_bounded():
    from compression_study.methods import plane_codecs as pc

    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:37, 0:53]
    plane = np.clip(2000 + 900 * np.sin(xx / 5.0) + rng.normal(0, 30, xx.shape), 0, 4095)
    plane = plane.astype(np.uint16)
    dec = pc.wl53_dec_factory(53, 37)
    prev = None
    for q in (0.5, 4.0, 40.0):
        a, _ = pc.wl53_enc(plane, 4095, q)
        b, _ = pc.wl53_enc(plane, 4095, q)
        assert a == b and a[:2] == b"W\x01"  # same bytes every run; header magic + version
        err = np.abs(dec(a).astype(int) - plane.astype(int))
        assert err.max() <= max(2, 8 * q)  # coarser scale, larger (bounded) error
        if prev is not None:
            assert len(a) < prev  # bigger scale → fewer bytes
        prev = len(a)


def test_method_w_decodes_from_bytes_alone():
    if not has_tool("cc"):
        pytest.skip("no C compiler")
    raw = _raw()
    blob = rp.encode(raw, rp.RawSpec("W", "sqrt", 12), {k: 2.0 for k in ("R", "G1", "G2", "B")})
    rec = rp.decode(blob)
    assert rec.shape == raw.mosaic.shape
    assert np.abs(rec - raw.mosaic).max() < 40
