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
from ..sensors.depth import safe_read
from ..storage.experiment_store import ExperimentPaths, ExperimentStore
from ..storage.metadata import write_capture_metadata
from .exposure_lock import merge_settings, overrides_from_lock

# Fixed capture order (Spec §11): the Pi camera first, then the two USB boards. Any
# camera present in config but not named here is captured last, in config order.
CAPTURE_ORDER = ("imx708", "openmv_n6", "openmv_ae3")

logger = logging.getLogger("nereus.coordinator")


def _load_analyzer(analysis_config: Optional[dict[str, Any]]):
    """Return ``(AnalysisConfig, analyze_fn)`` or ``None`` if the extra is absent.

    The reference-card pipeline needs the optional ``analysis`` extra (OpenCV). We
    import it lazily so a capture-only rig (no OpenCV) still runs — captures are the
    raw evidence and must never be gated on the analysis dependency (CLAUDE.md §11).
    """
    try:
        from ..analysis.isolated import analyze_isolated
        from ..analysis.result_writer import AnalysisConfig
    except ImportError:
        return None
    # In a child process: an OOM kill on the Zero 2 W then fails only the analysis, and the
    # experiment record is still written (analysis/isolated.py).
    return AnalysisConfig.from_dict(analysis_config), analyze_isolated


@dataclass
class CameraOutcome:
    """One camera's capture + analysis result and where its artifacts landed."""

    camera_name: str
    result: CaptureResult
    image_path: Optional[str] = None
    metadata_path: Optional[str] = None
    analysis: Optional[DetectionResult] = None
    analysis_dir: Optional[str] = None
    raw_result: Optional[CaptureResult] = None  # Phase 8 S3, when raw capture is on
    # Exposure sweep (pool tool): (shutter_us, result) per frame, when the sweep is on
    sweep: list = field(default_factory=list)

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


def _reset_if_asked(name: str, device, profile: dict[str, Any], why: str) -> None:
    """Hard-reset the board when its profile asks for it. Best-effort: a board that can't
    reset (older deployed board code, transient USB trouble) still gets its capture."""
    if not profile.get("reset_before_capture"):
        return
    reset = getattr(device, "reset_board", None)
    if callable(reset):
        try:
            r = reset()
            logger.info("camera %s: board hard-reset before %s (%.1fs to ready)",
                        name, why, r.get("duration_seconds", 0.0))
        except Exception as exc:
            logger.warning("camera %s: reset before %s failed (continuing): %s", name, why, exc)


def _capture_raw(name: str, device, profile: dict[str, Any], cap_dir: Path,
                 when: datetime) -> CaptureResult:
    """The RAW step (Phase 8 S3, SPEC §20 ``raw: true``): after the still, same camera guard.
    Boards with ``reset_before_capture`` are reset again first — the AE3 allows one camera
    session per boot on OpenMV v5 (PR #70). Never raises."""
    capture_raw = getattr(device, "capture_raw", None)
    request = CaptureRequest(kind="image", settings=dict(profile))
    if not callable(capture_raw):
        return CaptureResult(camera=_identity(device), request=request, status="failed",
                             error={"code": "not_supported",
                                    "message": f"{type(device).__name__} has no capture_raw"})
    _reset_if_asked(name, device, profile, "raw capture")
    ext = getattr(device, "raw_extension", "raw")
    dest = cap_dir / naming.capture_filename(name, "raw", ext, when)
    logger.info("camera %s: capturing RAW -> %s", name, dest)
    return capture_raw(str(dest), request)


DEFAULT_SWEEP_SHUTTERS_US = (4000, 8000, 16667, 33333, 66667)  # 1/250 … 1/15 s


def sweep_settings(profile: dict[str, Any], override: Optional[dict[str, Any]],
                   camera: Optional[str] = None) -> Optional[dict[str, Any]]:
    """The exposure-sweep config for one camera, or None when it is off (the default).
    ``override`` (from the CLI) wins over the profile's ``exposure_sweep`` block; its ``roi``
    may be one box for every camera or ``{camera: box}`` (ROIs are in each camera's own px)."""
    cfg = {**(profile.get("exposure_sweep") or {}), **(override or {})}
    for key in ("roi", "shutters_us"):  # one value for every camera, or {camera: value}
        if isinstance(cfg.get(key), dict):
            cfg[key] = cfg[key].get(camera, cfg[key].get("_default"))
    if not cfg.get("enabled"):
        return None
    shutters = [int(v) for v in (cfg.get("shutters_us") or DEFAULT_SWEEP_SHUTTERS_US)]
    roi = cfg.get("roi")
    return {"shutters_us": shutters, "repeats": max(int(cfg.get("repeats", 1)), 1),
            "gain": cfg.get("gain", "floor"),
            "tolerance": float(cfg.get("tolerance", 0.10)), "card": cfg.get("card"),
            "roi": [int(v) for v in roi] if roi else None}


def _capture_sweep(name: str, device, profile: dict[str, Any], cap_dir: Path, when: datetime,
                   sweep: dict[str, Any], still: Optional[CaptureResult] = None) -> list:
    """N RAW frames at the shutter ladder, gain locked at the camera's floor, back to back
    (each with the profile's reset first where it asks for one). Never raises."""
    lock = getattr(device, "locked_exposure_settings", None)
    capture_raw = getattr(device, "capture_raw", None)
    frames = []
    ladder = [sh for sh in sweep["shutters_us"] for _ in range(sweep.get("repeats", 1))]
    for i, shutter in enumerate(ladder):
        if not callable(lock) or not callable(capture_raw):
            frames.append((shutter, CaptureResult(
                camera=_identity(device), request=CaptureRequest(kind="image"), status="failed",
                error={"code": "not_supported",
                       "message": f"{type(device).__name__} cannot lock exposure for a sweep"})))
            continue
        settings = merge_settings(profile, lock(shutter))
        # Focus fixed for the whole sweep: a camera that reports a lens position on its still
        # (the IMX708's autofocus) is locked there, so frames differ only in shutter time.
        lens = ((still.sensor_metadata or {}) if still is not None else {}).get("LensPosition")
        if lens is not None:
            settings = merge_settings(settings, {"camera_controls": {"focus": {
                "mode": "manual", "lens_position": float(lens)}}})
        _reset_if_asked(name, device, settings, f"sweep frame {i}")
        ext = getattr(device, "raw_extension", "raw")
        dest = cap_dir / naming.capture_filename(name, f"raw_sweep{i:02d}", ext, when)
        logger.info("camera %s: sweep frame %d at %d us -> %s", name, i, shutter, dest.name)
        try:
            res = capture_raw(str(dest), CaptureRequest(kind="image", settings=settings))
        except Exception as exc:
            logger.exception("camera %s: adapter raised during sweep frame %d", name, i)
            res = CaptureResult(camera=_identity(device), request=CaptureRequest(kind="image"),
                                status="failed", error={"code": "adapter_exception",
                                                        "message": repr(exc)})
        frames.append((shutter, res))
    return frames


def _score_sweeps(record: ExperimentRecord, outcomes: list, sweeps: dict[str, dict]) -> None:
    """Score each camera's sweep on its RAWs and pick a frame (color.exposure_sweep). Best
    effort: a scoring failure is a warning, the frames are kept either way."""
    for o in outcomes:
        if not o.sweep:
            continue
        sweep = sweeps[o.camera_name]
        entry: dict[str, Any] = {
            "shutters_us": sweep["shutters_us"], "repeats": sweep.get("repeats", 1),
            "gain": sweep["gain"],
            "captures": [{"shutter_us": sh, "status": r.status, "file": Path(r.output_path).name
                        if r.output_path else None, "error": r.error,
                        "readback": {k: (r.sensor_metadata or {}).get(k) for k in
                                     ("ExposureTime", "AnalogueGain", "DigitalGain",
                                      "exposure_us", "gain_db")}} for sh, r in o.sweep]}
        ok = [(sh, r) for sh, r in o.sweep if r.ok and r.output_path]
        try:
            from ..color.exposure_sweep import DEFAULT_CARD, score_sweep
            scored = score_sweep([Path(r.output_path) for _, r in ok], [sh for sh, _ in ok],
                                 Path(sweep["card"]) if sweep.get("card") else DEFAULT_CARD,
                                 sweep["tolerance"], sweep.get("roi"))
            entry.update(scored)
            p = scored["pick"]
            logger.info("camera %s: sweep pick %s us — %s", o.camera_name, p["shutter_us"],
                        p["reason"])
        except Exception as exc:  # scoring must never cost the frames
            logger.exception("camera %s: sweep scoring failed", o.camera_name)
            record.warnings.append(f"{o.camera_name} sweep scoring failed: {exc!r}")
        record.exposure_sweeps[o.camera_name] = entry


def _identity(device) -> CameraIdentity:
    current = getattr(device, "_current_identity", None)
    return current() if callable(current) else CameraIdentity(
        driver=getattr(device, "driver", "unknown"), platform="unknown")


def _capture_one_camera(
    name: str,
    camera_cfg: dict[str, Any],
    paths: ExperimentPaths,
    analyzer,
    when: datetime,
    raw: Optional[bool] = None,
    settings_override: Optional[dict[str, Any]] = None,
    sweep: Optional[dict[str, Any]] = None,
) -> CameraOutcome:
    """Capture + (best-effort) analyze one camera. Never raises (Spec §11).

    ``settings_override`` (pool spec §5.2, from an exposure lock) is deep-merged into the
    camera profile, so the still and the RAW both use it."""
    cap_dir = paths.capture_dir(name)

    try:
        profile = merge_settings(load_camera_profile(camera_cfg), settings_override)
        device = build_camera(camera_cfg, profile=profile)
    except Exception as exc:  # unknown driver / bad config — record and keep going
        logger.error("camera %s: could not build adapter: %s", name, exc)
        result = _synth_failed_result(name, camera_cfg, "camera_init_failed", str(exc))
        write_capture_metadata(cap_dir / "capture.json", result)
        return CameraOutcome(camera_name=name, result=result)

    # Stale-state hygiene (CLAUDE.md §10 — one variable at a time): boards stay powered
    # between experiments and firmware AWB state can survive the per-capture
    # ``sensor.reset()`` (AE3 green cast after lights-off runs, 2026-07-16). When the
    # camera profile asks for it, hard-reset the board so this experiment starts from
    # fresh firmware state.
    _reset_if_asked(name, device, profile, "capture")

    # Handshake first so the recorded identity carries the device-reported board +
    # firmware (Spec §5 requires firmware in every experiment record; §12 identity).
    # Best-effort: a board that can't answer info can still attempt a capture.
    try:
        device.get_device_info()
    except Exception as exc:  # non-fatal: capture still proceeds
        logger.debug("camera %s: get_device_info failed (non-fatal): %s", name, exc)

    image_name = naming.capture_filename(name, "image", "jpg", when)
    dest = cap_dir / image_name
    request = CaptureRequest(kind="image", settings=dict(profile))

    logger.info("camera %s: capturing -> %s", name, dest)
    raw_result = None
    sweep_frames: list = []
    want_raw = profile.get("raw", False) if raw is None else raw
    try:
        # Adapters return failed results rather than raise; this guard keeps one that does
        # anyway (an unmapped transport error) from aborting every later camera — Spec §11.
        try:
            result = device.capture_image(str(dest), request)
        except Exception as exc:
            logger.exception("camera %s: adapter raised during capture", name)
            result = _synth_failed_result(name, camera_cfg, "adapter_exception", repr(exc))
        if want_raw and sweep:
            sweep_frames = _capture_sweep(name, device, profile, cap_dir, when, sweep, result)
        elif want_raw:
            try:
                raw_result = _capture_raw(name, device, profile, cap_dir, when)
            except Exception as exc:
                logger.exception("camera %s: adapter raised during RAW capture", name)
                raw_result = _synth_failed_result(name, camera_cfg, "adapter_exception",
                                                  repr(exc))
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
        raw_result=raw_result,
        sweep=sweep_frames,
    )
    for i, (_, r) in enumerate(sweep_frames):
        write_capture_metadata(cap_dir / f"raw_sweep{i:02d}.json", r)
    if raw_result is not None:
        write_capture_metadata(cap_dir / "raw_capture.json", raw_result)
        if raw_result.ok:
            logger.info("camera %s: RAW %s %s bytes sha256=%s", name,
                        raw_result.image_format, raw_result.size_bytes, raw_result.sha256)
        else:
            err = raw_result.error or {}
            logger.error("camera %s: RAW capture FAILED %s: %s", name, err.get("code"),
                         err.get("message"))

    if not result.ok:
        err = result.error or {}
        logger.error("camera %s: capture FAILED %s: %s", name, err.get("code"), err.get("message"))
        return outcome

    logger.info(
        "camera %s: captured %sx%s %s bytes sha256=%s",
        name, result.width, result.height, result.size_bytes, result.sha256,
    )

    if analyzer is not None and result.output_path:
        analysis_cfg, analyze_fn = analyzer
        adir = paths.analysis_dir(name)
        det = analyze_fn(result.output_path, adir, analysis_cfg)
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
    raw: Optional[bool] = None,
    exposure_lock: Optional[dict[str, Any]] = None,
    depth_sensor=None,
    exposure_sweep: Optional[dict[str, Any]] = None,
) -> ExperimentOutcome:
    """Run one sequential capture set across all connected cameras (Spec §11, §13).

    Returns an ``ExperimentOutcome``; a disconnected/failing camera yields a
    ``partial`` status with its slot marked failed in ``experiment.json`` while every
    other camera's raw + analysis artifacts are retained.

    ``raw``: take a RAW after each still (Phase 8 S3); ``None`` = each camera profile's
    ``raw`` flag (default off, so the Phase 5 path is unchanged). A failed RAW is recorded in
    ``raw_captures`` + ``errors`` but does not fail the camera's still.

    ``exposure_lock`` (pool spec §5.2, ``capture.exposure_lock``): each locked camera's settings
    are merged into its profile; the lock is copied into ``experiment.json``. ``depth_sensor``
    (``sensors.depth``, §5.1): read at the start and end of the set into ``record.sensors``.
    ``exposure_sweep`` (pool tool, off by default; ``{"enabled": True, "shutters_us": [...]}``
    or a camera profile's ``exposure_sweep`` block): with ``raw``, each camera takes N RAWs at
    a shutter ladder with gain at its floor instead of the single RAW; every frame is kept,
    scored, and one is picked (``record.exposure_sweeps``).
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

    analyzer = None
    if analysis:
        analyzer = _load_analyzer(config.get("analysis"))
        if analyzer is None:
            msg = "analysis unavailable (install the 'analysis' extra); captures only"
            logger.warning(msg)
            record.warnings.append(msg)

    logger.info(
        "experiment %s: env=%r cameras=%s analysis=%s",
        paths.experiment_id, environment_label, ordered, analyzer is not None,
    )

    overrides: dict[str, Any] = {}
    if exposure_lock:
        overrides = overrides_from_lock(exposure_lock)
        record.exposure_lock = exposure_lock
        unlocked = [n for n in ordered if n not in overrides]
        logger.info("exposure lock %s: locked %s", exposure_lock.get("folder"), sorted(overrides))
        if unlocked:
            record.warnings.append(f"exposure lock: not locked (auto exposure): {unlocked}")
    if depth_sensor is not None:
        record.sensors["depth_info"] = depth_sensor.info()
        record.sensors["depth_start"] = safe_read(depth_sensor)

    outcomes: list[CameraOutcome] = []
    sweeps: dict[str, dict] = {}
    for name in ordered:
        try:
            prof = load_camera_profile(cameras_cfg[name])
        except Exception:  # _capture_one_camera records the init failure
            prof = {}
        sweep = sweep_settings(prof, exposure_sweep, name)
        if sweep:
            sweeps[name] = sweep
        outcome = _capture_one_camera(name, cameras_cfg[name], paths, analyzer, when, raw,
                                      overrides.get(name), sweep)
        outcomes.append(outcome)
        record.cameras.append(outcome.result.camera)
        record.captures.append(outcome.result)
        if outcome.analysis is not None:
            record.analyses.append(outcome.analysis)
        if not outcome.ok:
            err = outcome.result.error or {}
            record.errors.append(f"{name}: {err.get('code')}: {err.get('message')}")
        for _, r in outcome.sweep:
            record.raw_captures.append(r)
            if not r.ok:
                err = r.error or {}
                record.errors.append(f"{name} sweep: {err.get('code')}: {err.get('message')}")
        if outcome.raw_result is not None:
            record.raw_captures.append(outcome.raw_result)
            if not outcome.raw_result.ok:
                err = outcome.raw_result.error or {}
                record.errors.append(f"{name} raw: {err.get('code')}: {err.get('message')}")

    if depth_sensor is not None:
        record.sensors["depth_end"] = safe_read(depth_sensor)
    if sweeps:
        _score_sweeps(record, outcomes, sweeps)

    store.write_record(paths, record)

    result = ExperimentOutcome(record=record, paths=paths, camera_outcomes=outcomes)
    logger.info("experiment %s: status=%s", paths.experiment_id, result.status)
    return result
