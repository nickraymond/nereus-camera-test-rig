"""Underwater-sim: make last night's in-air frames red-starved *at capture*, before any
compression (review consensus; the owner asked for last night's frames only).

Under water red is weak when the photons arrive, so it is quantized and coded at a few DN.
Multiplying red after decode (the spec's stress gain) does not reproduce that. Binomial
thinning does: keeping each photo-electron with probability p turns a Poisson signal of mean
v into one of mean p·v, so in DN

    v' = p·v + sqrt(a·p·(1 − p)·v)·z + sqrt(c·(1 − p²))·z'

with the fitted noise σ²(v) = a·v + c (``noise.py``): the shot noise stays Poisson at the new
level and the read noise stays at its original size. Then round to the sensor's integer grid
and clip like the sensor does: IMX708 never reports below its black (64) level; OpenMV
over-subtracts black by about 1 DN and clips at 0 (fact-check, 2026-09-30), emulated here as
``max(0, round(v'_true − 1))`` with v_true = v + 1 where the input is above 0.

Default factors from the TG-7 at 15.5 m (S2a: grey needs about R ×7.7, B ×1.2 vs G):
p_R = 0.14, p_B = 0.8, greens unchanged. Each repeat is thinned with its own seed, so noise
in the simulated frames is measured, not assumed.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from .common import Raw, merge, split
from .noise import NoiseModel

P_UNDERWATER = {"R": 0.14, "G1": 1.0, "G2": 1.0, "B": 0.8}


def thin(raw: Raw, model: NoiseModel, p: dict[str, float] = P_UNDERWATER, seed: int = 0,
         dead_zone: float = 0.0) -> Raw:
    rng = np.random.default_rng(seed)
    out = {}
    for ch, plane in split(raw.mosaic.astype(np.float64), raw.cfa).items():
        v = plane - raw.black
        if dead_zone:
            v = np.where(v > 0, v + dead_zone, v)
        pc = p[ch]
        if pc >= 1:
            out[ch] = plane
            continue
        shot = np.sqrt(np.maximum(model.a[ch] * pc * (1 - pc) * np.maximum(v, 0), 0))
        read = np.sqrt(model.c[ch] * (1 - pc ** 2))
        vt = pc * v + shot * rng.standard_normal(v.shape) + read * rng.standard_normal(v.shape)
        out[ch] = np.round(vt - dead_zone) + raw.black
    mos = np.clip(merge(out, raw.cfa), raw.black, raw.white).astype(np.uint16)
    return replace(raw, mosaic=mos, meta={**raw.meta, "underwater_sim": dict(p), "seed": seed})
