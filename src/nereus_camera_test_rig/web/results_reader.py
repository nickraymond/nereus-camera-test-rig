"""Read-only access to experiment result folders — Spec §13, §14 (Phase 6).

The web app displays what is already on disk; the §13 folder layout is the
contract. This module never writes, never invents a new data format, and is
tolerant of partial runs: a camera slot with a failed capture.json (or no
analysis dir) still renders, per the Spec §11 partial-failure rule.

Everything is returned as plain dicts + small dataclasses so templates stay
simple and no coordinator/analysis import is needed just to *view* results.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Same fixed display order the coordinator captures in (Spec §11).
CAMERA_ORDER = ("imx708", "openmv_n6", "openmv_ae3")

# results/<YYYY-MM-DD>/<exp_id>/ path components must be simple names —
# anything else is treated as a traversal attempt and rejected.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EXP_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

logger = logging.getLogger("nereus.web")


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    """Load JSON or return None (missing/corrupt files must not break a page)."""
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("unreadable JSON %s: %s", path, exc)
        return None


@dataclass
class CameraView:
    """One camera's artifacts within a run, resolved from the §13 layout."""

    name: str
    capture: Optional[dict[str, Any]] = None  # captures/<cam>/capture.json
    detection: Optional[dict[str, Any]] = None  # analysis/<cam>/detection.json
    image_rel: Optional[str] = None  # paths relative to the experiment root
    annotated_rel: Optional[str] = None
    crop_rel: Optional[str] = None
    crop_abs: Optional[Path] = None  # for view-time color analysis

    @property
    def ok(self) -> bool:
        return bool(self.capture) and self.capture.get("status") == "completed"

    @property
    def output(self) -> dict[str, Any]:
        """The capture sidecar's nested output block (path/size/dims/sha256)."""
        return (self.capture or {}).get("output") or {}

    @property
    def error(self) -> Optional[dict[str, Any]]:
        return (self.capture or {}).get("error")

    @property
    def analysis_pass(self) -> Optional[bool]:
        if self.detection is None:
            return None
        return self.detection.get("status") == "pass"


@dataclass
class ExperimentView:
    """One experiment folder, fully resolved for display."""

    date: str
    experiment_id: str
    root: Path
    record: dict[str, Any] = field(default_factory=dict)
    cameras: list[CameraView] = field(default_factory=list)

    @property
    def status(self) -> str:
        """completed | partial | failed — same rule as ExperimentOutcome."""
        oks = [c.ok for c in self.cameras]
        if oks and all(oks):
            return "completed"
        return "partial" if any(oks) else "failed"

    @property
    def cameras_ok(self) -> int:
        return sum(1 for c in self.cameras if c.ok)

    @property
    def analyses_pass(self) -> int:
        return sum(1 for c in self.cameras if c.analysis_pass)

    @property
    def analyses_total(self) -> int:
        return sum(1 for c in self.cameras if c.detection is not None)


def safe_experiment_root(results_root: Path, date: str, exp_id: str) -> Path:
    """Resolve results/<date>/<exp_id>, rejecting traversal attempts loudly."""
    if not _DATE_RE.match(date) or not _EXP_ID_RE.match(exp_id):
        raise ValueError(f"invalid experiment reference: {date!r}/{exp_id!r}")
    root = (results_root / date / exp_id).resolve()
    if not root.is_relative_to(results_root.resolve()):
        raise ValueError(f"experiment path escapes results root: {root}")
    return root


def _ordered_camera_dirs(captures_root: Path) -> list[str]:
    if not captures_root.is_dir():
        return []
    present = sorted(d.name for d in captures_root.iterdir() if d.is_dir())
    known = [n for n in CAMERA_ORDER if n in present]
    return known + [n for n in present if n not in CAMERA_ORDER]


def _find_image(cap_dir: Path, capture: Optional[dict[str, Any]]) -> Optional[Path]:
    """The still is named in capture.json; fall back to any image file present."""
    output_path = ((capture or {}).get("output") or {}).get("path")
    if output_path:
        candidate = cap_dir / Path(output_path).name
        if candidate.is_file():
            return candidate
    for pattern in ("*.jpg", "*.jpeg", "*.png"):
        found = sorted(cap_dir.glob(pattern))
        if found:
            return found[0]
    return None


def load_experiment(results_root: Path, date: str, exp_id: str) -> Optional[ExperimentView]:
    """Load one experiment folder for display, or None if it doesn't exist."""
    try:
        root = safe_experiment_root(results_root, date, exp_id)
    except ValueError:
        return None
    if not root.is_dir():
        return None

    view = ExperimentView(
        date=date,
        experiment_id=exp_id,
        root=root,
        record=_read_json(root / "experiment.json") or {},
    )

    for name in _ordered_camera_dirs(root / "captures"):
        cap_dir = root / "captures" / name
        cam = CameraView(name=name, capture=_read_json(cap_dir / "capture.json"))

        image = _find_image(cap_dir, cam.capture)
        if image is not None:
            cam.image_rel = str(image.relative_to(root))

        adir = root / "analysis" / name
        cam.detection = _read_json(adir / "detection.json")
        if (adir / "annotated.jpg").is_file():
            cam.annotated_rel = str((adir / "annotated.jpg").relative_to(root))
        if (adir / "card_crop.jpg").is_file():
            cam.crop_rel = str((adir / "card_crop.jpg").relative_to(root))
            cam.crop_abs = adir / "card_crop.jpg"

        view.cameras.append(cam)

    return view


def list_experiments(results_root: Path) -> list[ExperimentView]:
    """All experiment folders under the results root, newest first.

    Loads each run's record + camera slots (folders are small: a handful of
    JSON reads per run). Folders that don't match the §13 naming are skipped.
    """
    results_root = Path(results_root)
    views: list[ExperimentView] = []
    if not results_root.is_dir():
        return views
    for date_dir in sorted(results_root.iterdir(), reverse=True):
        if not date_dir.is_dir() or not _DATE_RE.match(date_dir.name):
            continue
        for exp_dir in sorted(date_dir.iterdir(), reverse=True):
            if not exp_dir.is_dir() or not (exp_dir / "experiment.json").is_file():
                continue
            loaded = load_experiment(results_root, date_dir.name, exp_dir.name)
            if loaded is not None:
                views.append(loaded)
    return views


def iter_experiment_files(root: Path) -> list[Path]:
    """Every file in a run folder (for the download list + ZIP), stable order."""
    return sorted(p for p in root.rglob("*") if p.is_file())
