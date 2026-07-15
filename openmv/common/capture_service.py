"""OpenMV capture + file-transfer service — Spec §8, §10.

Shared board logic (both N6 and AE3 reuse it): configure the sensor from a validated
settings dict, snapshot to on-board storage, and stream the file back over USB with the
framed transfer from §10. Board specifics (valid framesizes, defaults, sensor mount)
come in via ``board_config`` so nothing here branches on board type (CLAUDE.md §6/§36).

Transfer method (OQ-3, verified 2026-07-14): capture to ``/flash`` then send the bytes
over the same CDC serial, length-framed — no USB mass-storage mounting (avoids the
concurrent-FS-access corruption class). SHA-256 is computed on-board so the host can
verify the round-trip (§10, §19: trust artifacts, not exit codes).

Runs on the board under MicroPython. Uses the legacy ``sensor`` API (verified present
and sufficient on OPENMV_N6 firmware 1.26.0).
"""

import binascii
import hashlib
import os

import command_protocol as cp
import sensor

STORAGE_DIR = "/flash"
_CHUNK = 512  # small allocations for MicroPython (CLAUDE.md §23)

# Filename charset allowed for on-board files. The host controls the name (it owns real
# time for timestamps); the board sanitizes to a basename in this set so a request can
# never traverse out of STORAGE_DIR.
_SAFE_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"


def _safe_basename(name):
    """Reduce ``name`` to a safe basename inside STORAGE_DIR, or raise ProtocolError."""
    if not name:
        name = "capture.jpg"
    name = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c in _SAFE_CHARS)
    if not name or name in (".", ".."):
        raise cp.ProtocolError(cp.ERR_MISSING_FIELD, "invalid filename")
    return name


def _resolve_framesize(board_config, settings):
    fs = settings.get("framesize", board_config.DEFAULT_FRAMESIZE)
    attr = board_config.FRAMESIZES.get(fs)
    if attr is None:
        raise cp.ProtocolError(
            cp.ERR_CAPTURE_FAILED, "unsupported framesize: " + repr(fs)
        )
    return getattr(sensor, attr), fs


def _resolve_pixformat(board_config, settings):
    pf = str(settings.get("pixel_format", board_config.DEFAULT_PIXEL_FORMAT)).upper()
    attr = board_config.PIXEL_FORMATS.get(pf)
    if attr is None:
        raise cp.ProtocolError(
            cp.ERR_CAPTURE_FAILED, "unsupported pixel_format: " + repr(pf)
        )
    return getattr(sensor, attr), pf


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return binascii.hexlify(h.digest()).decode()


def capture_image(board_config, settings):
    """Configure the sensor, snapshot to STORAGE_DIR, return output metadata (§8).

    Validates framesize/pixel_format against the board allowlist; raises ProtocolError
    (mapped to a structured failure by the caller) on bad settings or capture error.
    """
    name = _safe_basename(settings.get("filename"))
    fs_const, fs_name = _resolve_framesize(board_config, settings)
    pf_const, pf_name = _resolve_pixformat(board_config, settings)
    quality = int(settings.get("jpeg_quality", board_config.DEFAULT_JPEG_QUALITY))
    warmup_ms = int(settings.get("warmup_ms", board_config.DEFAULT_WARMUP_MS))

    sensor.reset()
    sensor.set_pixformat(pf_const)
    sensor.set_framesize(fs_const)
    if warmup_ms > 0:
        sensor.skip_frames(time=warmup_ms)
    img = sensor.snapshot()

    path = STORAGE_DIR + "/" + name
    img.save(path, quality=quality)
    size = os.stat(path)[6]
    return {
        "filename": name,
        "width": img.width(),
        "height": img.height(),
        "format": "jpeg",
        "pixel_format": pf_name,
        "framesize": fs_name,
        "jpeg_quality": quality,
        "size_bytes": size,
        "sha256": _sha256_file(path),
        "mount_rotation_deg": board_config.MOUNT_ROTATION_DEG,
    }


def send_file(usb, command_id, filename):
    """Stream a stored file to the host, length-framed per §10.

    Wire sequence: ``sending`` header line (with size + sha256) -> exactly size_bytes
    raw bytes -> ``completed`` line. On a missing file, a single ``failed`` line is sent
    and no binary follows, so the host never mis-frames.
    """
    name = _safe_basename(filename)
    path = STORAGE_DIR + "/" + name
    try:
        size = os.stat(path)[6]
    except OSError:
        usb.write(cp.encode_message(
            cp.failed_response(command_id, cp.ERR_FILE_NOT_FOUND, "no such file: " + name)
        ))
        return
    sha = _sha256_file(path)
    usb.write(cp.encode_message(cp.sending_response(command_id, name, size, sha)))
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            usb.write(chunk)
    usb.write(cp.encode_message(
        cp.completed_response(command_id, {"filename": name, "size_bytes": size, "sha256": sha})
    ))
