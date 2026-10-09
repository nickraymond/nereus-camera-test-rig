# ruff: noqa: E501  (embedded child script)
"""Onboard V3 tag detection cost + reliability, run ON THE PI (Sprint28 card-WB proposal, 2026-10-08).

    python scripts/s28_tag_detect.py <sweep_run_dir> [--limit-kib 256000]

Detector: the rig's ``color.locate.locate_frame`` (OpenCV ArUco DICT_APRILTAG_25h9, the V3 card
spec, multi-scale ½x/¼x passes + a 2x pass for frames <= 3 MP), on an 8-bit image file.
Per sweep frame (s<slot>_<arm>.dng/.jpg), two inputs, each in its own child process under
``ulimit -v`` (the units' 250 MiB encoder guard) so a memory overrun fails the child, not the Pi:
  dng_crop  the B3a crop (native x=1504, y=846, 1600x900) of the DNG, bilinear demosaic, the
            green channel normalised to its p99.5 and gamma 1/2.2 -> 8-bit (what the unit has
            in the B3a path before encoding)
  jpeg      the camera's full JPEG (4608x2592), as stored
Recorded: tags found (0-4) and whether the card quad was located, prep s (read + crop +
demosaic / JPEG decode), detect s (locate_frame only), child peak RSS (VmHWM, KiB), rc /
error, plus Lux / exposure / gains from the frame's metadata. Writes <run_dir>/tag_detect.csv.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CROP = (1504, 846, 1600, 900)
CHILD = r"""
import json, sys, time, tempfile
from pathlib import Path
t0 = time.perf_counter()
import numpy as np, cv2
from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.jpeg_geometry import JpegMap
from nereus_camera_test_rig.color.locate import _detect, _record, locate_frame, tag_geometry, tag_spec
card = load_card(sys.argv[3])
kind, path, mode = sys.argv[1], sys.argv[2], sys.argv[4]
t_imp = time.perf_counter()
with tempfile.TemporaryDirectory() as td:
    if kind == "dng_crop":
        from nereus_camera_test_rig.color.raw_io import read_dng, normalize, demosaic_bilinear
        fr = read_dng(path)
        x, y, w, h = %s
        lin, sat, cfa = normalize(fr)
        crop = lin[y:y + h, x:x + w]          # x, y even: the CFA phase is unchanged
        del lin, sat
        g = demosaic_bilinear(crop, cfa)[..., 1]
        g = np.clip(g / max(float(np.percentile(g, 99.5)), 1e-6), 0, 1) ** (1 / 2.2)
        view = Path(td) / "view.png"
        cv2.imwrite(str(view), (g * 255 + 0.5).astype(np.uint8))
    else:
        view = Path(path)
    t_prep = time.perf_counter()
    spec = tag_spec(card)
    if mode == "single":
        # T0.3 bar: one native-scale pass (no 1/2x, 1/4x or 2x), then the same quad checks
        gray = cv2.imread(str(view), cv2.IMREAD_GRAYSCALE)
        found = _detect(gray, set(card.corner_map.values()), (1.0,), family=spec.family)
        rec = _record({i: (det, s, "img") for i, (det, s) in found.items()}, card.corner_map,
                      JpegMap.offset(0, 0), "apriltag", tag_geometry(card), spec.ratio_range)
    else:
        rec = locate_frame(None, view, card.corner_map, JpegMap.offset(0, 0),
                           geometry=tag_geometry(card), spec=spec)
    t_det = time.perf_counter()
try:
    hwm = next(int(l.split()[1]) for l in open("/proc/self/status") if l.startswith("VmHWM:"))
except OSError:  # off Linux (Mac test): ru_maxrss is bytes on macOS
    import resource
    hwm = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
print(json.dumps({"tags": len(rec.get("tags_found", [])), "located": bool(rec.get("located")),
                  "import_s": round(t_imp - t0, 3), "prep_s": round(t_prep - t_imp, 3),
                  "detect_s": round(t_det - t_prep, 3), "vmhwm_kib": hwm,
                  "reason": rec.get("reason", "")}))
""" % (CROP,)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--limit-kib", type=int, default=256000, help="ulimit -v for each child")
    ap.add_argument(
        "--card",
        default=str(Path(__file__).resolve().parents[1] / "configs/cards/nereus_v3_c1.yaml"),
    )
    ap.add_argument(
        "--modes",
        default="single",
        help="single (T0.3 bar: one native-scale pass), multi (locate_frame), or single,multi",
    )
    a = ap.parse_args()
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    rows = []
    for dng in sorted(glob.glob(str(a.run_dir / "s*_*.dng"))):
        stem = os.path.basename(dng)[:-4]
        slot, arm = stem[1:].split("_", 1)
        meta = json.loads(Path(dng[:-4] + ".json").read_text())
        for mode, kind, src in [
            (m, k, s)
            for m in a.modes.split(",")
            for k, s in (("dng_crop", dng), ("jpeg", dng[:-4] + ".jpg"))
        ]:
            t0 = time.perf_counter()
            p = subprocess.run(
                [
                    "/bin/sh",
                    "-c",
                    f'ulimit -v {a.limit_kib}; exec "$0" "$@"',
                    sys.executable,
                    "-c",
                    CHILD,
                    kind,
                    src,
                    a.card,
                    mode,
                ],
                capture_output=True,
                text=True,
                env=env,
            )
            wall = round(time.perf_counter() - t0, 3)
            row = {
                "frame": stem,
                "slot": slot,
                "arm": arm,
                "input": kind,
                "mode": mode,
                "lux": round(meta.get("Lux", 0), 3),
                "exposure_us": meta.get("ExposureTime"),
                "again": round(meta.get("AnalogueGain", 0), 3),
                "dgain": round(meta.get("DigitalGain", 0), 3),
                "rc": p.returncode,
                "wall_s": wall,
            }
            try:
                row.update(json.loads(p.stdout.strip().splitlines()[-1]))
            except (IndexError, ValueError):
                row.update(
                    tags="",
                    located="",
                    error=(p.stderr.strip().splitlines() or ["no output"])[-1][:200],
                )
            rows.append(row)
            print(
                stem,
                mode,
                kind,
                row.get("tags"),
                row.get("detect_s"),
                row.get("vmhwm_kib"),
                row.get("error", ""),
                flush=True,
            )
    keys = sorted(
        {k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99
    )
    with open(a.run_dir / "tag_detect.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print("wrote", a.run_dir / "tag_detect.csv", len(rows), "rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
