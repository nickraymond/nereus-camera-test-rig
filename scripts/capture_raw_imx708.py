#!/usr/bin/env python3
"""capture_raw_imx708.py — meter once, then lock exposure and capture RAW (DNG) on the IMX708.

Run ON the rig Pi from the repo root (stdlib only, no venv needed):

    python3 scripts/capture_raw_imx708.py [--stops -1 0 1] [--gain 1.0] [--mode 4608:2592]

Card-metered (S3/S4; needs the rig venv for the card finder):

    .venv/bin/python scripts/capture_raw_imx708.py --card configs/cards/nereus_v1.yaml \
        [--target 0.8]

1. **Meter:** one auto shot (`--metadata`); read back ExposureTime, AnalogueGain, ColourGains,
   LensPosition — what auto-exposure / AWB / AF chose.
2. **Lock:** analogue gain fixed at ``--gain`` (default 1.0 = as low as the sensor goes); a
   quick probe shot reads back the gain the camera really applies — the sensor clamps to its
   range (IMX708 on ``nereus002``: 1.0 → 1.1228, 2026-09-28) — and that gain is used. Shutter
   = metered exposure × metered gain / applied gain (the same total exposure), then × 2^stop
   for each ``--stops``; WB fixed to the metered ColourGains, focus to the metered LensPosition.
   With ``--card``, the probe is also a RAW: the card is found on it and stop 0 is scaled so the
   card's brightest channel lands on ``--target`` (scene metering left the V1 card's white at
   0.14 of full scale in the 2026-09-28 run-through); stop 0 is then checked on the card.
3. **Capture** each stop with ``--raw`` (JPEG + DNG from one exposure) and ``--metadata``;
   ``--repeat N`` takes N frames per stop.
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


MAX_CARD_PROBES = 3
MAX_GAIN = 16.0  # IMX708 analogue gain ceiling (libcamera AnalogueGain range 1.0-16.0)
TOL_TARGET = 0.15  # card-on-target check at stop 0, relative


def card_plan(dng: Path, card_yaml: Path, exposure_us: float, target: float) -> dict:
    """Card metering on one DNG (``color.raw_meter``, same rule as the OpenMV recipe).
    Imported here so the script stays stdlib-only without ``--card``."""
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    from nereus_camera_test_rig.color.card import load_card
    from nereus_camera_test_rig.color.raw_io import read_dng
    from nereus_camera_test_rig.color.raw_meter import (
        card_levels,
        card_reference,
        exposure_for_target,
    )

    ref = card_reference(card_levels(read_dng(dng), load_card(card_yaml)))
    plan = exposure_for_target(exposure_us, ref["level"], target, ref["clipped"],
                               hi_us=10_000_000)
    return {"reference": ref, **plan}


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
    ap.add_argument("--repeat", type=int, default=1,
                    help="frames per stop (S4 noise / repeatability); >1 names them stop_<s>_r<n>")
    ap.add_argument("--gain", type=float, default=1.0, help="locked analogue gain")
    ap.add_argument("--max-shutter-us", type=int, default=0,
                    help="gain priority (Nick's ISO-100 rule): keep the lowest gain and lengthen "
                         "the shutter up to this cap, then raise gain (0 = no cap)")
    ap.add_argument("--mode", default="4608:2592", help="sensor mode W:H (full res default)")
    ap.add_argument("--meter-ms", type=int, default=2000, help="metering shot settle time")
    ap.add_argument("--shot-ms", type=int, default=1000, help="locked shot settle time")
    ap.add_argument("--rpicam", default="rpicam-still")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--card", type=Path, help="card YAML: meter on the card, not the scene "
                    "(needs the rig venv: .venv/bin/python)")
    ap.add_argument("--target", type=float, default=0.80,
                    help="with --card: brightest card channel at stop 0, fraction of full scale")
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

    # probe: which gain does the sensor actually apply for the one we ask? With --card it is
    # also a RAW, and the card on it sets the exposure.
    card_metering = []
    probe_us = round(total_us / args.gain)
    for n in range(MAX_CARD_PROBES if args.card else 1):
        name = "probe" if n == 0 else f"probe_{n}"
        run([args.rpicam, "-n", "-t", str(args.shot_ms), *size, "-o", str(out / f"{name}.jpg"),
             "--metadata", str(out / f"{name}.json"), "--metadata-format", "json",
             *(["--raw"] if args.card else []),
             "--shutter", str(probe_us), "--gain", f"{args.gain:.4f}", *locks], timeout)
        p = metadata(out / f"{name}.json")
        gain = float(p["AnalogueGain"])
        exposure_us = total_us / gain
        if not args.card:
            break
        try:
            plan = card_plan(out / f"{name}.dng", args.card, float(p["ExposureTime"]),
                             args.target)
        except ValueError as exc:  # card not in view / not located: a failed check
            (out / "capture_raw.json").write_text(json.dumps(
                {"utc": stamp, "card": str(args.card), "card_metering": card_metering,
                 "error": f"card metering on {name}.dng: {exc}", "passed": False},
                indent=1) + "\n")
            print(f"FAIL: card metering on {name}.dng: {exc}  ({out})")
            return 1
        card_metering.append({"probe": name, **plan})
        print(f"-- card {plan['reference']['patch']}.{plan['reference']['channel']} = "
              f"{plan['reference']['level']:.3f} at {p['ExposureTime']} us -> "
              f"{plan['exposure_us']} us for {args.target}")
        exposure_us = plan["exposure_us"]
        if not plan["remeter"]:
            break
        probe_us = plan["exposure_us"]
    else:
        raise SystemExit(f"FAIL: card still clipped after {MAX_CARD_PROBES} probes")
    if not close(args.gain, gain, "AnalogueGain"):
        print(f"-- gain {args.gain:g} not available: the sensor applies {gain:g}; using it")
    gain_priority = None
    if args.max_shutter_us and exposure_us > args.max_shutter_us:
        # Lowest gain first, shutter up to the motion cap, then gain (Nick, 2026-10-05).
        need = gain * exposure_us / args.max_shutter_us
        gain_priority = {"wanted_us": round(exposure_us), "cap_us": args.max_shutter_us,
                         "gain_floor": gain, "gain_needed": round(need, 4),
                         "gain_clamped": need > MAX_GAIN}
        print(f"-- shutter {exposure_us:.0f} us > cap {args.max_shutter_us} us: shutter at the "
              f"cap, gain {gain:.4f} -> {min(need, MAX_GAIN):.4f}")
        exposure_us, gain = float(args.max_shutter_us), min(need, MAX_GAIN)

    shots, ok_all = [], True
    series = [(stop, n) for stop in args.stops for n in range(args.repeat)]
    for stop, rep in series:
        name = f"stop_{stop:+g}" + (f"_r{rep}" if args.repeat > 1 else "")
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
        shots.append({"name": name, "stop": stop, "repeat": rep, "requested": want,
                      "dng_bytes": dng_bytes,
                      "readback": {k: got.get(k) for k in (*want, "DigitalGain", "Lux",
                                                            "SensorTemperature")},
                      "checks": checks, "passed": passed})
        if args.card and stop == 0:
            ref = card_plan(dng, args.card, float(got["ExposureTime"]), args.target)["reference"]
            checks["card_on_target"] = abs(ref["level"] / args.target - 1) <= TOL_TARGET
            checks["card_unclipped"] = not ref["clipped"]
            passed = all(checks.values())
            ok_all &= passed
            shots[-1].update(card=ref, checks=checks, passed=passed)
        failed = [k for k, v in checks.items() if not v]
        print(f"PASS {name}" if passed else f"FAIL {name}: {', '.join(failed)}")

    summary = {"utc": stamp, "host": socket.gethostname(), "rpicam": version,
               "mode": args.mode, "gain_requested": args.gain, "gain_applied": gain,
               "meter": {k: m.get(k) for k in ("ExposureTime", "AnalogueGain", "DigitalGain",
                                               "ColourGains", "LensPosition", "Lux")},
               "locked_exposure_us_at_stop0": round(exposure_us), "locked_gain": gain,
               "max_shutter_us": args.max_shutter_us or None, "gain_priority": gain_priority,
               "shots": shots,
               "card": str(args.card) if args.card else None, "target": args.target,
               "card_metering": card_metering,
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
