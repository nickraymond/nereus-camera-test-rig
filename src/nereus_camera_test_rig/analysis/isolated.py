"""Run the reference-card analysis in a child process — Spec §11 (partial failure).

Prior art: bm_cam_legacy runs its heavy HEIC encode in an isolated helper process on the
Pi Zero 2 W (``heic_encode_helper.py``). Same reason here: on the Zero 2 W rig
(``nereus002``, 415 MB) the 12 MP analysis peaks at ~243 MB of ~265 MB free (2026-09-28).
If the kernel OOM-kills it, only the child dies: the coordinator records a failed analysis
with the reason and still writes ``experiment.json`` and the other cameras' results.

Child: ``python -m nereus_camera_test_rig.analysis.isolated <image> <out_dir> <config json>``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from ..models import DetectionResult
from .result_writer import AnalysisConfig, analyze_reference_card

TIMEOUT_S = 300.0
SRC = Path(__file__).resolve().parents[2]  # run this checkout's code in the child


def _failed(config: AnalysisConfig, out_dir: Path, message: str) -> DetectionResult:
    result = DetectionResult(status="fail", expected_tags=list(config.expected_tag_ids),
                             errors=[message])
    (out_dir / "detection.json").write_text(json.dumps(result.to_dict(), indent=2))
    return result


def analyze_isolated(image_path: str | Path, out_dir: str | Path, config: AnalysisConfig,
                     timeout: float = TIMEOUT_S) -> DetectionResult:
    """``analyze_reference_card`` in a child process; never raises (Spec §11)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    detection = out_dir / "detection.json"
    detection.unlink(missing_ok=True)
    cmd = [sys.executable, "-m", "nereus_camera_test_rig.analysis.isolated", str(image_path),
           str(out_dir), json.dumps(dataclasses.asdict(config))]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(SRC), *filter(None, [os.environ.get("PYTHONPATH")])])}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return _failed(config, out_dir, f"analysis timed out after {timeout:.0f} s")
    if proc.returncode == 0 and detection.is_file():
        data = json.loads(detection.read_text())
        names = {f.name for f in dataclasses.fields(DetectionResult)}
        return DetectionResult(**{k: v for k, v in data.items() if k in names})
    code = proc.returncode
    why = (f"killed by {signal.Signals(-code).name}" if code < 0 else f"exit {code}")
    if code == -signal.SIGKILL:
        why += " (likely out of memory — see the kernel log)"
    tail = proc.stderr.strip().splitlines()[-3:]
    return _failed(config, out_dir, f"analysis process {why}" + (f": {' | '.join(tail)}"
                                                                   if tail else ""))


def main(argv: list[str]) -> int:
    image, out_dir, cfg = argv
    data = json.loads(cfg)
    data["scales"] = tuple(data["scales"])
    analyze_reference_card(image, out_dir, AnalysisConfig(**data))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
