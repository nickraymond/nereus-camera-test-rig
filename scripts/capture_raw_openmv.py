#!/usr/bin/env python3
"""capture_raw_openmv.py — card-metered, locked-exposure RAW on an OpenMV board (S3).

Run ON the rig Pi from the repo root, with the rig venv (needs the serial + color deps):

    .venv/bin/python scripts/capture_raw_openmv.py --board n6|ae3 [--serial S] \
        [--card configs/cards/nereus_v1.yaml] [--target 0.8] [--stops -1 0 1 --repeat 3]

1. **Meter:** ``capture_raw`` with the autos (the board locks what they chose); find the card
   on that RAW and read its brightest white-patch channel (``color.raw_meter``).
2. **Lock:** gain at the sensor's floor (3.15 dB, OQ-21); exposure = metered exposure x metered
   gain / floor gain x target / level — the card's brightest channel lands on ``--target`` of
   full scale. A clipped reference halves the exposure and meters again (up to 3 meter shots).
3. **Capture** the locked RAW. The board lengthens the frame for exposures past its default
   frame time (OQ-51, up to ~2 s); if the read-back is still short, the shortfall goes into
   gain and the locked shot is taken again.
4. **Verify:** read-back exposure within 5 % and gain within one sensor step (0.75 dB) of the
   request, the card found again, its brightest channel within 15 % of the target and unclipped.
5. **Series (S4, optional):** ``--stops`` / ``--repeat`` take ``stop_<s>_r<n>.bayer`` at the
   locked gain and the locked exposure x 2^stop, ``--repeat`` frames each; every one is checked
   for its exposure / gain read-back (a white clipped at +1 stop is expected, not a failure).

``reset_board`` runs before every shot (required on the AE3: one camera session per boot on
OpenMV v5; also gives both boards fresh 3A state). Stop Nick's workbench recipe first
(``curl -X POST localhost:8088/api/stop``). Writes ``results/raw_openmv/<UTC>_<board>/
{meter_<n>,locked}.{bayer,json} + capture_raw.json``; never overwrites. Exit 0 = every check
passed; 1 = a check failed (see capture_raw.json); 2 = a camera command failed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from nereus_camera_test_rig.cameras.openmv_usb import OpenMvUsbCamera  # noqa: E402
from nereus_camera_test_rig.color.card import load_card  # noqa: E402
from nereus_camera_test_rig.color.raw_io import read_openmv_bayer  # noqa: E402
from nereus_camera_test_rig.color.raw_meter import (  # noqa: E402
    DEFAULT_TARGET,
    card_levels,
    card_reference,
    exposure_for_target,
)
from nereus_camera_test_rig.models import CaptureRequest  # noqa: E402

SERIALS = {"n6": "020023000450433547373200", "ae3": "0829c14000000000"}  # nereus002
MIN_GAIN_DB = 3.152157  # PAG7936 floor on both boards: 0 dB reads back as this (OQ-21)
MAX_METER_SHOTS = 3
# Gain moves in sensor steps (read back on the N6, 2026-09-28: asked 3.676 -> 3.522 dB, 4.18 ->
# 4.22 dB), so its check only catches gross errors; the card-on-target check judges the result.
TOL_EXPOSURE, TOL_GAIN_DB, TOL_TARGET = 0.05, 0.75, 0.15


def shot(cam: OpenMvUsbCamera, dest: Path, settings: dict) -> dict:
    cam.reset_board()
    result = cam.capture_raw(str(dest), CaptureRequest(kind="image", settings=settings))
    if not result.ok:
        raise RuntimeError(f"capture_raw {dest.name} failed: {result.error}")
    return json.loads(dest.with_suffix(".json").read_text())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--board", choices=sorted(SERIALS), required=True)
    ap.add_argument("--serial", help="USB serial (default: the nereus002 board)")
    ap.add_argument("--card", type=Path, default=ROOT / "configs/cards/nereus_v1.yaml")
    ap.add_argument("--target", type=float, default=DEFAULT_TARGET,
                    help="brightest card channel as a fraction of full scale")
    ap.add_argument("--warmup-ms", type=int, default=2000, help="metering time on the board")
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "raw_openmv")
    ap.add_argument("--stops", type=float, nargs="+", default=[],
                    help="after the locked shot: a series at locked exposure x 2^stop")
    ap.add_argument("--repeat", type=int, default=1, help="frames per --stops entry")
    ap.add_argument("--exposure-us", type=int, default=None,
                    help="skip card metering and lock this exposure at the floor gain (e.g. "
                         "flat-field frames, where the card is covered)")
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out / f"{stamp}_{args.board}"
    out.mkdir(parents=True, exist_ok=False)
    card = load_card(args.card)
    summary: dict = {"board": args.board, "card": str(args.card), "target": args.target,
                     "meter": [], "checks": {}}
    cam = OpenMvUsbCamera(serial_number=args.serial or SERIALS[args.board], board=args.board)
    try:
        settings: dict = {"warmup_ms": args.warmup_ms}
        meter_shots = 0 if args.exposure_us else MAX_METER_SHOTS
        if args.exposure_us:          # fixed exposure: no card on the frame (flat-field)
            settings = {"warmup_ms": 300, "exposure_us": int(args.exposure_us),
                        "gain_db": MIN_GAIN_DB}
            summary["fixed_exposure_us"] = int(args.exposure_us)
        for n in range(meter_shots):
            side = shot(cam, out / f"meter_{n}.bayer", settings)
            ref = card_reference(card_levels(read_openmv_bayer(out / f"meter_{n}.bayer"), card))
            # Same total exposure at the floor gain, then scaled onto the target.
            at_floor = side["exposure_us"] * 10 ** ((side["gain_db"] - MIN_GAIN_DB) / 20)
            plan = exposure_for_target(at_floor, ref["level"], args.target, ref["clipped"])
            summary["meter"].append({"file": f"meter_{n}.bayer", "exposure_us": side["exposure_us"],
                                     "gain_db": side["gain_db"], "reference": ref, "plan": plan})
            print(f"meter {n}: {side['exposure_us']} us {side['gain_db']:.2f} dB, card "
                  f"{ref['patch']}.{ref['channel']} = {ref['level']:.3f} "
                  f"-> {plan['exposure_us']} us")
            settings = {"warmup_ms": 300, "exposure_us": plan["exposure_us"],
                        "gain_db": MIN_GAIN_DB}
            if not plan["remeter"]:
                break
        else:
            if meter_shots:
                raise RuntimeError(f"card still clipped after {MAX_METER_SHOTS} meter shots")

        side = shot(cam, out / "locked.bayer", settings)
        if side["exposure_us"] < (1 - TOL_EXPOSURE) * settings["exposure_us"]:
            # Still clamped (past the board's ~2 s frame-time limit, or a service without the
            # OQ-51 frame-time port): make the rest up with gain and shoot again.
            deficit_db = 20 * math.log10(settings["exposure_us"] / side["exposure_us"])
            summary["exposure_ceiling_us"] = side["exposure_us"]
            print(f"exposure clamped at {side['exposure_us']} us (asked "
                  f"{settings['exposure_us']}): +{deficit_db:.2f} dB gain instead")
            settings = {**settings, "exposure_us": side["exposure_us"],
                        "gain_db": round(MIN_GAIN_DB + deficit_db, 3)}
            side = shot(cam, out / "locked.bayer", settings)
        if args.exposure_us:          # no card to check: report the frame's clip fraction
            fr = read_openmv_bayer(out / "locked.bayer")
            clip = float((fr.active()[0] >= fr.white_level).mean())
            import numpy as np
            ref = {"patch": "frame", "channel": "all", "level": float(np.percentile(
                fr.active()[0], 99.5) / fr.white_level), "clipped": clip > 0.001}
        else:
            ref = card_reference(card_levels(read_openmv_bayer(out / "locked.bayer"), card))
        series = []
        for stop in args.stops:
            for n in range(args.repeat):
                name = f"stop_{stop:+g}_r{n}.bayer"
                want = {**settings, "exposure_us": round(settings["exposure_us"] * 2 ** stop)}
                got = shot(cam, out / name, want)
                ok = (abs(got["exposure_us"] - want["exposure_us"])
                      <= TOL_EXPOSURE * want["exposure_us"]
                      and abs(got["gain_db"] - want["gain_db"]) <= TOL_GAIN_DB)
                series.append({"file": name, "stop": stop, "requested_us": want["exposure_us"],
                               "exposure_us": got["exposure_us"], "gain_db": got["gain_db"],
                               "readback_ok": ok})
                print(f"{'ok  ' if ok else 'FAIL'} {name}: {got['exposure_us']} us "
                      f"(asked {want['exposure_us']}) {got['gain_db']:.2f} dB")
    except (RuntimeError, ValueError) as exc:
        summary["error"] = str(exc)
        (out / "capture_raw.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"FAIL: {exc}  ({out})")
        return 2
    finally:
        cam.close()

    want_us = settings["exposure_us"]
    checks = {
        "exposure_readback": abs(side["exposure_us"] - want_us) <= TOL_EXPOSURE * want_us,
        "gain_readback": abs(side["gain_db"] - settings["gain_db"]) <= TOL_GAIN_DB,
        "card_on_target": (True if args.exposure_us
                           else abs(ref["level"] / args.target - 1) <= TOL_TARGET),
        "card_unclipped": not ref["clipped"],
    }
    if series:
        checks["series_readback"] = all(r["readback_ok"] for r in series)
    summary.update({"locked": {"file": "locked.bayer", "requested_us": want_us,
                               "exposure_us": side["exposure_us"], "gain_db": side["gain_db"],
                               "reference": ref}, "series": series, "checks": checks,
                    "passed": all(checks.values())})
    (out / "capture_raw.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"locked: {side['exposure_us']} us (asked {want_us}) {side['gain_db']:.2f} dB, card "
          f"{ref['patch']}.{ref['channel']} = {ref['level']:.3f} (target {args.target})")
    print(("PASS" if summary["passed"] else "FAIL") + f": {checks}  ({out})")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
