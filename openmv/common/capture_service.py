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
_RAW_CHUNK = 4096  # memoryview slices of the frame buffer: no copy, no allocation per chunk

# Filename charset allowed for on-board files. The host controls the name (it owns real
# time for timestamps); the board sanitizes to a basename in this set so a request can
# never traverse out of STORAGE_DIR.
_SAFE_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"

# Stream JPEG chroma subsampling: wire name -> ``image`` module constant (OpenMV v5.0.1,
# modules/py_image.c). "auto" passes nothing, i.e. the firmware default.
_SUBSAMPLING = {"auto": None, "420": "JPEG_SUBSAMPLING_420", "422": "JPEG_SUBSAMPLING_422",
                "444": "JPEG_SUBSAMPLING_444"}


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
    """Allowlisted wire name -> what ``sensor.set_framesize`` takes: a ``sensor`` constant, or a
    (w, h) tuple for a board's custom size (the N6's ISP-scaled 960x600, n6/board_config.py)."""
    fs = settings.get("framesize", default or board_config.DEFAULT_FRAMESIZE)
    attr = board_config.FRAMESIZES.get(fs)
    if attr is None:
        raise cp.ProtocolError(
            cp.ERR_CAPTURE_FAILED, "unsupported framesize: " + repr(fs)
        )
    if isinstance(attr, tuple):
        return attr, fs
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


# Synchronized capture (2026-10-14, Nick: the three cameras must shoot the same moment). After
# warm-up the board says "armed" and keeps snapshotting (autos stay live) until the host's fire
# byte arrives, then takes the next frame. A host that never fires cannot hang the board.
TRIGGER_MAX_WAIT_MS = 20000


def _wait_fire(usb, command_id, snap):
    """Armed loop: returns board ticks of armed / fire receipt + the frame period seen while
    waiting (the fire-to-frame latency is up to about one frame period)."""
    t_armed = time.ticks_ms()
    usb.write(cp.encode_message(cp.armed_response(command_id, t_armed)))
    frames = 0
    while True:
        if usb.any():
            t_fire = time.ticks_ms()
            usb.read(64)  # consume the fire byte(s)
            break
        if time.ticks_diff(time.ticks_ms(), t_armed) > TRIGGER_MAX_WAIT_MS:
            t_fire = None
            break
        snap()
        frames += 1
    waited = time.ticks_diff(time.ticks_ms(), t_armed)
    return {"armed_ms": t_armed, "fire_rx_ms": t_fire, "timed_out": t_fire is None,
            "frame_ms": (waited // frames) if frames else None}


def capture_image(board_config, settings, usb=None, command_id=None):
    """Configure the sensor, snapshot to STORAGE_DIR, return output metadata (§8).

    Validates framesize/pixel_format against the board allowlist; raises ProtocolError
    (mapped to a structured failure by the caller) on bad settings or capture error.
    ``settings["trigger"]``: synchronized capture — after warm-up, wait for the host's fire
    byte (``_wait_fire``; needs ``usb`` + ``command_id``) and report the board ticks.
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
    sync = None
    if settings.get("trigger") and usb is not None:
        sync = _wait_fire(usb, command_id, sensor.snapshot)
    img = sensor.snapshot()
    if sync is not None:
        sync["snap_ms"] = time.ticks_ms()

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
    if sync is not None:
        out["sync"] = sync
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


def _read_3a():
    """Current exposure (us) and gain (dB), or Nones where the firmware can't report them."""
    try:
        return sensor.get_exposure_us(), _finite(sensor.get_gain_db())
    except Exception:
        return None, None


def _lock_3a():
    """Freeze AE, gain and AWB at their settled values (N6 eval test 1, SPEC_n6_eval must do 7).

    Each control is tried on its own and reported, not board-branched: the PAG7936 has no
    AWB control on some firmware (module docstring), so ``awb`` can come back False while
    exposure and gain lock. Three frames are flushed after the lock, since a changed exposure
    can leave stale frames buffered (``capture_raw``).
    """
    exposure_us, gain_db = _read_3a()
    out = {"exposure_us": exposure_us, "gain_db": gain_db}
    for key, fn in (("exposure", lambda: sensor.set_auto_exposure(False, exposure_us=exposure_us)),
                    ("gain", lambda: sensor.set_auto_gain(False, gain_db=gain_db)),
                    ("awb", lambda: sensor.set_auto_whitebal(False))):
        try:
            fn()
            out[key] = True
        except Exception as exc:
            out[key] = False
            out[key + "_error"] = repr(exc)
    sensor.skip_frames(3)
    out["exposure_us_after"], out["gain_db_after"] = _read_3a()
    return out


def _tag_box(tag, w, h, margin=40):
    """Search box around one tag for the next frame: its corners + ``margin`` px, clipped to the frame."""
    c = _val(tag.corners)
    x0 = max(0, min(p[0] for p in c) - margin)
    y0 = max(0, min(p[1] for p in c) - margin)
    x1 = min(w, max(p[0] for p in c) + margin)
    y1 = min(h, max(p[1] for p in c) + margin)
    return (x0, y0, x1 - x0, y1 - y0)


def _val(attr):
    """A tag field that is a method on some firmware and a property on others (QE 010 N2)."""
    return attr() if callable(attr) else attr


def _finite(v):
    """JSON-safe float: the AE3 reports ``rgb_gain_db()`` as (-inf, 0, -inf)."""
    v = float(v)
    return v if v == v and v not in (float("inf"), float("-inf")) else None


# PAG7936 frame-time registers (us, 21 bit) + the sensor-update commit — the sensor on both
# boards. Ported unchanged from Nick's ``ADIN_SPI_OpenMV/pi/s28/s28_board_burst.py``
# (``set_frame_time``, verified there 2026-09-03: exposure read-back exact, no wedge). The
# exposure lock clamps to the live frame time (N6 8,248 us, AE3 16,584 us at HD Bayer by
# default, OQ-51), so a longer exposure needs a longer frame first. ``csi.framerate()`` would
# do this through a mode rewrite + capture abort that wedges the board — never call it.
_FT_H, _FT_M, _FT_L = 0x004E, 0x004D, 0x004C
_SENSOR_UPDATE, _SU_FLAG = 0x00EB, 0x80
_FT_SLACK_US = 5000  # frame time = exposure + slack (the sensor keeps an 80 us margin)
_FT_MAX_US = 2000000


def _set_frame_time(cam, ft_us):
    ft_us = min(max(int(ft_us), 200), _FT_MAX_US)
    h = cam.__read_reg(_FT_H)
    cam.__write_reg(_FT_H, (h & 0xE0) | ((ft_us >> 16) & 0x1F))
    cam.__write_reg(_FT_M, (ft_us >> 8) & 0xFF)
    cam.__write_reg(_FT_L, ft_us & 0xFF)
    cam.__write_reg(_SENSOR_UPDATE, _SU_FLAG)
    return ft_us


def _get_frame_time(cam):
    return (((cam.__read_reg(_FT_H) & 0x1F) << 16) | (cam.__read_reg(_FT_M) << 8)
            | cam.__read_reg(_FT_L))


def _snap(cam, tries=4):
    """``snapshot()`` that retries the transient "Frame capture has timed out." seen on the
    first frame after a frame-time change (Nick's ``snap``, measured 2026-09-02)."""
    for i in range(tries):
        try:
            return cam.snapshot()
        except RuntimeError as e:
            if "timed out" in str(e) and i < tries - 1:
                time.sleep_ms(60)
                continue
            raise


def capture_raw(usb, command_id, board_config, settings):
    """Bayer RAW at a locked exposure, streamed straight from RAM to the host (S3, OQ-21).

    Uses the v5 ``csi`` module (``csi.BAYER`` is the only raw format: 8 bit, 1 byte/px).
    Autos run for ``warmup_ms``, then exposure / gain are locked at the metered values —
    or at ``exposure_us`` / ``gain_db`` when given — and WB is frozen, then 3 frames are
    flushed (a changed exposure can leave two stale frames buffered; Nick's
    ``s28_board_burst.py``) before the snapshot, so the read-back values describe this
    frame. The ISP WB gains are *not* in the Bayer data and ``rgb_gain_db()`` lists the
    top-left site first (OQ-21): reported as-is, for diagnosis only. An exposure longer than
    the default frame time allows is reached by lengthening the frame through the sensor's
    registers (``_set_frame_time``, OQ-51; reported as ``frame_time_us``). Never calls
    ``csi.framerate()`` (wedges the board). On the AE3 this is a camera session:
    ``reset_board`` first (one session per boot on v5).

    Wire sequence, as ``send_file`` (§10): ``sending`` header (size + SHA-256) -> exactly
    size_bytes of mosaic -> ``completed`` line carrying the capture metadata. No ``/flash``
    copy: writing 1 MB to the N6's flash took ~7.6 s of wall time with the tick counter and
    USB stalled (``ticks_ms`` reported 0.5 s), and the N6 dropped off USB after ~1 in 10
    such captures (``nereus002``, 2026-09-28); with no flash write the capture takes ~1 s.
    Settings errors raise ``ProtocolError`` before any binary is sent.
    """
    import csi

    fs_name = settings.get("framesize", board_config.RAW_DEFAULT_FRAMESIZE)
    attr = board_config.RAW_FRAMESIZES.get(fs_name)
    if attr is None:
        raise cp.ProtocolError(
            cp.ERR_CAPTURE_FAILED, "unsupported raw framesize: " + repr(fs_name)
        )
    warmup_ms = int(settings.get("warmup_ms", board_config.DEFAULT_WARMUP_MS))
    name = _safe_basename(settings.get("filename") or "capture.bayer")

    t_start = time.ticks_ms()
    cam = csi.CSI()
    cam.reset()
    cam.pixformat(csi.BAYER)
    cam.framesize(getattr(csi, attr))
    while time.ticks_diff(time.ticks_ms(), t_start) < warmup_ms:
        cam.snapshot()
    metered = {"exposure_us": cam.exposure_us(), "gain_db": _finite(cam.gain_db())}
    # exposure_scale (default 1): lock at metered x scale, e.g. 0.5 = one stop dark for the
    # calibration block, so the ChArUco white squares stay under ~250 (8-bit Bayer).
    scale = float(settings.get("exposure_scale") or 1.0)
    exposure_us = int(settings.get("exposure_us") or metered["exposure_us"] * scale)
    gain_db = float(settings.get("gain_db") or metered["gain_db"] or 0.0)
    cam.auto_exposure(False, exposure_us=exposure_us)
    cam.auto_gain(False, gain_db=gain_db)
    cam.auto_whitebal(False)
    frame_time_us = default_ft = None
    if cam.exposure_us() < exposure_us * 0.95:
        # Clamped by the frame time: lengthen the frame, then lock the exposure again. Only
        # then — the default path never touches sensor registers. The previous frame time is
        # put back after the frame is sent, so a long capture leaves no state behind (a
        # later short locked capture would otherwise still run one frame per long period).
        default_ft = _get_frame_time(cam)
        frame_time_us = _set_frame_time(cam, exposure_us + _FT_SLACK_US)
        cam.auto_exposure(False, exposure_us=exposure_us)
    try:
        _capture_locked_and_send(usb, command_id, board_config, cam, name, fs_name, t_start,
                                 metered, exposure_us, gain_db, frame_time_us,
                                 trigger=bool(settings.get("trigger")))
    finally:
        if default_ft is not None:
            _set_frame_time(cam, default_ft)


def _capture_locked_and_send(usb, command_id, board_config, cam, name, fs_name, t_start,
                             metered, exposure_us, gain_db, frame_time_us, trigger=False):
    t_locked = time.ticks_ms()
    for _ in range(3):
        _snap(cam)
    sync = None
    if trigger:
        sync = _wait_fire(usb, command_id, lambda: _snap(cam))
    img = _snap(cam)
    t_snap = time.ticks_ms()
    if sync is not None:
        sync["snap_ms"] = t_snap

    data = memoryview(img.bytearray())
    size = len(data)
    h = hashlib.sha256()
    for off in range(0, size, _RAW_CHUNK):
        h.update(data[off:off + _RAW_CHUNK])
    sha = binascii.hexlify(h.digest()).decode()
    t_sha = time.ticks_ms()
    out = {
        "filename": name,
        "width": img.width(),
        "height": img.height(),
        "format": "bayer",
        "framesize": fs_name,
        "cfa": board_config.RAW_CFA,
        "bits": board_config.RAW_BITS,
        "black_level": board_config.RAW_BLACK_LEVEL,
        "white_level": (1 << board_config.RAW_BITS) - 1,
        "exposure_us": cam.exposure_us(),
        "gain_db": _finite(cam.gain_db()),
        "requested": {"exposure_us": exposure_us, "gain_db": gain_db},
        "frame_time_us": frame_time_us,
        "metered": metered,
        "isp_rgb_gain_db": [_finite(v) for v in cam.rgb_gain_db()],
        "size_bytes": size,
        "sha256": sha,
        "mount_rotation_deg": board_config.MOUNT_ROTATION_DEG,
    }
    if sync is not None:
        out["sync"] = sync
    usb.write(cp.encode_message(cp.sending_response(command_id, name, size, sha)))
    for off in range(0, size, _RAW_CHUNK):
        usb.write(data[off:off + _RAW_CHUNK])
    # Where the capture time goes (metering, lock + flush + snapshot, SHA-256, USB send);
    # the soak cadence depends on it (S7).
    out["timing_ms"] = {
        "meter": time.ticks_diff(t_locked, t_start),
        "lock_snapshot": time.ticks_diff(t_snap, t_locked),
        "sha256": time.ticks_diff(t_sha, t_snap),
        "send": time.ticks_diff(time.ticks_ms(), t_sha),
    }
    usb.write(cp.encode_message(cp.completed_response(command_id, out)))


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
    # Optional frame-rate cap (0 = as fast as the board can, the old behaviour). Fewer frames
    # = less CPU/USB activity and current: Nick 2026-10-13, N6 at 10 fps like the AE3.
    fps = float(settings.get("fps", 0) or 0)
    period_ms = int(1000 / fps) if fps > 0 else 0
    # JPEG chroma subsampling (pool 2 B0, 2026-10-17). Absent / "auto" = the firmware's own choice,
    # unchanged: the AE3's software encoder picks 4:4:4 at quality >= 60 (lib/imlib/jpege.c), the
    # N6's hardware encoder is 4:2:0. "420" | "422" | "444" force one.
    sub = settings.get("subsampling") or "auto"
    if sub not in _SUBSAMPLING:
        raise cp.ProtocolError(cp.ERR_CAPTURE_FAILED, "unsupported subsampling: " + repr(sub))
    sub_const = None
    if sub != "auto":
        import image  # OpenMV firmware module; imported only when a subsampling is forced

        sub_const = getattr(image, _SUBSAMPLING[sub])
    # Per-frame timing (B0): each header carries this frame's snapshot and JPEG ms and the
    # previous frame's USB write ms (a frame's own write ends after its header is sent).
    timing = bool(settings.get("timing"))
    # Per-frame 3A (SPEC_n6_eval must do 2): each header carries the exposure (us) and gain
    # (dB) read right after its snapshot. Off by default: the header stays as before.
    meta = bool(settings.get("meta"))
    # On-board AprilTags on every streamed frame (SPEC_n6_eval must do 17's "stream + detect"
    # power mode, Nick 10/23 DOE): tag count, ids and detect ms go in the frame header. The
    # family constant is looked up by name (``tag_family``, default TAG25H9 = card V3), never
    # assumed: stock v5.0.1 on the N6 defines only TAG36H11 (10/23), so a missing name is a stream
    # error, not a silent 0 tags.
    tag_family = None
    if settings.get("apriltags"):
        import image

        fam_name = settings.get("tag_family") or "TAG25H9"
        tag_family = getattr(image, fam_name, None)
        if tag_family is None:
            raise cp.ProtocolError(cp.ERR_CAPTURE_FAILED, "no %s family in image module" % fam_name)
    # Bench 2 (10/24): detect on every Nth frame only (video keeps its rate), and/or track: search
    # only boxes around the last frame's tags (HD 32 ms vs 182 ms full frame, bench 1), falling
    # back to the full frame whenever a tracked tag is missing.
    tag_every = max(1, int(settings.get("tag_every") or 1))
    tag_roi = bool(settings.get("tag_roi"))
    tag_boxes = None

    sensor.reset()
    # The sensor actually fitted (focus page, Nick 10/24: PAG7936 1 MP or PS5520 5 MP). Read live,
    # because board_config.SENSOR_NAME is a constant; sent once, in the first frame's header.
    try:
        sensor_id = sensor.get_id()
    except Exception:
        sensor_id = None
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(fs_const)
    # Synchronized video (settings["trigger"], 2026-10-14): warm up, say "armed", start on the
    # host's fire byte; every frame header then carries its board ticks. Focus stream: as before.
    trigger = bool(settings.get("trigger"))
    sensor.skip_frames(time=int(settings.get("warmup_ms", 2000 if trigger else 300)))
    # AE + gain + AWB locked after the warm-up (SPEC_n6_eval test 1); absent = autos run.
    locked = _lock_3a() if settings.get("lock_3a") else None
    sync = _wait_fire(usb, command_id, sensor.snapshot) if trigger else None
    if sync is not None and sync["timed_out"]:
        # The host never fired (it died or gave up): do not stream into a port nobody reads.
        usb.write(cp.encode_message(cp.completed_response(command_id,
                                                          {"frames": 0, "sync": sync})))
        return

    seq = 0
    write_ms = None
    # Bench 3 (10/24, Nick: power with frames kept in RAM vs streamed): keep each JPEG in RAM and
    # send nothing but a 1 s "buffering" keepalive until the stop; then the whole burst. A full
    # heap ends the capture (reported), it never crashes the stream.
    buffering = bool(settings.get("buffer_frames"))
    buf, buffer_error, t_keep = [], None, time.ticks_ms()
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
        t_frame = time.ticks_ms()
        img = sensor.snapshot()
        t_snap = time.ticks_ms()
        tag_extra = None
        if tag_family is not None and seq % tag_every == 0:
            try:
                tags, mode = None, "full"
                if tag_roi and tag_boxes:
                    tags = []
                    for box in tag_boxes:
                        tags.extend(img.find_apriltags(roi=box, families=tag_family))
                    mode = "roi"
                    if len(tags) < len(tag_boxes):  # lost one: search the whole frame again
                        tags, mode = None, "roi+full"
                if tags is None:
                    tags = img.find_apriltags(families=tag_family)
                if tag_roi:
                    tag_boxes = [_tag_box(t, img.width(), img.height()) for t in tags] or None
                tag_extra = {"tags": len(tags), "tag_ids": [_val(t.id) for t in tags]}
                if tag_roi:
                    tag_extra["tag_mode"] = mode
            except Exception as exc:  # e.g. MemoryError at HD: reported per frame, stream goes on
                tag_extra = {"tags": None, "tag_error": repr(exc)}
            tag_extra["detect_ms"] = time.ticks_diff(time.ticks_ms(), t_snap)
            t_snap = time.ticks_ms()  # jpeg_ms below stays the encode alone
        if sub_const is None:
            img.compress(quality=quality)
        else:
            img.compress(quality=quality, subsampling=sub_const)
        data = img.bytearray()
        n = len(data)
        extra = None
        if timing:
            extra = {"snap_ms": time.ticks_diff(t_snap, t_frame),
                     "jpeg_ms": time.ticks_diff(time.ticks_ms(), t_snap),
                     "prev_write_ms": write_ms}
        if meta:
            extra = extra or {}
            extra["exposure_us"], extra["gain_db"] = _read_3a()
        if tag_extra is not None:
            extra = extra or {}
            extra.update(tag_extra)
        if seq == 0 and sensor_id is not None:
            extra = extra or {}
            extra["sensor_id"] = sensor_id
        t_write = time.ticks_ms()
        header = cp.encode_message(
            cp.frame_response(command_id, seq, n, n, img.width(), img.height(),
                              ticks_ms=t_snap if trigger else None, extra=extra)
        )
        if buffering:
            try:
                buf.append((header, bytes(data)))
            except MemoryError as exc:
                buffer_error = repr(exc)
                break
            if time.ticks_diff(time.ticks_ms(), t_keep) >= 1000:
                t_keep = time.ticks_ms()
                usb.write(cp.encode_message({"version": cp.PROTOCOL_VERSION, "command_id": command_id,
                                             "status": "buffering", "frames": len(buf)}))
        else:
            usb.write(header)
            usb.write(data)
        write_ms = time.ticks_diff(time.ticks_ms(), t_write)
        seq += 1
        if period_ms:
            # Sleep the rest of the period in <= 50 ms slices so a host stop byte is still seen
            # promptly even with a tiny --fps (QE #107).
            while not usb.any():
                rest = period_ms - time.ticks_diff(time.ticks_ms(), t_frame)
                if rest <= 0:
                    break
                time.sleep_ms(min(rest, 50))

    if buffering:
        t_burst = time.ticks_ms()
        for header, data in buf:
            usb.write(header)
            usb.write(data)
        del buf
    out = {"frames": seq}
    if buffering:
        out["buffered"] = {"frames": seq, "burst_ms": time.ticks_diff(time.ticks_ms(), t_burst),
                           "error": buffer_error}
    if sync is not None:
        out["sync"] = sync
    if locked is not None:
        out["locked_3a"] = locked
    usb.write(cp.encode_message(cp.completed_response(command_id, out)))
