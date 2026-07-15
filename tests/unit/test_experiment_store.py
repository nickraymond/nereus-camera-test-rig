"""Unit tests for the experiment run-folder store — Spec §13 (Phase 5)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from nereus_camera_test_rig.models import ExperimentRecord
from nereus_camera_test_rig.storage.experiment_store import ExperimentStore

WHEN = datetime(2026, 7, 15, 18, 0, 0, tzinfo=timezone.utc)


def test_create_builds_spec13_layout(tmp_path):
    store = ExperimentStore(tmp_path)
    paths = store.create("reference_card_above_water", when=WHEN)

    assert paths.experiment_id == "exp_20260715T180000Z_reference_card_above_water"
    # Date-partitioned folder under the results root (Spec §13).
    assert paths.root == tmp_path / "2026-07-15" / paths.experiment_id
    assert paths.root.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.log_path == paths.root / "logs" / "experiment.log"
    assert paths.record_path == paths.root / "experiment.json"

    # Per-camera dirs are created lazily and land under captures/ and analysis/.
    cap = paths.capture_dir("imx708")
    an = paths.analysis_dir("imx708")
    assert cap == paths.root / "captures" / "imx708" and cap.is_dir()
    assert an == paths.root / "analysis" / "imx708" and an.is_dir()


def test_create_never_overwrites(tmp_path):
    store = ExperimentStore(tmp_path)
    store.create("reference_card", when=WHEN)
    # Same type + same timestamp -> same folder -> must refuse (CLAUDE.md §11).
    with pytest.raises(FileExistsError):
        store.create("reference_card", when=WHEN)


def test_write_record_is_readable(tmp_path):
    store = ExperimentStore(tmp_path)
    paths = store.create("reference_card", when=WHEN)
    record = ExperimentRecord(
        experiment_id=paths.experiment_id,
        timestamp="20260715T180000Z",
        environment_label="bench",
        experiment_type="reference_card",
    )
    written = store.write_record(paths, record)
    assert written == paths.record_path

    data = json.loads(paths.record_path.read_text())
    assert data["experiment_id"] == paths.experiment_id
    assert data["environment_label"] == "bench"
