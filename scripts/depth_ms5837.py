#!/usr/bin/env python3
"""depth_ms5837.py — one Blue Robotics Bar30 / Bar02 (TE MS5837) reading as JSON.

Purpose
    The pool spec (SPEC_pool_codec_test §5.1) wants depth + water temperature with every capture
    set. scripts/pool_capture.py runs this script in a subprocess (timeout) at the start and end
    of each set, so a sensor problem can only ever produce an ``error`` field — never a stopped or
    slowed capture.

Hardware (verified on nereus002, 2026-10-13 8:45 PM): I2C bus 1, address 0x76, sharing the bus
    with the LiFePO4wered/Pi+ (0x43). PROM CRC OK; it is a Bar30 (MS5837-30BA): the 30BA formula
    gives 985 mbar in air, the 02BA formula an impossible 49 mbar. Nothing else is written on the
    bus: only the MS5837's own commands (reset 0x1E, convert 0x4A / 0x5A, ADC read 0x00, PROM
    0xA0..0xAC).

Method (TE MS5837-30BA / -02BA datasheets; same steps as bluerobotics/ms5837-python): reset,
    read PROM C0..C6 + CRC4 check, D1 (pressure) and D2 (temperature) at OSR 8192 (max 17.2 ms per
    conversion), first- and second-order compensation for the chosen model.
    depth_m = (P - P_ref) * 100 / (rho * 9.80665), fresh water rho = 997 kg/m^3 (pool).

Usage
    /usr/bin/python3 scripts/depth_ms5837.py [--model 30BA|02BA] [--ref-mbar 985.0] [--samples 1]
    -> {"ok": true, "pressure_mbar": .., "temp_c": .., "depth_m": .. (only with --ref-mbar), ...}
    Exit 0 with {"ok": false, "error": ...} on any sensor/bus error (the caller decides).

Needs smbus2 (Debian's python3 on nereus002 has 0.4.3; the rig venv does not). No root.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

ADDR = 0x76
G = 9.80665
RHO_FRESH = 997.0


def crc4(prom: list[int]) -> int:
    """MS5837 PROM CRC (datasheet): 4-bit CRC over C0 (CRC nibble cleared) .. C6 + a zero word."""
    n = list(prom) + [0]
    n[0] &= 0x0FFF
    rem = 0
    for i in range(16):
        rem ^= (n[i >> 1] & 0x00FF) if i % 2 else (n[i >> 1] >> 8)
        for _ in range(8):
            rem = ((rem << 1) ^ 0x3000) if rem & 0x8000 else (rem << 1)
            rem &= 0xFFFF
    return (rem >> 12) & 0xF


def compensate(c: list[int], d1: int, d2: int, model: str) -> tuple[float, float]:
    """(pressure mbar, temperature C) with 1st + 2nd order compensation."""
    dt = d2 - c[5] * 256
    temp = 2000 + dt * c[6] / 8388608
    if model == "30BA":
        off = c[2] * 65536 + (c[4] * dt) / 128
        sens = c[1] * 32768 + (c[3] * dt) / 256
        if temp < 2000:
            ti = 3 * dt * dt / 8589934592
            offi = 3 * (temp - 2000) ** 2 / 2
            sensi = 5 * (temp - 2000) ** 2 / 8
            if temp < -1500:
                offi += 7 * (temp + 1500) ** 2
                sensi += 4 * (temp + 1500) ** 2
        else:
            ti, offi, sensi = 2 * dt * dt / 137438953472, (temp - 2000) ** 2 / 16, 0.0
        p = ((d1 * (sens - sensi)) / 2097152 - (off - offi)) / 8192 / 10.0
    elif model == "02BA":
        off = c[2] * 131072 + (c[4] * dt) / 64
        sens = c[1] * 65536 + (c[3] * dt) / 128
        if temp < 2000:
            ti = 11 * dt * dt / 34359738368
            offi = 31 * (temp - 2000) ** 2 / 8
            sensi = 63 * (temp - 2000) ** 2 / 32
        else:
            ti = offi = sensi = 0.0
        p = ((d1 * (sens - sensi)) / 2097152 - (off - offi)) / 32768 / 100.0
    else:
        raise ValueError(f"unknown model {model!r}")
    return p, (temp - ti) / 100.0


def read(model: str = "30BA", samples: int = 1, bus_no: int = 1) -> dict:
    from smbus2 import SMBus, i2c_msg

    def adc(bus, cmd):
        bus.write_byte(ADDR, cmd)
        time.sleep(0.02)
        bus.i2c_rdwr(i2c_msg.write(ADDR, [0x00]))
        m = i2c_msg.read(ADDR, 3)
        bus.i2c_rdwr(m)
        b = list(m)
        return b[0] << 16 | b[1] << 8 | b[2]

    try:  # the shared I2C lock (scripts/pool_led.py); fail-soft if it can't be imported
        from pool_led import i2c_lock
    except ImportError:
        import contextlib
        i2c_lock = contextlib.nullcontext
    t0 = time.monotonic()
    with i2c_lock(), SMBus(bus_no) as bus:
        bus.write_byte(ADDR, 0x1E)
        time.sleep(0.02)
        prom = []
        for i in range(7):
            w = bus.read_word_data(ADDR, 0xA0 + 2 * i)
            prom.append(((w & 0xFF) << 8) | (w >> 8))  # PROM words are big-endian
        if crc4(prom) != prom[0] >> 12:
            raise ValueError(f"PROM CRC mismatch: calc {crc4(prom)}, stored {prom[0] >> 12}")
        ps, ts = [], []
        for _ in range(max(1, samples)):
            p, t = compensate(prom, adc(bus, 0x4A), adc(bus, 0x5A), model)
            ps.append(p)
            ts.append(t)
    ps.sort()
    return {"ok": True, "model": model, "pressure_mbar": round(ps[len(ps) // 2], 2),
            "temp_c": round(sorted(ts)[len(ts) // 2], 2), "samples": len(ps),
            "spread_mbar": round(ps[-1] - ps[0], 2), "read_s": round(time.monotonic() - t0, 3)}


def depth_m(pressure_mbar: float, ref_mbar: float) -> float:
    return round((pressure_mbar - ref_mbar) * 100.0 / (RHO_FRESH * G), 3)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="30BA", choices=("30BA", "02BA"))
    ap.add_argument("--samples", type=int, default=1)
    ap.add_argument("--ref-mbar", type=float, default=None,
                    help="surface pressure for depth (the arm-time reading)")
    args = ap.parse_args(argv)
    try:
        out = read(args.model, args.samples)
        if args.ref_mbar:
            out["ref_mbar"] = args.ref_mbar
            out["depth_m"] = depth_m(out["pressure_mbar"], args.ref_mbar)
    except Exception as exc:  # fail-soft by contract: the caller logs it, capture goes on
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
