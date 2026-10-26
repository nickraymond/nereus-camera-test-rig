#!/usr/bin/env python3
"""n6_raw_compare_capture.py — the exposure plan of docs/n6_eval/TEST_CARD_RAW_COMPARE.md on the N6 (Pi side).

Calls the rig's ``capture_raw`` (8-bit Bayer, locked exposure + gain, AWB frozen, SHA-256 checked by the adapter) once
per frame and checks every sidecar against the card's validity rules: exposure read-back within 2 % of the request and
``frame_time_us`` null. A non-null ``frame_time_us`` means the board took the PAG7936 frame-time register path
(``capture_service._set_frame_time``), which must never run on the PS5520: the script stops, sends ``reset_board`` and
exits 3. Two failed captures in a row stop it with exit 2. Every frame is logged to ``<out>/manifest.jsonl``.

Usage (Pi, repo root):
  .venv/bin/python scripts/n6_raw_compare_capture.py --out DIR --exposures-us 2000 4000 8000 --frames 10 \
      [--framesize HD|WQXGA2] [--label dark]
Exit 0 = every frame captured and valid; 1 = a frame failed a check (kept, logged); 2 = two failures in a row;
3 = frame-time path taken (stopped, board reset).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nereus_camera_test_rig.cameras.openmv_usb import OpenMvUsbCamera  # noqa: E402
from nereus_camera_test_rig.models import CaptureRequest  # noqa: E402

SERIAL = "020023000450433547373200"
EXPOSURE_TOL = 0.02


def check(sidecar: dict, exposure_us: int) -> list[str]:
    """The card's per-frame validity rules; an empty list = valid."""
    problems = []
    if sidecar.get("frame_time_us") is not None:
        problems.append("frame_time_us=%s (frame-time register path taken)" % sidecar["frame_time_us"])
    got = sidecar.get("exposure_us")
    if got is None or abs(got - exposure_us) > EXPOSURE_TOL * exposure_us:
        problems.append("exposure read-back %s us vs requested %d" % (got, exposure_us))
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--exposures-us", type=int, nargs="+", required=True)
    ap.add_argument("--frames", type=int, default=10)
    ap.add_argument("--framesize", default="HD", help="HD, or WQXGA2 (PS5520 2592x1944, card amendment 1)")
    ap.add_argument("--gain-db", type=float, default=1.0, help="requested gain; each driver clamps to its floor")
    ap.add_argument("--label", default="lit", help="file prefix, e.g. lit / dark")
    ap.add_argument("--serial", default=SERIAL)
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    manifest = args.out / "manifest.jsonl"
    cam = OpenMvUsbCamera(args.serial)
    rc, fails_in_row = 0, 0
    try:
        for exp in args.exposures_us:
            for i in range(1, args.frames + 1):
                dest = args.out / ("%s_%s_e%05d_%02d.bayer" % (args.label, args.framesize, exp, i))
                if dest.exists():
                    print("exists, not overwriting: %s" % dest, file=sys.stderr)
                    return 2
                t0 = time.time()
                settings = {"framesize": args.framesize, "exposure_us": exp, "gain_db": args.gain_db}
                result = cam.capture_raw(str(dest), CaptureRequest(kind="image", settings=settings))
                row = {"file": dest.name, "label": args.label, "framesize": args.framesize,
                       "exposure_req_us": exp, "frame": i,
                       "status": result.status, "error": result.error, "sha256": result.sha256,
                       "size_bytes": result.size_bytes, "wall_s": round(time.time() - t0, 2),
                       "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                if result.status != "completed":
                    fails_in_row += 1
                    rc = max(rc, 1)
                    row["problems"] = ["capture failed"]
                else:
                    fails_in_row = 0
                    sidecar = json.loads(dest.with_suffix(".json").read_text())
                    row.update({k: sidecar.get(k) for k in ("width", "height", "exposure_us", "gain_db",
                                                             "frame_time_us", "cfa")})
                    row["problems"] = check(sidecar, exp)
                    if row["problems"]:
                        rc = max(rc, 1)
                with manifest.open("a") as f:
                    f.write(json.dumps(row) + "\n")
                print(json.dumps(row), flush=True)
                if any("frame_time_us" in p for p in row.get("problems", [])):
                    print("STOP: frame-time path taken; resetting the board", file=sys.stderr)
                    cam.reset_board()
                    return 3
                if fails_in_row >= 2:
                    print("STOP: two failed captures in a row", file=sys.stderr)
                    return 2
    finally:
        cam.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
