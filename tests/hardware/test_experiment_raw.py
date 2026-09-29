"""Hardware test: one coordinated capture set with RAW on every camera — Phase 8 S3 demo.

Runs on the rig Pi (``nereus002``) with all three cameras and the capture service deployed:
``PYTHONPATH=$PWD/src:$PWD .venv/bin/python -m pytest tests/hardware/test_experiment_raw.py``.
Stop Nick's workbench recipe first. Checks the artifacts, not the exit status (CLAUDE.md §19):
each camera has its still + ``capture.json`` and a RAW + ``raw_capture.json``, every RAW's
SHA-256 matches the record, and it reopens as a ``RawFrame`` with the expected layout.
"""

import hashlib
import json
from pathlib import Path

import pytest

from nereus_camera_test_rig import config as config_mod
from nereus_camera_test_rig.capture.coordinator import run_experiment
from nereus_camera_test_rig.color.stages import open_raw

REPO = Path(__file__).resolve().parents[2]
EXPECTED = {  # camera -> (mosaic shape, CFA, white level); OQ-21, OQ-24
    "imx708": ((2592, 4608), "BGGR", 1023.0),
    "openmv_n6": ((800, 1280), "BGGR", 255.0),
    "openmv_ae3": ((800, 1280), "BGGR", 255.0),
}


def test_experiment_with_raw_on_all_cameras():
    # On the SD card like a real run, not pytest's tmp_path: /tmp on the Zero 2 W is a RAM
    # disk (208 MB of 415 MB), and a run with a 24 MB DNG there failed the IMX708 write.
    cfg = config_mod.load_rig_config(REPO / "configs" / "rig.example.yaml")
    outcome = run_experiment(cfg, "s3_raw_hw_test", environment_label="hardware-test",
                             results_root=REPO / "results" / "hw_tests", analysis=False,
                             raw=True)
    assert outcome.status == "completed", outcome.record.errors
    record = json.loads((outcome.paths.root / "experiment.json").read_text())
    assert len(record["raw_captures"]) == len(EXPECTED) and not record["errors"]
    for c in outcome.camera_outcomes:
        cap_dir = outcome.paths.capture_dir(c.camera_name)
        assert (cap_dir / "capture.json").is_file() and (cap_dir / "raw_capture.json").is_file()
        raw = c.raw_result
        assert raw is not None and raw.ok, raw and raw.error
        path = Path(raw.output_path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == raw.sha256
        frame = open_raw(path)
        shape, cfa, white = EXPECTED[c.camera_name]
        assert (frame.mosaic.shape, frame.cfa, frame.white_level) == (shape, cfa, white)
        assert frame.exposure_s and frame.exposure_s > 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
