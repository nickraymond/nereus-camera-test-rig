"""Experiment run-folder layout + records — Spec §11, §13 (Phase 5).

Owns the on-disk shape of one experiment run and never overwrites a prior one
(CLAUDE.md §11 "preserve raw data"). The layout is exactly Spec §13::

    results/<YYYY-MM-DD>/exp_<ts>_<slug>/
    ├── experiment.json
    ├── captures/<camera>/{<camera>_image_<ts>.jpg, capture.json}
    ├── analysis/<camera>/{detection.json, annotated.jpg, card_crop.jpg}
    └── logs/experiment.log

The store creates the run root + ``logs/`` up front; per-camera ``captures/`` and
``analysis/`` subdirs are created lazily as each camera is captured/analyzed so a
disconnected camera never leaves an empty, misleading directory behind.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..capture import naming
from ..models import ExperimentRecord
from .metadata import write_json


@dataclass
class ExperimentPaths:
    """Resolved paths for one experiment run (Spec §13 layout)."""

    root: Path
    experiment_id: str

    @property
    def captures_root(self) -> Path:
        return self.root / "captures"

    @property
    def analysis_root(self) -> Path:
        return self.root / "analysis"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def log_path(self) -> Path:
        return self.logs_dir / "experiment.log"

    @property
    def record_path(self) -> Path:
        return self.root / "experiment.json"

    def capture_dir(self, camera: str) -> Path:
        """Return (creating if needed) ``captures/<camera>/``."""
        d = self.captures_root / naming.slugify(camera)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def analysis_dir(self, camera: str) -> Path:
        """Return (creating if needed) ``analysis/<camera>/``."""
        d = self.analysis_root / naming.slugify(camera)
        d.mkdir(parents=True, exist_ok=True)
        return d


class ExperimentStore:
    """Creates timestamped experiment folders under a results root."""

    def __init__(self, results_root: str | Path):
        self.results_root = Path(results_root)

    def create(
        self, experiment_type: str, when: Optional[datetime] = None
    ) -> ExperimentPaths:
        """Create a fresh experiment folder and return its resolved paths.

        Raises ``FileExistsError`` if the target folder already exists — an
        experiment must never overwrite a prior run's evidence (CLAUDE.md §11).
        """
        when = when or datetime.now(timezone.utc)
        exp_id = naming.experiment_id(experiment_type, when)
        root = self.results_root / naming.date_folder(when) / exp_id
        if root.exists():
            raise FileExistsError(f"experiment folder already exists: {root}")
        root.mkdir(parents=True)
        (root / "logs").mkdir()
        return ExperimentPaths(root=root, experiment_id=exp_id)

    def write_record(self, paths: ExperimentPaths, record: ExperimentRecord) -> Path:
        """Write ``experiment.json`` for the run (atomic)."""
        return write_json(paths.record_path, record.to_dict())
