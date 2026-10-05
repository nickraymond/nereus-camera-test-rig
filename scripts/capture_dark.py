#!/usr/bin/env python3
"""capture_dark.py — lens-covered dark RAW frames at fixed exposures (S4 noise floor).

Run ON the rig Pi from the repo root with the rig venv, lenses covered, lamps may stay on:

    .venv/bin/python scripts/capture_dark.py --camera imx708|n6|ae3 \
        --exposures-us 20000 40000 80000 [--repeat 3]

Each frame is taken at the camera's lowest analogue gain (IMX708 1.0 -> 1.1228 applied,
OpenMV 3.15 dB floor, OQ-21) — the gain the S4 recipes lock. IMX708: ``rpicam-still --raw``
(DNG + JPEG + metadata, AWB/AF off); OpenMV: allowlisted ``capture_raw`` after a
``reset_board`` (one camera session per boot on the AE3). Writes
``results/dark/<UTC>_<camera>/dark_<us>_r<n>.{dng|bayer,...}`` + ``dark.json`` (per frame:
read-back exposure / gain, mean and max of the raw values). Never overwrites.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from nereus_camera_test_rig.color.raw_io import read_dng, read_openmv_bayer  # noqa: E402

SERIALS = {"n6": "020023000450433547373200", "ae3": "0829c14000000000"}  # nereus002
MIN_GAIN_DB = 3.152157


def imx708(out: Path, exposure_us: int, n: int) -> dict:
    name = f"dark_{exposure_us}_r{n}"
    # cwd + bare names: rpicam-still truncates -o paths at 127 chars (OQ-24 addendum)
    subprocess.run(["rpicam-still", "-n", "-t", "500", "--mode", "4608:2592", "--width", "4608",
                    "--height", "2592", "--raw", "-o", f"{name}.jpg", "--metadata",
                    f"{name}.json", "--metadata-format", "json", "--shutter", str(exposure_us),
                    "--gain", "1.0", "--awb", "custom", "--awbgains", "1,1",
                    "--autofocus-mode", "manual", "--lens-position", "0"],
                   cwd=out, check=True, capture_output=True, timeout=60)
    meta = json.loads((out / f"{name}.json").read_text())
    frame = read_dng(out / f"{name}.dng")
    return {"file": f"{name}.dng", "exposure_us": meta.get("ExposureTime"),
            "gain": meta.get("AnalogueGain"), "frame": frame}


def openmv(cam, out: Path, exposure_us: int, n: int) -> dict:
    from nereus_camera_test_rig.models import CaptureRequest

    name = f"dark_{exposure_us}_r{n}.bayer"
    cam.reset_board()
    r = cam.capture_raw(str(out / name), CaptureRequest(kind="image", settings={
        "warmup_ms": 300, "exposure_us": exposure_us, "gain_db": MIN_GAIN_DB}))
    if not r.ok:
        raise RuntimeError(f"capture_raw {name}: {r.error}")
    side = json.loads((out / name).with_suffix(".json").read_text())
    return {"file": name, "exposure_us": side["exposure_us"], "gain_db": side["gain_db"],
            "frame": read_openmv_bayer(out / name)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--camera", choices=["imx708", *SERIALS], required=True)
    ap.add_argument("--exposures-us", type=int, nargs="+", required=True)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "dark")
    args = ap.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out / f"{stamp}_{args.camera}"
    out.mkdir(parents=True, exist_ok=False)
    cam = None
    if args.camera != "imx708":
        from nereus_camera_test_rig.cameras.openmv_usb import OpenMvUsbCamera
        cam = OpenMvUsbCamera(serial_number=SERIALS[args.camera], board=args.camera)
    frames = []
    try:
        for e in args.exposures_us:
            for n in range(args.repeat):
                rec = imx708(out, e, n) if cam is None else openmv(cam, out, e, n)
                f = rec.pop("frame")
                # one CFA site (top-left) against its own black level; float32 keeps the
                # 12 MP IMX708 frame inside the Zero's RAM
                m = f.mosaic[::2, ::2].astype(np.float32) - float(f.black_level[0])
                rec.update(black_level=list(f.black_level), mean_above_black=round(float(
                    m.mean()), 4), std=round(float(m.std()), 4), max=float(f.mosaic.max()))
                frames.append(rec)
                print(f"{rec['file']}: exposure {rec['exposure_us']} us, mean above black "
                      f"{rec['mean_above_black']}, std {rec['std']}, max {rec['max']:g}")
    finally:
        if cam is not None:
            cam.close()
    (out / "dark.json").write_text(json.dumps({"utc": stamp, "camera": args.camera,
                                               "frames": frames}, indent=1) + "\n")
    print(f"{len(frames)} dark frames in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
