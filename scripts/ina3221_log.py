#!/usr/bin/env python3
"""ina3221_log.py — read-only INA3221 logger for the N6 eval power rows (SPEC_n6_eval must dos 2, 3).

Purpose: log bus volts and current per channel from the Adafruit INA3221 on nereus001 (I2C bus 1,
  0x40; CH1 = the N6's 5 V, Fri 10/23) on the Pi's monotonic clock, so a row lines up with
  sync_video's ``host_rx_mono`` frame times.

Reused: register map and scaling from Nick's ADIN_SPI_OpenMV ``pi/workbench/power_log.py``
  (verified there against Adafruit_CircuitPython_INA3221, 2026-08-23): shunt CHn = 0x01/0x03/0x05
  at 40 uV/LSB after >> 3, bus CHn = 0x02/0x04/0x06 at 8 mV/LSB after >> 3, ID 0xFE = 0x5449,
  0.05 ohm shunts. Changed: **no config write** (power_log.py writes 0x7527). This logger reads
  the chip as it is, and records its config register in the header line, so the card's "read-only
  registers" rule holds. The power-on default 0x7127 (3 channels, continuous, no averaging) was
  read on nereus001 on 10/23.

Usage (Pi, repo root; needs the [power] extra = smbus2):
  python scripts/ina3221_log.py --probe                         # IDs + config + one read per channel
  python scripts/ina3221_log.py --out power.csv --seconds 10 [--hz 10] [--ch 1]
Output CSV: mono_s, wall_utc, ch, bus_v, shunt_mv, ma, mw. The header comment line holds the IDs and config.
Exit 0 = ran; 1 = not an INA3221 or a read failed (stated on stderr).
mW is "not measured" for the eval until the known-load check (must do 3) passes; this script only logs.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timezone

ADDR = 0x40
REG_CONFIG, REG_MANUF_ID, REG_DIE_ID = 0x00, 0xFE, 0xFF
REG_SHUNT = {1: 0x01, 2: 0x03, 3: 0x05}
REG_BUS = {1: 0x02, 2: 0x04, 3: 0x06}
SHUNT_OHMS = 0.05
SHUNT_V_PER_LSB = 40e-6
BUS_V_PER_LSB = 8e-3
TI = 0x5449


def read16(bus, addr: int, reg: int) -> int:
    raw = bus.read_word_data(addr, reg)          # SMBus words are little-endian
    return ((raw & 0xFF) << 8) | (raw >> 8)      # the chip is big-endian


def to_signed(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def read_channel(bus, addr: int, ch: int) -> tuple[float, float, float]:
    """(bus volts, shunt mV, mA) for one channel."""
    sh = to_signed(read16(bus, addr, REG_SHUNT[ch])) >> 3
    bu = to_signed(read16(bus, addr, REG_BUS[ch])) >> 3
    shunt_v = sh * SHUNT_V_PER_LSB
    return bu * BUS_V_PER_LSB, shunt_v * 1000.0, shunt_v / SHUNT_OHMS * 1000.0


def identify(bus, addr: int) -> dict:
    return {"manuf": read16(bus, addr, REG_MANUF_ID), "die": read16(bus, addr, REG_DIE_ID),
            "config": read16(bus, addr, REG_CONFIG)}


def log(bus, addr: int, channels: list[int], hz: float, seconds: float, out) -> int:
    """Write rows for ``seconds`` at ``hz``; returns rows written."""
    w = csv.writer(out)
    w.writerow(["mono_s", "wall_utc", "ch", "bus_v", "shunt_mv", "ma", "mw"])
    period, t0, rows = 1.0 / hz, time.monotonic(), 0
    while time.monotonic() - t0 < seconds:
        tick = time.monotonic()
        wall = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        for ch in channels:
            v, mv, ma = read_channel(bus, addr, ch)
            w.writerow([f"{tick:.4f}", wall, ch, f"{v:.3f}", f"{mv:.3f}", f"{ma:.2f}",
                        f"{v * ma:.1f}"])
            rows += 1
        out.flush()
        time.sleep(max(0.0, period - (time.monotonic() - tick)))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bus", type=int, default=1)
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=ADDR)
    ap.add_argument("--ch", type=int, action="append", choices=(1, 2, 3))
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--out", default=None, help="CSV path (default stdout)")
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args(argv)
    from smbus2 import SMBus  # imported here so the decode helpers test without smbus2

    channels = a.ch or [1, 2, 3]
    with SMBus(a.bus) as bus:
        ids = identify(bus, a.addr)
        if ids["manuf"] != TI:
            print(f"ina3221_log: 0x{a.addr:02x} manuf=0x{ids['manuf']:04x} is not an INA3221",
                  file=sys.stderr)
            return 1
        head = f"# INA3221 bus {a.bus} 0x{a.addr:02x} manuf=0x{ids['manuf']:04x} " \
               f"die=0x{ids['die']:04x} config=0x{ids['config']:04x} (read-only, no writes)"
        if a.probe:
            print(head)
            for ch in (1, 2, 3):
                v, mv, ma = read_channel(bus, a.addr, ch)
                print(f"CH{ch}: {v:.3f} V  {mv:.2f} mV  {ma:.1f} mA")
            return 0
        out = open(a.out, "w", newline="") if a.out else sys.stdout
        try:
            out.write(head + "\n")
            n = log(bus, a.addr, channels, a.hz, a.seconds, out)
        finally:
            if a.out:
                out.close()
    print(f"ina3221_log: {n} rows, {a.seconds:g} s at {a.hz:g} Hz", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
