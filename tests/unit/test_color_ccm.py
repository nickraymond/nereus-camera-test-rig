"""v0.3 depth-dependent colour matrix (SPEC §4 Phase 8 S2a, OQ-39): row-sum-1 fit, depth bands,
interpolation, and leave-one-dive-out (a dive never sees its own frames)."""

from __future__ import annotations

import numpy as np

from nereus_camera_test_rig.color.ccm import (
    band_matrices,
    fit_matrix,
    leave_one_dive_out,
    matrix_at,
)


def desaturate(depth: float) -> np.ndarray:
    """A row-sum-1 matrix that desaturates more with depth (what the water does)."""
    k = 0.03 * depth
    return (1 - k) * np.eye(3) + k / 3


def frames(dive: str, depths, rng):
    out = []
    for i, d in enumerate(depths):
        t = rng.uniform(0.02, 0.9, (12, 3))
        x = t @ desaturate(d).T  # the camera sees desaturated colours
        out.append({"stem": f"{dive}-{i}", "dive": dive, "depth_m": float(d), "x": x.tolist(),
                    "t": t.tolist()})
    return out


def test_fit_recovers_a_row_sum_one_matrix():
    rng = np.random.default_rng(0)
    M = np.array([[1.3, -0.2, -0.1], [-0.1, 1.2, -0.1], [0.05, -0.25, 1.2]])
    x = rng.uniform(0, 1, (40, 3))
    fitted = fit_matrix(x, x @ M.T)
    np.testing.assert_allclose(fitted, M, atol=1e-9)
    np.testing.assert_allclose(fit_matrix(x, x @ M.T + 0.01).sum(axis=1), 1.0)


def test_bands_follow_depth_and_interpolate():
    rng = np.random.default_rng(1)
    obs = frames("3", np.linspace(4, 16, 18), rng)
    bands = band_matrices(obs)
    assert len(bands) == 3 and [b["n_frames"] for b in bands] == [6, 6, 6]
    for b in bands:  # each band's matrix undoes the desaturation at its median depth
        np.testing.assert_allclose(b["M"], np.linalg.inv(desaturate(b["depth"])), atol=0.05)
    mid = matrix_at(bands, (bands[0]["depth"] + bands[1]["depth"]) / 2)
    np.testing.assert_allclose(mid, (np.array(bands[0]["M"]) + bands[1]["M"]) / 2)
    np.testing.assert_allclose(matrix_at(bands, 0.5), bands[0]["M"])  # clamped
    assert matrix_at([], 5.0) is None


def test_leave_one_dive_out_never_uses_the_dives_own_frames():
    rng = np.random.default_rng(2)
    obs = frames("3", np.linspace(4, 16, 15), rng) + frames("4", np.linspace(4, 16, 15), rng)
    lodo = leave_one_dive_out(obs, ["3", "4"])
    assert sum(b["n_frames"] for b in lodo["3"]) == 15 == sum(b["n_frames"] for b in lodo["4"])
    only_4 = band_matrices([o for o in obs if o["dive"] == "4"])
    assert lodo["3"] == only_4
