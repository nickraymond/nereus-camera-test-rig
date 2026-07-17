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

Stale-3A-state hazard (observed on hardware 2026-07-16): the boards stay powered between
experiments, and firmware auto-white-balance state can outlive ``sensor.reset()``. After
lights-off runs the AE3 (fw 1.25.0-preview) produced a strong green cast on *every* later
capture — its firmware exposes no AWB control (``set_auto_whitebal`` /
``get_rgb_gain_db`` report "not supported" for the PAG7936) so nothing callable from
MicroPython could clear it; only a hard MCU reset did. The N6 (fw 1.26.0) exposes working
AWB gains and reconverges per frame, so it recovered on its own. Hence ``reset_board``
below: an allowlisted command that acks then ``machine.reset()``s — the coordinator sends
it before capture so one extreme-lighting run can't poison the next (CLAUDE.md §10, one
variable at a time). ``capture_image`` additionally reports the settled exposure/gain so
a poisoned capture is diagnosable from its metadata alone (§12).
"""

import binascii
import hashlib
import os
import time

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


def _resolve_framesize(board_config, settings, default=None):
    fs = settings.get("framesize", default or board_config.DEFAULT_FRAMESIZE)
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
    out = {
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
    # Settled 3A state at snapshot time (§12 exposure settings where available). Makes a
    # stuck auto-exposure/AWB episode diagnosable from capture.json alone: a "normal"
    # scene captured at extreme exposure/gain is the stale-state signature. Capability-
    # probed, not board-branched — unsupported controls just omit the fields.
    try:
        out["exposure_us"] = sensor.get_exposure_us()
        out["gain_db"] = sensor.get_gain_db()
    except Exception:
        pass
    return out


def reset_board(usb, command_id):
    """Ack the command, then hard-reset the MCU (never returns).

    ``machine.reset()`` is the verified remedy for firmware 3A state that survives
    ``sensor.reset()`` (the manual ``mpremote reset`` that cleared the AE3's stuck-AWB
    green cast on 2026-07-16 — see module docstring). The ack is written first and given
    a moment to drain over USB CDC so the host sees a structured completion before the
    port drops and re-enumerates (~3.5 s to service-ready, measured on the AE3).
    """
    usb.write(cp.encode_message(
        cp.completed_response(command_id, {"resetting": True})
    ))
    time.sleep_ms(250)  # let the CDC buffer flush before the port disappears
    import machine

    machine.reset()


def delete_file(filename):
    """Delete one stored file from STORAGE_DIR, returning output metadata.

    The flash copy is only a transfer buffer — the authoritative raw evidence is the
    checksum-verified copy on the Pi (§10, §11 raw-data rule). Without cleanup every
    uniquely-named capture accumulates until the filesystem is full (the N6 hit 0 bytes
    free on 2026-07-17 and every capture failed with io_error "Write failed"). Same
    ``_safe_basename`` guard as ``get_file`` so a request can never reach outside
    STORAGE_DIR. A missing file is a structured ``file_not_found`` failure — the host
    treats deletion as best-effort and never fails a capture over it.
    """
    name = _safe_basename(filename)
    path = STORAGE_DIR + "/" + name
    try:
        size = os.stat(path)[6]
        os.remove(path)
    except OSError:
        raise cp.ProtocolError(cp.ERR_FILE_NOT_FOUND, "no such file: " + name)
    return {"filename": name, "deleted": True, "size_bytes": size}


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


def stream_frames(usb, command_id, board_config, settings):
    """Continuously stream framed JPEG frames for manual lens focusing (§2 "where practical").

    Each frame: a ``frame`` header line (seq, size, sharpness proxy, dims) then exactly
    ``size_bytes`` of JPEG. Sharpness is the JPEG byte count at fixed quality — it rises
    as the lens comes into focus on a fixed scene. Ends on any inbound byte from the host
    (the stop signal) or a safety timeout, then sends a single ``completed`` line.

    Not request/response: the board stays in this loop, so no other command is served
    until the stream stops. The host owns the port exclusively while streaming.
    """
    fs_const, _fs_name = _resolve_framesize(
        board_config, settings, default=board_config.STREAM_DEFAULT_FRAMESIZE
    )
    quality = int(settings.get("jpeg_quality", board_config.STREAM_DEFAULT_QUALITY))
    max_seconds = int(settings.get("max_seconds", board_config.STREAM_MAX_SECONDS))

    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(fs_const)
    sensor.skip_frames(time=300)

    seq = 0
    deadline = time.ticks_add(time.ticks_ms(), max_seconds * 1000)
    while True:
        # Any inbound byte is the host's stop signal; drain it and exit.
        if usb.any():
            try:
                usb.read(usb.any())
            except Exception:
                pass
            break
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            break
        img = sensor.snapshot()
        img.compress(quality=quality)
        data = img.bytearray()
        n = len(data)
        usb.write(cp.encode_message(
            cp.frame_response(command_id, seq, n, n, img.width(), img.height())
        ))
        usb.write(data)
        seq += 1

    usb.write(cp.encode_message(cp.completed_response(command_id, {"frames": seq})))
