"""Stage ``grvi`` — the backend GRVI ``cheeca_v3`` baseline (SPEC §4 Phase 8 S2a (a), OQ-31).

Mac-only, dev-only: GRVI never ships from this repo and is never imported by it. The stage

1. resolves ``--ref`` in the backend checkout to a full commit SHA and exports only
   ``backend/app`` at that commit with ``git archive`` (read-only on the checkout — the
   working tree, its branch and its virtualenv are not touched);
2. runs ``host_tools/grvi_runner.py`` in the backend's own Python environment (``--python``,
   built from the backend's pinned ``requirements.txt``, see ``docs/hardware_setup.md``) on the
   camera JPEG of every located card frame;
3. samples GRVI's output with the same patch boxes on the same card area as every other method
   (the RAW tag-centre quad through the dataset's RAW → JPEG map, ``patches.sample_jpeg``).

A frame where GRVI finds no card keeps the camera JPEG (the backend's ``no_card`` result) and is
recorded as such; ``correct`` scores it on the as-shot JPEG. Frames in the map's
``exclude_frames`` are skipped. Output: ``grvi/{images/, sidecars/, patches.json,
summary.json, stage.json}``.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import tempfile
from functools import partial
from pathlib import Path
from typing import Any

from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.jpeg_geometry import JpegMap
from nereus_camera_test_rig.color.patches import sample_jpeg
from nereus_camera_test_rig.color.stages import run_parallel, verify_fresh, write_stage

RUNNER = Path(__file__).with_name("grvi_runner.py")
GRVI_WORKERS = 4  # GRVI peaks at ~4.2 GB per 12 MP frame (measured on the TG-7, 2026-09-27)
EXPORT_PATHS = ("backend/__init__.py", "backend/app")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def export_backend(repo: Path, ref: str, dest: Path) -> str:
    """``git archive`` of the backend code at ``ref`` into ``dest``; returns the full SHA."""
    sha = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    archive = subprocess.run(["git", "-C", str(repo), "archive", sha, *EXPORT_PATHS],
                             capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
    return sha


def _sample(stem: str, image: Path, quad_raw, card, jpeg_map) -> tuple[str, dict]:
    try:
        return stem, sample_jpeg(image, quad_raw, card, jpeg_map)
    except (OSError, ValueError) as exc:
        return stem, {"error": f"{type(exc).__name__}: {exc}"}


def grvi(locate_dir: Path, dataset_config: Path, card_path: Path, backend_repo: Path,
         ref: str, python: Path, workers: int | None = None,
         grvi_workers: int = GRVI_WORKERS) -> dict[str, Any]:
    locate_record = verify_fresh(locate_dir)
    root = locate_dir.parent
    dataset_dir = Path(verify_fresh(root / "ingest")["params"]["dataset_dir"])
    rows = {r["stem"]: r for r in csv.DictReader((root / "ingest" / "manifest.csv").open())}
    corners = json.loads((locate_dir / "corners.json").read_text())
    jmap = JpegMap.from_config(locate_record["params"])
    card = load_card(card_path)
    workers = workers or os.cpu_count() or 1

    stems = [s for s, rec in corners.items()
             if rec.get("located") and rows[s]["jpeg"] and s not in jmap.exclude_frames]
    out_dir = root / "grvi"
    shutil.rmtree(out_dir, ignore_errors=True)  # never mix runs
    out_dir.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        sha = export_backend(backend_repo, ref, Path(tmp))
        jobs = out_dir / "jobs.json"
        jobs.write_text(json.dumps({"out_dir": str(out_dir), "workers": grvi_workers, "frames": [
            {"stem": s, "jpeg": str(dataset_dir / rows[s]["jpeg"])} for s in stems]}))
        env = {**os.environ, "PYTHONPATH": tmp}
        proc = subprocess.run([str(python), str(RUNNER), str(jobs)], capture_output=True,
                              text=True, env=env)
        if proc.returncode != 0:
            raise RuntimeError(f"GRVI runner failed ({python}, backend {sha[:10]}):\n"
                               f"{proc.stderr[-2000:]}")
        runner = json.loads(proc.stdout.strip().splitlines()[-1])

    detected = [s for s in stems if (out_dir / "images" / f"{s}.jpg").is_file()]
    fn = partial(_sample, card=card, jpeg_map=jmap)
    sampled = dict(run_parallel(fn, [(s, out_dir / "images" / f"{s}.jpg",
                                      corners[s]["quad_raw"]) for s in detected], workers))
    results = {s: sampled.get(s, {"no_card": True}) for s in stems}
    (out_dir / "patches.json").write_text(json.dumps(results, separators=(",", ":")) + "\n")
    summary = {"frames": len(stems), "card_detected": len(detected),
               "no_card": sorted(set(stems) - set(detected)),
               "errors": {s: r["error"] for s, r in results.items() if "error" in r},
               "skipped_jpeg_excluded": sorted(set(jmap.exclude_frames) & set(corners)),
               "backend_sha": sha, "backend_ref": ref, "runner": runner}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_stage(out_dir, "grvi", configs=[dataset_config, card_path], upstream=[locate_dir],
                params={"backend_repo": str(backend_repo), "backend_ref": ref,
                        "backend_sha": sha, "export_paths": list(EXPORT_PATHS),
                        "python": str(python), "runner_env": runner,
                        "sampling": "raw quad through jpeg_from_raw, central 60 %"})
    summary["out_dir"] = str(out_dir)
    return summary
