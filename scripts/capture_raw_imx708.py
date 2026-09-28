#!/usr/bin/env python3
"""capture_raw_imx708.py — meter once, then lock exposure and capture RAW (DNG) on the IMX708.

Run ON the rig Pi from the repo root (stdlib only, no venv needed):

    python3 scripts/capture_raw_imx708.py [--stops -1 0 1] [--gain 1.0] [--mode 4608:2592]

1. **Meter:** one auto shot (`--metadata`); read back ExposureTime, AnalogueGain, ColourGains,
   LensPosition — what auto-exposure / AWB / AF chose.
2. **Lock:** analogue gain fixed at ``--gain`` (default 1.0 = as low as the sensor goes); a
   quick probe shot reads back the gain the camera really applies — the sensor clamps to its
   range (IMX708 on ``nereus002``: 1.0 → 1.1228, 2026-09-28) — and that gain is used. Shutter
   = metered exposure × metered gain / applied gain (the same total exposure), then × 2^stop
   for each ``--stops``; WB fixed to the metered ColourGains, focus to the metered LensPosition.
3. **Capture** each stop with ``--raw`` (JPEG + DNG from one exposure) and ``--metadata``.
4. **Verify** every shot: JPEG + DNG exist, the DNG size fits the sensor mode (16-bit), and the
   read-back ExposureTime / AnalogueGain / ColourGains / LensPosition match what was asked.

Writes ``results/raw_imx708/<UTC>/{meter.*, stop_<s>.{jpg,dng,json}, capture_raw.json}``;
never overwrites. Exit 0 = every shot passed; 1 = a check failed (see capture_raw.json);
2 = the camera command failed. Flags as in ``cameras/imx708.py`` (``--awb custom
--awbgains``, ``--shutter``, ``--gain``, ``--autofocus-mode manual --lens-position``) plus
``--raw`` / ``--mode``; their acceptance on the rig is what this script checks (OQ-7, OQ-24).
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOL = {"ExposureTime": (0.02, 50.0), "AnalogueGain": (0.03, 0.0),  # (relative, absolute)
       "ColourGains": (0.01, 0.0), "LensPosition": (0.0, 0.05)}
DNG_HEADER_MAX = 2_000_000  # tags + embedded thumbnail, bytes


def run(cmd: list[str], timeout: float) -> None:
    print("--", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        print(r.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"FAIL: {Path(cmd[0]).name} exit {r.returncode}")


def metadata(path: Path) -> dict:
    data = json.loads(path.read_text())
    if isinstance(data, list):  # some rpicam versions write a list of frames
        data = next(d for d in reversed(data) if isinstance(d, dict))
    return data


def close(want, got, key: str) -> bool:
    rel, ab = TOL[key]
    want = want if isinstance(want, list) else [want]
    got = got if isinstance(got, list) else [got]
    return len(want) == len(got) and all(
        abs(g - w) <= max(rel * abs(w), ab) for w, g in zip(want, got))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stops", type=float, nargs="+", default=[-1.0, 0.0, 1.0])
    ap.add_argument("--gain", type=float, default=1.0, help="locked analogue gain")
    ap.add_argument("--mode", default="4608:2592", help="sensor mode W:H (full res default)")
    ap.add_argument("--meter-ms", type=int, default=2000, help="metering shot settle time")
    ap.add_argument("--shot-ms", type=int, default=1000, help="locked shot settle time")
    ap.add_argument("--rpicam", default="rpicam-still")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    w, h = (int(v) for v in args.mode.split(":")[:2])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or ROOT / "results" / "raw_imx708" / stamp
    out.mkdir(parents=True, exist_ok=False)  # never overwrite a prior run
    size = ["--width", str(w), "--height", str(h), "--mode", args.mode]
    version = subprocess.run([args.rpicam, "--version"], capture_output=True,
                             text=True).stdout.strip().splitlines()[:1]
    timeout = 30 + args.meter_ms / 1000

    run([args.rpicam, "-n", "-t", str(args.meter_ms), *size, "-o", str(out / "meter.jpg"),
         "--metadata", str(out / "meter.json"), "--metadata-format", "json"], timeout)
    m = metadata(out / "meter.json")
    total_us = float(m["ExposureTime"]) * float(m["AnalogueGain"])  # exposure × gain
    red, blue = (float(v) for v in m["ColourGains"])
    lens = m.get("LensPosition")
    locks = ["--awb", "custom", "--awbgains", f"{red:.4f},{blue:.4f}"]
    if lens is not None:
        locks += ["--autofocus-mode", "manual", "--lens-position", f"{float(lens):.4f}"]

    # probe (JPEG only): which gain does the sensor actually apply for the one we ask?
    run([args.rpicam, "-n", "-t", str(args.shot_ms), *size, "-o", str(out / "probe.jpg"),
         "--metadata", str(out / "probe.json"), "--metadata-format", "json",
         "--shutter", str(round(total_us / args.gain)), "--gain", f"{args.gain:.4f}", *locks],
        timeout)
    gain = float(metadata(out / "probe.json")["AnalogueGain"])
    if not close(args.gain, gain, "AnalogueGain"):
        print(f"-- gain {args.gain:g} not available: the sensor applies {gain:g}; using it")
    exposure_us = total_us / gain

    shots, ok_all = [], True
    for stop in args.stops:
        name = f"stop_{stop:+g}"
        want = {"ExposureTime": round(exposure_us * 2 ** stop), "AnalogueGain": gain,
                "ColourGains": [red, blue]}
        if lens is not None:
            want["LensPosition"] = float(lens)
        run([args.rpicam, "-n", "-t", str(args.shot_ms), *size, "--raw",
             "-o", str(out / f"{name}.jpg"), "--metadata", str(out / f"{name}.json"),
             "--metadata-format", "json", "--shutter", str(want["ExposureTime"]),
             "--gain", f"{gain:.4f}", *locks], timeout)
        got = metadata(out / f"{name}.json")
        dng = out / f"{name}.dng"
        checks = {"jpeg_exists": (out / f"{name}.jpg").is_file(), "dng_exists": dng.is_file()}
        dng_bytes = dng.stat().st_size if dng.is_file() else 0
        checks["dng_size_fits_mode"] = w * h * 2 <= dng_bytes <= w * h * 2 + DNG_HEADER_MAX
        for key, value in want.items():
            checks[f"readback_{key}"] = key in got and close(value, got[key], key)
        passed = all(checks.values())
        ok_all &= passed
        shots.append({"name": name, "stop": stop, "requested": want, "dng_bytes": dng_bytes,
                      "readback": {k: got.get(k) for k in (*want, "DigitalGain", "Lux",
                                                            "SensorTemperature")},
                      "checks": checks, "passed": passed})
        failed = [k for k, v in checks.items() if not v]
        print(f"PASS {name}" if passed else f"FAIL {name}: {', '.join(failed)}")

    summary = {"utc": stamp, "host": socket.gethostname(), "rpicam": version,
               "mode": args.mode, "gain_requested": args.gain, "gain_applied": gain,
               "meter": {k: m.get(k) for k in ("ExposureTime", "AnalogueGain", "DigitalGain",
                                               "ColourGains", "LensPosition", "Lux")},
               "locked_exposure_us_at_stop0": round(exposure_us), "shots": shots,
               "passed": ok_all}
    (out / "capture_raw.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(f"{'PASS' if ok_all else 'FAIL'}: {len(shots)} locked RAW shots in {out}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            sys.exit(2)
        raise
