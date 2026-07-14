"""IMX708 (Raspberry Pi CSI) camera adapter — Spec §9.

Adapts the proven rpicam capture path from bm_cam_legacy's ``process_image_v2.py``
(camera-binary selection, control-flag builder, ``--metadata`` parsing, timeout)
into the ``CameraDevice`` interface, with all Bristlemouth/Spotter telemetry and
hardcoded ``/home/pi`` paths stripped. Prefers ``rpicam-still``/``rpicam-vid``,
falls back to the ``libcamera-*`` names (Spec §9).

Defaults are full-auto exposure/white-balance/focus: when no control values are
given, no control flags are passed and libcamera picks them (the legacy default
path; see OQ-10). The camera's chosen values are recorded from ``--metadata``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from ..capture.image_probe import ImageProbeError, jpeg_dimensions
from ..models import CameraIdentity, CaptureRequest, CaptureResult
from ..storage.checksums import sha256_file
from .base import CameraDevice

DEFAULT_WIDTH = 4608
DEFAULT_HEIGHT = 2592
DEFAULT_JPEG_QUALITY = 95
DEFAULT_WARMUP_MS = 2000
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_VIDEO_SECONDS = 5.0

# Sensor metadata fields worth recording from libcamera --metadata (Spec §5).
_METADATA_FIELDS = (
    "ExposureTime", "AnalogueGain", "DigitalGain", "ColourGains",
    "ColourTemperature", "LensPosition", "AfState", "AfMode", "FocusFoM",
    "Lux", "FrameDuration", "SensorTemperature",
)


class CaptureError(RuntimeError):
    """Raised on an unrecoverable capture failure (surfaced as a failed result)."""


def select_command(kind: str, override: Optional[str] = None) -> tuple[str, str]:
    """Resolve the rpicam/libcamera binary for ``kind`` ("image"|"video").

    Returns ``(path, backend)`` where backend is "rpicam" or "libcamera". Raises
    ``CaptureError`` if neither is on PATH. ``override`` forces a specific binary.
    """
    if override:
        path = shutil.which(override) or override
        backend = "rpicam" if "rpicam" in override else "libcamera"
        return path, backend
    suffix = "still" if kind == "image" else "vid"
    for prefix, backend in (("rpicam", "rpicam"), ("libcamera", "libcamera")):
        found = shutil.which(f"{prefix}-{suffix}")
        if found:
            return found, backend
    raise CaptureError(
        f"no camera command found for {kind!r}: expected rpicam-{suffix} or libcamera-{suffix}"
    )


def _setting(source: dict, settings: dict, key: str, default):
    """Look up ``key`` in the source sub-dict, then top-level settings, then default."""
    return source.get(key, settings.get(key, default))


def _control_args(controls: dict[str, Any]) -> list[str]:
    """Build optional libcamera control flags. Empty list == full auto (OQ-10).

    Only emits a flag when a concrete value is supplied; anything left as None means
    "let the camera decide". Mirrors the legacy control-flag surface (Spec §9).
    """
    args: list[str] = []
    if not isinstance(controls, dict):
        return args

    focus = controls.get("focus") or {}
    if focus.get("mode"):
        args += ["--autofocus-mode", str(focus["mode"])]
    if focus.get("lens_position") is not None:
        args += ["--lens-position", f"{float(focus['lens_position']):.4f}"]

    wb = controls.get("white_balance") or {}
    red, blue = wb.get("red_gain"), wb.get("blue_gain")
    if red is not None and blue is not None:
        args += ["--awb", "custom", "--awbgains", f"{float(red):.4f},{float(blue):.4f}"]
    elif wb.get("mode"):
        args += ["--awb", str(wb["mode"])]

    exposure = controls.get("exposure") or {}
    if exposure.get("shutter_us") is not None:
        args += ["--shutter", str(int(exposure["shutter_us"]))]
    if exposure.get("analogue_gain") is not None:
        args += ["--gain", f"{float(exposure['analogue_gain']):.4f}"]

    return args


class Imx708Camera(CameraDevice):
    """Raspberry Pi IMX708 adapter over the rpicam CLI."""

    driver = "imx708"

    def __init__(
        self,
        settings: Optional[dict[str, Any]] = None,
        *,
        still_command: Optional[str] = None,
        video_command: Optional[str] = None,
        runner=subprocess.run,
        clock=time.monotonic,
    ):
        self._settings: dict[str, Any] = dict(settings or {})
        self._still_command = still_command
        self._video_command = video_command
        self._runner = runner  # injectable for host unit tests
        self._clock = clock

    # -- interface -----------------------------------------------------------
    def get_device_info(self) -> dict[str, Any]:
        path, backend = select_command("image", self._still_command)
        info = {
            "driver": self.driver,
            "platform": "raspberry_pi",
            "sensor": "imx708",
            "command": path,
            "backend": backend,
        }
        listing = self._list_cameras(path)
        if listing:
            info["cameras"] = listing
        return info

    def configure(self, settings: dict[str, Any]) -> None:
        self._settings.update(settings or {})

    def capture_image(self, destination: str, request: CaptureRequest) -> CaptureResult:
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        meta_path = dest.with_suffix(dest.suffix + ".rpicam.json")
        camera = CameraIdentity(driver=self.driver, platform="raspberry_pi", sensor="imx708")
        started = self._clock()
        # select_command + run are both inside the guard so a missing binary yields a
        # failed result (fail loudly, not a traceback — CLAUDE.md §17).
        try:
            path, _backend = select_command("image", self._still_command)
            settings = {**self._settings, **(request.settings or {})}
            source = settings.get("source") or {}
            width = int(_setting(source, settings, "width", DEFAULT_WIDTH))
            height = int(_setting(source, settings, "height", DEFAULT_HEIGHT))
            quality = int(_setting(source, settings, "jpeg_quality", DEFAULT_JPEG_QUALITY))
            warmup = int(settings.get("warmup_ms", DEFAULT_WARMUP_MS))
            timeout = float(
                request.timeout_seconds or settings.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
            )
            cmd = [
                path, "-n",
                "-t", str(warmup),
                "--width", str(width),
                "--height", str(height),
                "-q", str(quality),
                "--metadata", str(meta_path),
                "--metadata-format", "json",
            ]
            cmd += _control_args(settings.get("camera_controls") or {})
            cmd += ["-o", str(dest)]
            self._run(cmd, timeout)
        except CaptureError as exc:
            elapsed = self._clock() - started
            return self._failed(camera, request, "capture_failed", str(exc), elapsed)

        return self._validate_still(camera, request, dest, meta_path, self._clock() - started)

    def capture_video(self, destination: str, request: CaptureRequest) -> CaptureResult:
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        camera = CameraIdentity(driver=self.driver, platform="raspberry_pi", sensor="imx708")
        started = self._clock()
        try:
            path, _backend = select_command("video", self._video_command)
            settings = {**self._settings, **(request.settings or {})}
            source = settings.get("source") or {}
            width = int(_setting(source, settings, "width", DEFAULT_WIDTH))
            height = int(_setting(source, settings, "height", DEFAULT_HEIGHT))
            seconds = float(settings.get("duration_seconds", DEFAULT_VIDEO_SECONDS))
            duration_ms = int(seconds * 1000)
            timeout = float(request.timeout_seconds or seconds + DEFAULT_TIMEOUT_SECONDS)
            cmd = [
                path, "-n",
                "-t", str(duration_ms),
                "--width", str(width),
                "--height", str(height),
                "-o", str(dest),
            ]
            self._run(cmd, timeout)
        except CaptureError as exc:
            elapsed = self._clock() - started
            return self._failed(camera, request, "capture_failed", str(exc), elapsed)

        return self._validate_video(camera, request, dest, self._clock() - started)

    def health_check(self) -> dict[str, Any]:
        try:
            path, backend = select_command("image", self._still_command)
        except CaptureError as exc:
            return {"driver": self.driver, "healthy": False, "error": str(exc)}
        cameras = self._list_cameras(path)
        return {
            "driver": self.driver,
            "healthy": bool(cameras),
            "command": path,
            "backend": backend,
            "cameras_detected": len(cameras) if cameras else 0,
        }

    # -- helpers -------------------------------------------------------------
    def _run(self, cmd: list[str], timeout: float) -> None:
        joined = " ".join(cmd)
        try:
            result = self._runner(cmd, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise CaptureError(f"camera command timed out after {timeout}s: {joined}") from exc
        except FileNotFoundError as exc:
            raise CaptureError(f"camera command not found: {cmd[0]}") from exc
        rc = getattr(result, "returncode", 1)
        if rc != 0:
            stderr = getattr(result, "stderr", b"") or b""
            tail = " | ".join(stderr.decode("utf-8", "replace").strip().splitlines()[-3:])
            raise CaptureError(f"camera command exited {rc}: {joined} :: {tail}")

    def _list_cameras(self, path: str) -> list[str]:
        cmd = [path, "--list-cameras"]
        try:
            res = self._runner(cmd, capture_output=True, timeout=15, check=False)
        except Exception:
            return []
        out = (getattr(res, "stdout", b"") or b"").decode("utf-8", "replace")
        return [ln.strip() for ln in out.splitlines() if "imx" in ln.lower()]

    def _parse_metadata(self, meta_path: Path) -> dict[str, Any]:
        if not meta_path.is_file():
            return {}
        try:
            data = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        if isinstance(data, list):
            data = next((d for d in reversed(data) if isinstance(d, dict)), {})
        if not isinstance(data, dict):
            return {}
        return {k: data[k] for k in _METADATA_FIELDS if k in data}

    def _validate_still(self, camera, request, dest, meta_path, duration) -> CaptureResult:
        # Trust artifacts, not exit codes (CLAUDE.md §19).
        if not dest.is_file() or dest.stat().st_size == 0:
            msg = f"missing/empty file: {dest}"
            return self._failed(camera, request, "empty_output", msg, duration)
        try:
            width, height = jpeg_dimensions(dest)
        except ImageProbeError as exc:
            return self._failed(camera, request, "corrupt_output", str(exc), duration)

        sensor_meta = self._parse_metadata(meta_path)
        return CaptureResult(
            camera=camera,
            request=request,
            status="completed",
            output_path=str(dest),
            width=width,
            height=height,
            image_format="jpeg",
            size_bytes=dest.stat().st_size,
            sha256=sha256_file(dest),
            duration_seconds=duration,
            sensor_metadata=sensor_meta,
        )

    def _validate_video(self, camera, request, dest, duration) -> CaptureResult:
        if not dest.is_file() or dest.stat().st_size == 0:
            msg = f"missing/empty file: {dest}"
            return self._failed(camera, request, "empty_output", msg, duration)
        return CaptureResult(
            camera=camera,
            request=request,
            status="completed",
            output_path=str(dest),
            image_format=dest.suffix.lstrip(".") or "h264",
            size_bytes=dest.stat().st_size,
            sha256=sha256_file(dest),
            duration_seconds=duration,
        )

    def _failed(self, camera, request, code, message, duration) -> CaptureResult:
        return CaptureResult(
            camera=camera,
            request=request,
            status="failed",
            duration_seconds=duration,
            error={"code": code, "message": message},
        )
