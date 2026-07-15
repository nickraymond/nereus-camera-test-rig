"""Sequential multi-camera experiment coordination — Spec §11, §13 (Phase 5).

Wires the already-verified pieces together: for each connected camera, capture a
still via its adapter (Phase 1/3/4), checksum + write ``capture.json``, then run the
reference-card analysis (Phase 2) into the run folder (Spec §13).

Pipeline order (Spec §11): create experiment record -> timestamped capture-set dir ->
capture IMX708 -> request N6 -> request AE3 -> collect outputs -> checksums (done by
each adapter as it validates its artifact) -> write raw metadata -> run reference-card
analysis per still -> write analysis results.

**Partial-failure (Spec §11, §12):** each camera runs in its own guard. A failed
capture (or an adapter that can't be built — e.g. a disconnected board) is recorded
in ``experiment.json`` and the loop continues; successful files are retained and the
folder is never deleted. Analysis is best-effort and never fails the *capture*: with
no reference card in frame the analysis simply reports ``status="fail"`` with no tags
and no crop, which is the expected result during mechanical bring-up, not an error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .. import config as config_mod
from ..analysis.result_writer import AnalysisConfig, analyze_reference_card
from ..capture import naming
from ..controller import build_camera, load_camera_profile
from ..logging_config import setup_logging
from ..models import (
    CameraIdentity,
    CaptureRequest,
    CaptureResult,
    DetectionResult,
    ExperimentRecord,
)
from ..storage.experiment_store import ExperimentPaths, ExperimentStore
from ..storage.metadata import write_capture_metadata

# Fixed capture order (Spec §11): the Pi camera first, then the two USB boards. Any
# camera present in config but not named here is captured last, in config order.
CAPTURE_ORDER = ("imx708", "openmv_n6", "openmv_ae3")

logger = logging.getLogger("nereus.coordinator")


@dataclass
class CameraOutcome:
    """One camera's capture + analysis result and where its artifacts landed."""

    camera_name: str
    result: CaptureResult
    image_path: Optional[str] = None
    metadata_path: Optional[str] = None
    analysis: Optional[DetectionResult] = None
    analysis_dir: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.result.ok


@dataclass
class ExperimentOutcome:
    """The full result of one experiment run."""

    record: ExperimentRecord
    paths: ExperimentPaths
    camera_outcomes: list[CameraOutcome] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return bool(self.camera_outcomes) and all(c.ok for c in self.camera_outcomes)

    @property
    def any_ok(self) -> bool:
        return any(c.ok for c in self.camera_outcomes)

    @property
    def status(self) -> str:
        """``completed`` (every camera captured), ``partial``, or ``failed``."""
        if self.all_ok:
            return "completed"
        return "partial" if self.any_ok else "failed"


def _ordered_camera_names(
    cameras: dict[str, Any], subset: Optional[list[str]] = None
) -> list[str]:
    """Canonical capture order, filtered to ``subset`` if given."""
    known = [n for n in CAPTURE_ORDER if n in cameras]
    extra = [n for n in cameras if n not in CAPTURE_ORDER]
    ordered = known + extra
    if subset is not None:
        wanted = set(subset)
        ordered = [n for n in ordered if n in wanted]
    return ordered


def _synth_failed_result(
    name: str, camera_cfg: dict[str, Any], code: str, message: str
) -> CaptureResult:
    """Build a failed ``CaptureResult`` when the adapter can't even be constructed."""
    driver = camera_cfg.get("driver", "unknown")
    platform = {"openmv_usb": "openmv", "imx708": "raspberry_pi"}.get(driver, "unknown")
    identity = CameraIdentity(
        driver=driver,
        platform=platform,
        board=camera_cfg.get("board"),
        serial_number=camera_cfg.get("serial_number"),
    )
    return CaptureResult(
        camera=identity,
        request=CaptureRequest(kind="image"),
        status="failed",
        error={"code": code, "message": message},
    )


def _capture_one_camera(
    name: str,
    camera_cfg: dict[str, Any],
    paths: ExperimentPaths,
    analysis_cfg: AnalysisConfig,
    when: datetime,
    run_analysis: bool,
) -> CameraOutcome:
    """Capture + (best-effort) analyze one camera. Never raises (Spec §11)."""
    cap_dir = paths.capture_dir(name)

    try:
        profile = load_camera_profile(camera_cfg)
        device = build_camera(camera_cfg, profile=profile)
    except Exception as exc:  # unknown driver / bad config — record and keep going
        logger.error("camera %s: could not build adapter: %s", name, exc)
        result = _synth_failed_result(name, camera_cfg, "camera_init_failed", str(exc))
        write_capture_metadata(cap_dir / "capture.json", result)
        return CameraOutcome(camera_name=name, result=result)

    image_name = naming.capture_filename(name, "image", "jpg", when)
    dest = cap_dir / image_name
    request = CaptureRequest(kind="image", settings=dict(profile))

    logger.info("camera %s: capturing -> %s", name, dest)
    try:
        result = device.capture_image(str(dest), request)  # adapters never raise on failure
    finally:
        close = getattr(device, "close", None)
        if callable(close):
            close()

    # Raw metadata sidecar (Spec §13 capture.json), whether the capture passed or failed.
    meta_path = cap_dir / "capture.json"
    write_capture_metadata(meta_path, result)
    outcome = CameraOutcome(
        camera_name=name,
        result=result,
        image_path=result.output_path,
        metadata_path=str(meta_path),
    )

    if not result.ok:
        err = result.error or {}
        logger.error("camera %s: capture FAILED %s: %s", name, err.get("code"), err.get("message"))
        return outcome

    logger.info(
        "camera %s: captured %sx%s %s bytes sha256=%s",
        name, result.width, result.height, result.size_bytes, result.sha256,
    )

    if run_analysis and result.output_path:
        adir = paths.analysis_dir(name)
        det = analyze_reference_card(result.output_path, adir, analysis_cfg)
        outcome.analysis = det
        outcome.analysis_dir = str(adir)
        logger.info(
            "camera %s: analysis status=%s tags=%s crop=%s",
            name, det.status, det.tags_detected, det.card_crop_created,
        )

    return outcome


def run_experiment(
    config: dict[str, Any],
    experiment_type: str,
    *,
    environment_label: str = "",
    operator_notes: str = "",
    camera_names: Optional[list[str]] = None,
    results_root: Optional[str | Path] = None,
    analysis: bool = True,
    when: Optional[datetime] = None,
) -> ExperimentOutcome:
    """Run one sequential capture set across all connected cameras (Spec §11, §13).

    Returns an ``ExperimentOutcome``; a disconnected/failing camera yields a
    ``partial`` status with its slot marked failed in ``experiment.json`` while every
    other camera's raw + analysis artifacts are retained.
    """
    when = when or datetime.now(timezone.utc)
    if results_root is None:
        results_root = config.get("rig", {}).get("results_directory", "./results")

    store = ExperimentStore(results_root)
    paths = store.create(experiment_type, when=when)

    # File-log the run into logs/experiment.log (a Spec §13 deliverable). Console is
    # left to the CLI so unit tests stay quiet.
    setup_logging(log_file=paths.log_path, console=False, logger_name="nereus")

    record = ExperimentRecord(
        experiment_id=paths.experiment_id,
        timestamp=naming.utc_timestamp(when),
        environment_label=environment_label,
        operator_notes=operator_notes,
        experiment_type=experiment_type,
    )

    cameras_cfg = config_mod.enabled_cameras(config)
    ordered = _ordered_camera_names(cameras_cfg, camera_names)
    analysis_cfg = AnalysisConfig.from_dict(config.get("analysis"))

    logger.info(
        "experiment %s: env=%r cameras=%s analysis=%s",
        paths.experiment_id, environment_label, ordered, analysis,
    )

    outcomes: list[CameraOutcome] = []
    for name in ordered:
        outcome = _capture_one_camera(name, cameras_cfg[name], paths, analysis_cfg, when, analysis)
        outcomes.append(outcome)
        record.cameras.append(outcome.result.camera)
        record.captures.append(outcome.result)
        if outcome.analysis is not None:
            record.analyses.append(outcome.analysis)
        if not outcome.ok:
            err = outcome.result.error or {}
            record.errors.append(f"{name}: {err.get('code')}: {err.get('message')}")

    store.write_record(paths, record)

    result = ExperimentOutcome(record=record, paths=paths, camera_outcomes=outcomes)
    logger.info("experiment %s: status=%s", paths.experiment_id, result.status)
    return result
