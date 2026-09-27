"""Solar elevation from UTC time + site — SPEC §4 Phase 8 S1 (light-change covariate).

The NOAA Solar Calculator algorithm (Julian century; after Meeus, *Astronomical Algorithms*),
good to ~0.01° over 1800–2100. The shorter fractional-year series was tried first and was off
by ~0.3–0.4° near the equinoxes. Site positions are only known to island level (±0.2°, ≲0.3°
of elevation), which dominates the error. No atmospheric refraction correction.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone


def _sin(deg: float) -> float:
    return math.sin(math.radians(deg))


def _cos(deg: float) -> float:
    return math.cos(math.radians(deg))


def solar_declination_and_eot(when: datetime) -> tuple[float, float]:
    """(declination in degrees, equation of time in minutes) at ``when`` (tz-aware)."""
    jd = when.astimezone(timezone.utc).timestamp() / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    c = (_sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + _sin(2 * m) * (0.019993 - 0.000101 * t) + _sin(3 * m) * 0.000289)
    omega = 125.04 - 1934.136 * t
    apparent_long = l0 + c - 0.00569 - 0.00478 * _sin(omega)
    eps0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    eps = eps0 + 0.00256 * _cos(omega)
    decl = math.degrees(math.asin(_sin(eps) * _sin(apparent_long)))
    y = math.tan(math.radians(eps / 2)) ** 2
    eot = 4 * math.degrees(y * _sin(2 * l0) - 2 * e * _sin(m) + 4 * e * y * _sin(m) * _cos(2 * l0)
                           - 0.5 * y * y * _sin(4 * l0) - 1.25 * e * e * _sin(2 * m))
    return decl, eot


def solar_elevation_deg(when: datetime, lat: float, lon: float) -> float:
    """Sun elevation above the horizon in degrees; ``lon`` east-positive, ``when`` tz-aware."""
    t = when.astimezone(timezone.utc)
    decl, eot = solar_declination_and_eot(t)
    minutes = t.hour * 60 + t.minute + t.second / 60
    hour_angle = (minutes + eot + 4 * lon) / 4 - 180
    cos_zenith = _sin(lat) * _sin(decl) + _cos(lat) * _cos(decl) * _cos(hour_angle)
    return 90.0 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zenith))))
