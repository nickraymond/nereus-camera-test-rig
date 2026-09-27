"""v0.3 depth-dependent colour matrix (SPEC §4 Phase 8 S2a, OQ-39).

Every RAW method so far applies the camera's daylight ``ColorMatrix`` after white balance, and
bottoms out at ΔE00 ≈ 19 under water: blue-green light desaturates the card in a way no daylight
matrix undoes. v0.3 fits the matrix on the card itself, as a function of depth:

- training observations: every usable card frame, colour patches only, in **white-balanced
  camera RGB** (card WB on the anchor grey, the ``raw_card_wb`` map) against the patches' design
  values in linear sRGB;
- one 3 × 3 per depth band (terciles of the training frames' depths), each row constrained to
  sum to 1 so a white-balanced neutral stays neutral, least squares in linear RGB;
- ``matrix_at``: interpolated linearly in depth between band centres, clamped outside;
- validation: **leave-one-dive-out** — a frame only ever gets a matrix fitted on the other
  dives, so a scored patch is never in its own fit.

Pure numpy; ``correct`` builds the observations and applies the matrices.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

N_BANDS = 3
MIN_FRAMES = 5  # per band


def fit_matrix(x: np.ndarray, t: np.ndarray) -> np.ndarray:
    """3 × 3 ``M`` minimising ‖M x − t‖² with every row summing to 1."""
    x, t = np.asarray(x, np.float64), np.asarray(t, np.float64)
    A = x[:, :2] - x[:, 2:3]
    M = np.empty((3, 3))
    for r in range(3):
        m01, *_ = np.linalg.lstsq(A, t[:, r] - x[:, 2], rcond=None)
        M[r] = [m01[0], m01[1], 1 - m01.sum()]
    return M


def band_matrices(obs: list[dict], n_bands: int = N_BANDS) -> list[dict[str, Any]]:
    """Per depth band: {depth (median), range, M, n_frames, n_patches, rms}.

    ``obs``: [{"stem", "depth_m", "x": [[r, g, b], …], "t": [[r, g, b], …]}] per frame.
    """
    obs = sorted(obs, key=lambda o: o["depth_m"])
    n_bands = max(1, min(n_bands, len(obs) // MIN_FRAMES))
    bands = []
    for group in np.array_split(np.arange(len(obs)), n_bands):
        frames = [obs[i] for i in group]
        x = np.concatenate([np.asarray(o["x"]) for o in frames])
        t = np.concatenate([np.asarray(o["t"]) for o in frames])
        M = fit_matrix(x, t)
        depths = [o["depth_m"] for o in frames]
        bands.append({"depth": float(np.median(depths)),
                      "range": [float(min(depths)), float(max(depths))],
                      "M": M.round(6).tolist(), "n_frames": len(frames), "n_patches": len(x),
                      "rms": round(float(np.sqrt(np.mean((x @ M.T - t) ** 2))), 5)})
    return bands


def matrix_at(bands: list[dict], depth: float) -> Optional[np.ndarray]:
    """The band matrices interpolated at ``depth`` (clamped to the outermost bands)."""
    if not bands:
        return None
    centres = np.array([b["depth"] for b in bands])
    Ms = np.array([b["M"] for b in bands])
    if len(bands) == 1 or depth <= centres[0]:
        return Ms[0]
    if depth >= centres[-1]:
        return Ms[-1]
    i = int(np.searchsorted(centres, depth)) - 1
    w = (depth - centres[i]) / (centres[i + 1] - centres[i])
    return (1 - w) * Ms[i] + w * Ms[i + 1]


def leave_one_dive_out(obs: list[dict], dives) -> dict[str, list[dict]]:
    """{dive: band matrices fitted on every other dive's frames}."""
    return {d: band_matrices([o for o in obs if o["dive"] != d]) for d in dives}
