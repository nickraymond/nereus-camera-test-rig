"""Depth (+ water temperature) reader interface — pool spec §5.1, §9.

The depth sensor is NOT chosen yet (model, interface — I2C? — and the pressure port through the
housing are Nick's decisions, spec §9). This module fixes only the interface the capture side
uses, so a real driver slots in later without touching the coordinator or the loop:

    sensor = build_depth_sensor("fake")          # or, later, e.g. "ms5837:i2c-1" (not built)
    reading = sensor.read()                      # DepthReading, never raises
    reading.to_dict()                            # what lands in experiment.json

``run_experiment(depth_sensor=…)`` reads it at the start and end of each capture set and
writes both readings into ``experiment.json`` (``sensors.depth_start`` / ``depth_end``).

The only implementation today is ``FakeDepthSensor``: it returns a fixed, clearly labelled
placeholder (``source: "fake"``) — by default no depth at all (``depth_m: null``), or a value
given for a dry run. It never pretends to be a measurement.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol


@dataclass
class DepthReading:
    ok: bool
    source: str                         # "fake", later the driver name
    depth_m: Optional[float] = None     # metres below the surface
    water_temp_c: Optional[float] = None
    utc: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"))
    error: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)  # driver-specific extras (pressure, …)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DepthSensor(Protocol):
    def info(self) -> dict[str, Any]: ...
    def read(self) -> DepthReading: ...


class FakeDepthSensor:
    """Placeholder until a sensor is chosen. Returns ``depth_m`` / ``water_temp_c`` as given
    (default ``None``) with ``source: "fake"`` so no record can mistake it for a measurement."""

    def __init__(self, depth_m: Optional[float] = None, water_temp_c: Optional[float] = None):
        self.depth_m, self.water_temp_c = depth_m, water_temp_c

    def info(self) -> dict[str, Any]:
        return {"source": "fake", "model": None, "interface": None,
                "note": "placeholder: no depth sensor chosen yet (pool spec §9)"}

    def read(self) -> DepthReading:
        t0 = time.monotonic()
        return DepthReading(ok=True, source="fake", depth_m=self.depth_m,
                            water_temp_c=self.water_temp_c,
                            raw={"read_seconds": round(time.monotonic() - t0, 4)})


def build_depth_sensor(spec: Optional[str]) -> Optional[DepthSensor]:
    """``None`` / ``"none"`` → no sensor; ``"fake"`` or ``"fake:<metres>"`` → ``FakeDepthSensor``.
    Anything else raises ``ValueError`` (no real driver exists yet)."""
    if not spec or spec == "none":
        return None
    kind, _, arg = spec.partition(":")
    if kind == "fake":
        return FakeDepthSensor(float(arg) if arg else None)
    raise ValueError(f"unknown depth sensor {spec!r}: only 'fake' exists until Nick picks the "
                     "sensor (pool spec §9)")


def safe_read(sensor: Optional[DepthSensor]) -> Optional[dict[str, Any]]:
    """``sensor.read().to_dict()``, or a failed reading if a driver raises anyway."""
    if sensor is None:
        return None
    try:
        return sensor.read().to_dict()
    except Exception as exc:  # a real driver must never kill a capture set
        return DepthReading(ok=False, source=sensor.info().get("source", "?"),
                            error=f"{type(exc).__name__}: {exc}").to_dict()
