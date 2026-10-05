#!/usr/bin/env python3
"""Re-run the exposure-sweep scoring + pick on a stored experiment (no new captures).

    PYTHONPATH=src python scripts/rescore_sweep.py <experiment_dir> --out rescore.json

Reads each camera's sweep (files, shutters, ROI, tolerance) from experiment.json and scores the
RAWs again with the current code. experiment.json is never modified; the result goes to --out.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from nereus_camera_test_rig.color.exposure_sweep import DEFAULT_CARD, score_sweep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("experiment", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--card", type=Path, default=DEFAULT_CARD)
    a = ap.parse_args()
    rec = json.loads((a.experiment / "experiment.json").read_text())
    out = {"experiment": rec.get("experiment_id", a.experiment.name), "cameras": {}}
    for cam, sw in rec.get("exposure_sweeps", {}).items():
        files = [a.experiment / "captures" / cam / f["file"] for f in sw["frames"]]
        shutters = [f["shutter_us"] for f in sw["frames"]]
        roi = sw["fallback_region"] if isinstance(sw["fallback_region"], list) else None
        t0 = time.monotonic()
        new = score_sweep(files, shutters, a.card, sw["pick"].get("tolerance", 0.10), roi)
        out["cameras"][cam] = {"stored_pick_us": sw["pick"]["shutter_us"],
                               "new_pick_us": new["pick"]["shutter_us"],
                               "seconds": round(time.monotonic() - t0, 1), **new}
        print(f"{cam}: stored {sw['pick']['shutter_us']} us -> new {new['pick']['shutter_us']} us"
              f" | {new['clip_test']} | {new['pick']['reason']}")
    a.out.write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
