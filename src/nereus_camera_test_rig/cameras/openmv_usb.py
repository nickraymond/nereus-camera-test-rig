"""OpenMV USB camera host adapter (N6 / AE3) — Spec §8, §10.

Host-side ``CameraDevice`` that drives an OpenMV board over its USB CDC serial using the
newline-delimited JSON protocol in ``openmv/common/command_protocol.py`` (the single
shared source of truth). Capture flow (§8, §10, §19):

1. ``capture_image`` -> board snapshots to its flash, returns metadata incl. SHA-256;
2. ``get_file`` -> board streams the bytes length-framed; the host writes them to the
   destination and **verifies the SHA-256 and byte count** before reporting success.

The board is addressed by USB **serial number** (§12), resolved to a live device path by
``host_tools.discover_openmv`` — never a hardcoded ``/dev/ttyACM*``. Board-specific
behaviour stays on the board (its ``board_config``); this adapter is board-agnostic and
carries no ``if board == "n6"`` branching (§4, §6).

Testability: all I/O goes through a small transport with ``read(n)``/``write(bytes)``, so
a fake loopback exercises the whole adapter on the host with no hardware.
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from ..models import CameraIdentity, CaptureRequest, CaptureResult
from ..storage.checksums import sha256_file
from .base import CameraDevice

# Shared protocol codec. openmv/ is not part of the src wheel (this rig runs from a
# checkout), so add the repo root to sys.path via __file__ rather than trusting CWD.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from openmv.common import command_protocol as cp  # noqa: E402

DEFAULT_BAUD = 115200
# Quick commands (device info) vs. capture (sensor reset + warm-up + snapshot + save +
# on-board SHA-256 of a full-res JPEG can take several seconds).
DEFAULT_TIMEOUT = 5.0
CAPTURE_TIMEOUT = 30.0
TRANSFER_TIMEOUT = 30.0
# Hard reset + USB re-enumeration + service boot measured ~3.5 s on the AE3; allow slack.
RESET_TIMEOUT = 20.0
_READ_CHUNK = 4096

logger = logging.getLogger(__name__)


class OpenMvError(RuntimeError):
    """A structured failure reported by the board or the transport."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class OpenMvTimeout(OpenMvError):
    def __init__(self, message: str):
        super().__init__("timeout", message)


class _SerialIO:
    """Buffered line/binary reader over a ``read(n)``/``write(bytes)`` transport.

    Keeps a single receive buffer so alternating ``read_line`` (JSON) and ``read_exact``
    (framed binary) never lose bytes across the JSON/binary boundary (§10).
    """

    def __init__(self, transport: Any, default_timeout: float = DEFAULT_TIMEOUT):
        self._t = transport
        self._default_timeout = default_timeout
        self._buf = bytearray()

    def write_message(self, message: dict) -> None:
        self._t.write(cp.encode_message(message))

    def _read_some(self) -> bytes:
        return self._t.read(_READ_CHUNK) or b""

    def read_line(self, timeout: Optional[float] = None) -> bytes:
        deadline = time.monotonic() + (self._default_timeout if timeout is None else timeout)
        while b"\n" not in self._buf:
            chunk = self._read_some()
            if chunk:
                self._buf += chunk
            elif time.monotonic() > deadline:
                raise OpenMvTimeout("timed out waiting for a response line")
        idx = self._buf.index(b"\n")
        line = bytes(self._buf[:idx])
        del self._buf[: idx + 1]
        return line

    def read_exact(self, n: int, timeout: Optional[float] = None) -> bytes:
        deadline = time.monotonic() + (self._default_timeout if timeout is None else timeout)
        while len(self._buf) < n:
            chunk = self._read_some()
            if chunk:
                self._buf += chunk
            elif time.monotonic() > deadline:
                raise OpenMvTimeout(
                    "timed out reading %d bytes (got %d)" % (n, len(self._buf))
                )
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


class OpenMvUsbCamera(CameraDevice):
    """Host adapter driving an OpenMV board over USB serial (N6 today, AE3 in Phase 4)."""

    driver = "openmv_usb"

    def __init__(
        self,
        serial_number: Optional[str] = None,
        *,
        port: Optional[str] = None,
        board: Optional[str] = None,
        settings: Optional[dict[str, Any]] = None,
        transport: Any = None,
        baudrate: int = DEFAULT_BAUD,
    ):
        self._serial_number = serial_number
        self._port = port
        self._board = board
        self._settings: dict[str, Any] = dict(settings or {})
        self._baudrate = baudrate
        self._transport = transport  # injected (tests) or opened lazily
        self._owns_transport = transport is None
        self._io: Optional[_SerialIO] = None
        self._identity: Optional[CameraIdentity] = None

    # -- transport lifecycle -------------------------------------------------
    def _ensure_io(self) -> _SerialIO:
        if self._io is not None:
            return self._io
        if self._transport is None:
            self._transport = self._open_serial()
        self._io = _SerialIO(self._transport)
        return self._io

    def _open_serial(self):
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise OpenMvError(
                "serial_unavailable",
                "pyserial not installed; install the 'serial' extra (pip install -e '.[serial]')",
            ) from exc
        port = self._port or self._resolve_port()
        if not port:
            raise OpenMvError(
                "device_not_found",
                "no OpenMV board for serial_number=%r" % self._serial_number,
            )
        self._port = port
        # Short per-read timeout; overall deadlines are enforced in _SerialIO.
        return serial.Serial(port, self._baudrate, timeout=0.2)

    def _resolve_port(self) -> Optional[str]:
        from host_tools.discover_openmv import find_port

        return find_port(self._serial_number)

    def close(self) -> None:
        if self._transport is not None and self._owns_transport:
            try:
                self._transport.close()
            except Exception:
                pass
        self._transport = None
        self._io = None

    def __enter__(self) -> "OpenMvUsbCamera":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- protocol primitives -------------------------------------------------
    def _command(
        self, action: str, settings: Optional[dict] = None, timeout: Optional[float] = None
    ) -> dict:
        """Send a command and return the single JSON response, raising on failure."""
        io = self._ensure_io()
        command_id = uuid.uuid4().hex[:12]
        io.write_message(cp.make_request(action, command_id, settings))
        resp = cp.decode_message(io.read_line(timeout=timeout))
        if resp.get("status") == "failed":
            err = resp.get("error") or {}
            raise OpenMvError(
                err.get("code", "unknown"), err.get("message", "board reported failure")
            )
        return resp

    # -- CameraDevice interface ---------------------------------------------
    def get_device_info(self) -> dict[str, Any]:
        resp = self._command("get_device_info")
        info = resp.get("output") or {}
        self._identity = CameraIdentity(
            driver=self.driver,
            platform="openmv",
            device_id=info.get("device_id"),
            board=info.get("board") or self._board,
            sensor=info.get("sensor"),
            firmware=info.get("firmware"),
            serial_number=self._serial_number,
        )
        return info

    def configure(self, settings: dict[str, Any]) -> None:
        self._settings.update(settings or {})

    def capture_image(self, destination: str, request: CaptureRequest) -> CaptureResult:
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        identity = self._current_identity()
        started = time.monotonic()
        output: dict[str, Any] = {}
        try:
            settings = {**self._settings, **(request.settings or {})}
            # The host owns the filename (it owns real time); the board saves under it.
            settings["filename"] = dest.name
            cap = self._command("capture_image", settings, timeout=CAPTURE_TIMEOUT)
            output = cap.get("output") or {}
            self._retrieve_file(output.get("filename", dest.name), dest, output)
        except OpenMvError as exc:
            elapsed = time.monotonic() - started
            return self._failed(identity, request, exc.code, exc.message, elapsed)

        return self._validate(identity, request, dest, output, time.monotonic() - started)

    def capture_video(self, destination: str, request: CaptureRequest) -> CaptureResult:
        # OQ-4: short-clip-to-file on the N6 is not yet verified. Live focus streaming is
        # provided separately (host focus stream). Report unsupported, not a crash.
        identity = self._current_identity()
        return self._failed(
            identity, request, "not_supported",
            "capture_video not implemented for OpenMV yet (OQ-4)", 0.0,
        )

    def reset_board(self, timeout: float = RESET_TIMEOUT) -> dict[str, Any]:
        """Hard-reset the board and wait until its capture service answers again.

        Clears firmware 3A state that survives the board's per-capture ``sensor.reset()``
        (stale AWB observed on the AE3 after lights-off runs, 2026-07-16 — only a hard
        reset recovered neutral color). The board acks, then ``machine.reset()``s; the
        USB CDC device drops and re-enumerates, possibly under a *different*
        ``/dev/ttyACM*`` number, so the port is re-resolved by USB serial (§12).

        Returns ``{"duration_seconds": ..., "info": <device info>}`` on success. Raises
        ``OpenMvError`` if the board rejects the command (e.g. pre-reset_board firmware)
        or does not come back within ``timeout``.
        """
        started = time.monotonic()
        self._command("reset_board")
        if not self._owns_transport:
            # Injected transport (tests/loopback): no real USB to re-enumerate — just
            # re-handshake over the same transport.
            return {
                "duration_seconds": time.monotonic() - started,
                "info": self.get_device_info(),
            }
        deadline = started + timeout
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            self.close()
            self._port = None  # ttyACM numbering can change across re-enumeration
            time.sleep(0.5)
            try:
                return {
                    "duration_seconds": time.monotonic() - started,
                    "info": self.get_device_info(),
                }
            except Exception as exc:  # not back yet (port gone / mid-boot) — retry
                last_error = exc
        self.close()
        raise OpenMvError(
            "reset_timeout",
            "board did not return within %.0fs of reset_board (serial=%r): %s"
            % (timeout, self._serial_number, last_error),
        )

    def health_check(self) -> dict[str, Any]:
        try:
            info = self.get_device_info()
        except OpenMvError as exc:
            return {"driver": self.driver, "healthy": False, "error": exc.message,
                    "serial_number": self._serial_number, "port": self._port}
        return {
            "driver": self.driver,
            "healthy": True,
            "port": self._port,
            "serial_number": self._serial_number,
            "board": info.get("board"),
            "firmware": info.get("firmware"),
            "sensor": info.get("sensor"),
            # A filling /flash is a failure-in-waiting (io_error on capture) — surface it
            # in health checks. None on firmware that doesn't report it.
            "flash_free_bytes": info.get("flash_free_bytes"),
            "flash_total_bytes": info.get("flash_total_bytes"),
        }

    # -- helpers -------------------------------------------------------------
    def _retrieve_file(self, filename: str, dest: Path, expected: dict) -> None:
        """Request a file, read the framed binary, verify size + SHA-256, write to dest."""
        io = self._ensure_io()
        command_id = uuid.uuid4().hex[:12]
        io.write_message(cp.make_request("get_file", command_id, {"filename": filename}))
        header = cp.decode_message(io.read_line(timeout=TRANSFER_TIMEOUT))
        if header.get("status") == "failed":
            err = header.get("error") or {}
            raise OpenMvError(
                err.get("code", "file_not_found"), err.get("message", "get_file failed")
            )
        if header.get("status") != "sending":
            raise OpenMvError("bad_transfer", "unexpected transfer header: %r" % header)
        transfer = header.get("transfer") or {}
        size = int(transfer.get("size_bytes", 0))
        data = io.read_exact(size, timeout=TRANSFER_TIMEOUT)
        footer = cp.decode_message(io.read_line(timeout=TRANSFER_TIMEOUT))
        if footer.get("status") != "completed":
            raise OpenMvError("bad_transfer", "transfer not completed: %r" % footer)

        # Verify the framed payload against the header before trusting it (§19).
        if len(data) != size:
            raise OpenMvError("size_mismatch", "got %d bytes, expected %d" % (len(data), size))
        actual_sha = _sha256_bytes(data)
        expected_sha = transfer.get("sha256")
        if expected_sha and actual_sha != expected_sha:
            raise OpenMvError("checksum_mismatch",
                              "sha256 %s != board %s" % (actual_sha, expected_sha))
        # Cross-check against the capture metadata's own sha where present.
        cap_sha = expected.get("sha256")
        if cap_sha and cap_sha != actual_sha:
            raise OpenMvError("checksum_mismatch",
                              "retrieved sha %s != capture sha %s" % (actual_sha, cap_sha))
        dest.write_bytes(data)
        # The flash copy is only a transfer buffer; the verified copy on disk is the raw
        # evidence (§11). Delete it so captures can't fill /flash (the N6 hit 0 bytes
        # free on 2026-07-17 and every capture failed with io_error). Best-effort: a
        # failed delete (e.g. pre-delete_file firmware) must not fail the capture.
        self._delete_remote_file(filename)

    def _delete_remote_file(self, filename: str) -> None:
        """Best-effort ``delete_file`` after a verified retrieval; warn, never raise."""
        try:
            self._command("delete_file", {"filename": filename})
        except OpenMvError as exc:
            logger.warning(
                "delete_file %r failed on board serial=%r (%s: %s) — flash will "
                "accumulate captures until the board firmware is redeployed",
                filename, self._serial_number, exc.code, exc.message,
            )

    def _validate(
        self, identity, request, dest: Path, output: dict, duration: float
    ) -> CaptureResult:
        # Trust the artifact, not the exit status (CLAUDE.md §19).
        if not dest.is_file() or dest.stat().st_size == 0:
            return self._failed(
                identity, request, "empty_output", "missing/empty file: %s" % dest, duration
            )
        local_sha = sha256_file(dest)
        board_sha = output.get("sha256")
        if board_sha and board_sha != local_sha:
            return self._failed(identity, request, "checksum_mismatch",
                                "on-disk sha %s != board %s" % (local_sha, board_sha), duration)
        return CaptureResult(
            camera=identity,
            request=request,
            status="completed",
            output_path=str(dest),
            width=output.get("width"),
            height=output.get("height"),
            image_format=output.get("format", "jpeg"),
            size_bytes=dest.stat().st_size,
            sha256=local_sha,
            duration_seconds=duration,
            sensor_metadata={
                "framesize": output.get("framesize"),
                "pixel_format": output.get("pixel_format"),
                "jpeg_quality": output.get("jpeg_quality"),
                "mount_rotation_deg": output.get("mount_rotation_deg"),
                # Settled 3A state at snapshot time (§12): a normal scene captured at
                # extreme exposure/gain is the stale-sensor-state signature.
                "exposure_us": output.get("exposure_us"),
                "gain_db": output.get("gain_db"),
            },
        )

    def _current_identity(self) -> CameraIdentity:
        if self._identity is not None:
            return self._identity
        return CameraIdentity(
            driver=self.driver, platform="openmv", board=self._board,
            serial_number=self._serial_number,
        )

    def _failed(self, identity, request, code, message, duration) -> CaptureResult:
        return CaptureResult(
            camera=identity,
            request=request,
            status="failed",
            duration_seconds=duration,
            error={"code": code, "message": message},
        )


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()
