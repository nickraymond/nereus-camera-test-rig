"""Linear JPEG XL transport (``color/linear_jxl.py``): the curve inverts, lossless is exact,
lossy keeps region means, and a wrong or truncated file fails loudly. Needs libjxl's
``cjxl``/``djxl`` on PATH (skipped otherwise)."""

from __future__ import annotations

import json
import shutil

import numpy as np
import pytest

from nereus_camera_test_rig.color import linear_jxl as lj

needs_jxl = pytest.mark.skipif(not (shutil.which("cjxl") and shutil.which("djxl")),
                               reason="libjxl tools (cjxl/djxl) not installed")
LIGHT = np.array([0.08, 0.6, 0.5])  # underwater-like: weak red
WB = LIGHT[1] / LIGHT


def scene(seed=0, h=256, w=384):
    """Flat patches of known reflectance under LIGHT, a textured strip, a near-black strip,
    with shot + read noise — linear, black-subtracted (negatives kept, like the RAW)."""
    rng = np.random.default_rng(seed)
    rho = np.repeat(np.repeat(rng.uniform(0.03, 1.0, (4, 6)), 48, 0), 64, 1)[..., None]
    img = np.zeros((h, w, 3))
    img[:192] = rho * LIGHT  # patches
    y, x = np.mgrid[0:32, 0:w]
    img[192:224] = (0.3 + 0.25 * np.sin(x / 7.0) * np.cos(y / 5.0))[..., None] * LIGHT
    img[224:] = 0.0005  # near black
    noise = np.sqrt(img.clip(0) * 2e-4 + 0.002 ** 2)
    return (img + rng.normal(0, 1, img.shape) * noise).astype(np.float32)


def test_curve_inverts_to_the_same_codes_and_keeps_order():
    x = scene()
    g = lj.headroom_gains(x, WB)
    assert np.max(np.maximum(x + lj.PEDESTAL, 0) * g) == pytest.approx(1.0)
    np.testing.assert_allclose(g / g[1], WB / WB[1])  # only a scale on top of the WB
    c = lj.to_codes(x, g)
    assert np.array_equal(lj.to_codes(lj.from_codes(c, g), g), c)
    ramp = np.linspace(-0.004, 0.9, 2000)[:, None, None].repeat(3, 2)
    assert np.all(np.diff(lj.from_codes(lj.to_codes(ramp, g), g)[:, 0, 0]) >= 0)


def test_weak_red_gets_as_many_codes_as_green():
    x = scene()
    c = lj.to_codes(x, lj.headroom_gains(x, WB))
    assert len(np.unique(c[:192, :, 0])) > 0.8 * len(np.unique(c[:192, :, 1]))


@needs_jxl
def test_lossless_round_trip_is_exact_in_codes(tmp_path):
    x = scene()
    side = lj.encode(x, WB, tmp_path / "a.jxl", distance=0)
    back = lj.decode(tmp_path / "a.jxl")
    expect = lj.from_codes(lj.to_codes(x, side["gains"]), side["gains"])
    np.testing.assert_array_equal(back, expect)
    # the pedestal keeps negative noise: a near-black region's mean survives (no clip bias)
    assert back[224:].mean() == pytest.approx(x[224:].mean(), abs=5e-5)
    assert side["below_pedestal_fraction"] < 1e-3


@needs_jxl
def test_lossy_keeps_region_means_and_is_smaller(tmp_path):
    x = scene()
    lossless = lj.encode(x, WB, tmp_path / "l.jxl", distance=0)
    side = lj.encode(x, WB, tmp_path / "d.jxl", distance=0.5, meta={"exposure_s": 0.04})
    back = lj.decode(tmp_path / "d.jxl")
    err = lj.block_mean_error(x[:224], back[:224], min_signal=0.005)
    assert err["p99"] < 0.02, err
    assert side["bytes"] < lossless["bytes"] / 3
    assert json.loads((tmp_path / "d.json").read_text())["meta"] == {"exposure_s": 0.04}


@needs_jxl
def test_a_changed_file_or_foreign_sidecar_fails_loudly(tmp_path):
    lj.encode(scene(), WB, tmp_path / "a.jxl", distance=1.0)
    data = (tmp_path / "a.jxl").read_bytes()
    (tmp_path / "a.jxl").write_bytes(data[: len(data) // 2])  # truncated transfer
    with pytest.raises(lj.JxlError, match="SHA-256"):
        lj.decode(tmp_path / "a.jxl")
    side = json.loads((tmp_path / "a.json").read_text())
    with pytest.raises(lj.JxlError, match="not a"):
        lj.decode(tmp_path / "a.jxl", {**side, "version": 99})


def test_bad_inputs_and_missing_tools_are_clear(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="3 positive"):
        lj.headroom_gains(scene(), [1, 0, 1])
    with pytest.raises(ValueError, match="finite"):
        lj.encode(np.zeros((4, 4)), WB, tmp_path / "x.jxl")
    monkeypatch.setattr(lj.shutil, "which", lambda name: None)
    with pytest.raises(lj.JxlError, match="brew install jpeg-xl"):
        lj.encode(scene(), WB, tmp_path / "x.jxl")
