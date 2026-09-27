"""Run the backend's GRVI ``cheeca_v3`` correction on camera JPEGs — SPEC §4 Phase 8 S2a (a).

Runs **inside the backend's own Python environment** (OQ-31), never this repo's: it imports
only the standard library, Pillow and ``backend.app.services.processing.grvi`` from an
exported backend tree on ``PYTHONPATH``. Nothing is re-derived: each JPEG is decoded exactly
as the backend worker does (``Image.open(...).convert("RGB")``, no EXIF transpose), passed to
the unmodified ``grvi.correct(image, profile, layout)`` with the default ``cheeca_v3`` profile
and V2 layout, and encoded with ``grvi.encode_jpeg``. A frame where GRVI finds no card gets
no output image (the backend returns ``no_card`` and keeps the original).

Usage (called by ``host_tools.grvi_baseline``)::

    PYTHONPATH=<exported backend root> <backend python> host_tools/grvi_runner.py jobs.json

``jobs.json``: ``{"out_dir": ..., "workers": N, "frames": [{"stem": ..., "jpeg": ...}, ...]}``.
Writes ``<out_dir>/images/<stem>.jpg`` + ``<out_dir>/sidecars/<stem>.json`` and prints one
JSON line with the environment.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path


def _one(job: dict, out_dir: str) -> dict:
    import numpy as np
    from backend.app.services.processing import grvi
    from PIL import Image

    t0 = time.perf_counter()
    image = Image.open(job["jpeg"]).convert("RGB")  # = image_derivatives.decode_image_bytes_to_rgb
    image.load()
    corrected, core = grvi.correct(np.asarray(image), grvi.load_profile(), grvi.load_layout())
    out = Path(out_dir)
    if corrected is not None:
        (out / "images" / f"{job['stem']}.jpg").write_bytes(grvi.encode_jpeg(corrected))
    core = {"stem": job["stem"], "input": job["jpeg"], **core,
            "duration_ms": int(round((time.perf_counter() - t0) * 1000))}
    (out / "sidecars" / f"{job['stem']}.json").write_text(json.dumps(core, indent=1) + "\n")
    return {"stem": job["stem"], "card_detected": core["card_detected"]}


def main(jobs_path: str) -> int:
    from backend.app.services.processing import grvi

    jobs = json.loads(Path(jobs_path).read_text())
    out = Path(jobs["out_dir"])
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "sidecars").mkdir(parents=True, exist_ok=True)
    frames = jobs["frames"]
    with ProcessPoolExecutor(int(jobs.get("workers", 4))) as pool:
        done = list(pool.map(_one, frames, [str(out)] * len(frames)))
    profile = grvi.load_profile()
    print(json.dumps({"frames": len(done),
                      "card_detected": sum(d["card_detected"] for d in done),
                      "upstream_commit": grvi.UPSTREAM_COMMIT,
                      "versions": grvi.runtime_versions(),
                      "pipeline_constants": grvi.pipeline_constants(profile),
                      "profile": grvi.DEFAULT_PROFILE.name,
                      "layout": grvi.DEFAULT_LAYOUT.name}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
