"""Rate control: find the codec knob that lands a byte target within ±5 % (spec §3).

- **Continuous knobs** (JPEG XL distance, x264 CRF): secant steps on log(bytes) with
  bracketing (Illinois), in log(knob) for distances; stops inside ±3 %. Usually 3–6 encodes.
- **Integer knobs** (JPEG / jpegli / HEIC quality): binary search for the crossing; returns
  the point inside ±5 % if one exists, else **both** bracketing points so the summary can
  interpolate metrics at the exact target in log(bpp) (review consensus) — never a forced fit.
- Unreachable targets return the extreme knob with ``reachable = False``.

``Curve`` caches (payload, seconds) per knob, so every target and variant of a frame reuses
earlier encodes of the same plane at the same knob.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

TOL_SOLVE = 0.03
TOL_HIT = 0.05


@dataclass
class Curve:
    enc: Callable  # knob -> (bytes, RunStats|None)
    cache: dict = field(default_factory=dict)

    def get(self, knob):
        key = round(float(knob), 4)
        if key not in self.cache:
            data, st = self.enc(knob)
            self.cache[key] = (data, st.seconds if st else None)
        return self.cache[key]

    def size(self, knob) -> int:
        return len(self.get(knob)[0])


@dataclass
class Solution:
    knobs: list  # one knob, or two bracketing integer knobs
    reachable: bool = True
    note: str = ""


def solve(size: Callable[[float], int], target: float, lo: float, hi: float, direction: int,
          integer: bool, log_knob: bool = True, start: Optional[float] = None) -> Solution:
    """Knob(s) whose total size hits ``target`` bytes. ``direction`` +1: knob up → bytes up."""
    big, small = (hi, lo) if direction > 0 else (lo, hi)  # knob giving most / fewest bytes
    if size(big) < target * (1 - TOL_HIT):
        return Solution([big], False, "target above the codec's largest output")
    if size(small) > target * (1 + TOL_HIT):
        return Solution([small], False, "target below the codec's smallest output")
    if integer:
        return _solve_int(size, target, int(lo), int(hi), direction)
    return _solve_float(size, target, lo, hi, direction, log_knob, start)


def _solve_int(size, target, lo, hi, direction) -> Solution:
    # invariant: size(a) <= target < size(b) in the "bytes" order
    a, b = (lo, hi) if direction > 0 else (hi, lo)
    while abs(b - a) > 1:
        m = (a + b) // 2
        if size(m) <= target:
            a = m
        else:
            b = m
    for k in (a, b):
        if abs(size(k) / target - 1) <= TOL_HIT:
            return Solution([k])
    return Solution([a, b], True, "bracketed (integer knob): metrics interpolated")


def _solve_float(size, target, lo, hi, direction, log_knob, start) -> Solution:
    tx = (lambda k: math.log(k)) if log_knob else (lambda k: k)
    fx = (lambda x: math.exp(x)) if log_knob else (lambda x: x)
    xa, xb = tx(lo), tx(hi)
    fa = math.log(size(lo)) - math.log(target)
    fb = math.log(size(hi)) - math.log(target)
    x = tx(start) if start else (xa + xb) / 2
    side = 0
    for _ in range(14):
        fv = math.log(size(fx(x))) - math.log(target)
        if abs(fv) <= math.log(1 + TOL_SOLVE):
            return Solution([round(fx(x), 4)])
        if (fv > 0) == (fa > 0):
            xa, fa = x, fv
            if side == -1:
                fb /= 2
            side = -1
        else:
            xb, fb = x, fv
            if side == 1:
                fa /= 2
            side = 1
        x = xb - fb * (xb - xa) / (fb - fa) if fb != fa else (xa + xb) / 2
    k = round(fx(x), 4)
    ok = abs(size(k) / target - 1) <= TOL_HIT
    return Solution([k], ok, "" if ok else "did not converge within ±5 %")
