"""Hardware smoke test for the OpenMV AE3 — Spec §24 (hardware), Phase 4 exit criteria.

Runs on the Pi against a real AE3 with the capture service deployed (see
``scripts/test_openmv_ae3.sh``, which deploys then invokes these). Excluded from the
default suite (``norecursedirs = tests/hardware``); skips cleanly when no board or
pyserial is present, so it never fails a hardware-less CI run.

Proves the Phase 4 exit criteria: discover the AE3 by USB identity, capture N stills in a
row each retrieved with a matching checksum, and reject a bad command cleanly. The AE3
reuses the same board-agnostic host adapter and USB protocol as the N6 (Phase 3); only
the board identity/sensor facts differ.
"""

import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("serial", reason="pyserial not installed (install the 'serial' extra)")

import serial  # noqa: E402
from host_tools.discover_openmv import find_port  # noqa: E402

from nereus_camera_test_rig.cameras.openmv_usb import OpenMvUsbCamera  # noqa: E402
from nereus_camera_test_rig.models import CaptureRequest  # noqa: E402

# The AE3 validated during Phase 4 bring-up. Override via the AE3_SERIAL env var.
AE3_SERIAL = os.environ.get("AE3_SERIAL", "0829c14000000000")


def _ae3_port():
    port = find_port(AE3_SERIAL)
    if not port:
        pytest.skip("AE3 (serial %s) not connected" % AE3_SERIAL)
    return port


def test_device_info():
    _ae3_port()
    cam = OpenMvUsbCamera(serial_number=AE3_SERIAL, board="ae3")
    try:
        info = cam.get_device_info()
    finally:
        cam.close()
    assert info["board"] == "ae3"
    assert info["sensor"] == "PAG7936"
    assert info["platform"] == "openmv"
    assert info.get("firmware")


def test_repeated_capture_with_checksums(tmp_path):
    _ae3_port()
    cam = OpenMvUsbCamera(serial_number=AE3_SERIAL, board="ae3")
    shas = []
    try:
        for i in range(3):
            dest = tmp_path / ("ae3_%d.jpg" % i)
            result = cam.capture_image(
                str(dest),
                CaptureRequest(kind="image", settings={"framesize": "VGA", "warmup_ms": 800}),
            )
            assert result.ok, result.error
            assert dest.is_file() and dest.stat().st_size > 1000
            assert result.sha256 and result.width == 640 and result.height == 400
            shas.append(result.sha256)
    finally:
        cam.close()
    # Each capture is a fresh frame (the adapter already verified board vs on-disk sha).
    assert len(set(shas)) == 3


def test_reset_board_returns_to_service(tmp_path):
    """reset_board hard-resets the MCU; the service must come back and capture cleanly.

    On this board the hard reset is the *only* in-band cure for stale firmware AWB
    state: fw 1.25.0-preview exposes no AWB control for the PAG7936, and after the
    2026-07-16 lights-off runs every capture kept a strong green cast (grey dE 39.7)
    across per-capture ``sensor.reset()`` calls until a manual ``mpremote reset``.
    """
    _ae3_port()
    cam = OpenMvUsbCamera(serial_number=AE3_SERIAL, board="ae3")
    try:
        out = cam.reset_board()
        assert out["info"]["board"] == "ae3"
        assert 0 < out["duration_seconds"] < 20
        dest = tmp_path / "after_reset.jpg"
        result = cam.capture_image(
            str(dest),
            CaptureRequest(kind="image", settings={"framesize": "VGA", "warmup_ms": 800}),
        )
        assert result.ok, result.error
        assert dest.is_file() and dest.stat().st_size > 1000
        # The settled 3A state is recorded so a stale-state capture is diagnosable (§12).
        assert result.sensor_metadata.get("exposure_us") is not None
        assert result.sensor_metadata.get("gain_db") is not None
    finally:
        cam.close()


def test_flash_free_space_stable_across_captures(tmp_path):
    """Captures must not accumulate on /flash (2026-07-17: the N6 filled its flash and
    every capture failed with io_error "Write failed"; same exposure on the AE3).

    The host deletes each flash copy after the checksum-verified retrieval, so free
    space after N captures must return to (at least) the starting level, within a small
    filesystem-bookkeeping slack — far below one VGA JPEG (~30-90 KB), so a leak of even
    a single retained capture fails the test.
    """
    _ae3_port()
    cam = OpenMvUsbCamera(serial_number=AE3_SERIAL, board="ae3")
    try:
        free_before = cam.get_device_info().get("flash_free_bytes")
        assert free_before is not None, "firmware does not report flash_free_bytes"
        for i in range(3):
            dest = tmp_path / ("ae3_flash_%d.jpg" % i)
            result = cam.capture_image(
                str(dest),
                CaptureRequest(kind="image", settings={"framesize": "VGA", "warmup_ms": 800}),
            )
            assert result.ok, result.error
        free_after = cam.get_device_info().get("flash_free_bytes")
    finally:
        cam.close()
    assert free_after >= free_before - 8192, (
        "flash leaked %d bytes over 3 captures — on-board cleanup not working"
        % (free_before - free_after)
    )


def test_rejects_unknown_action():
    port = _ae3_port()
    bad = {"version": 1, "command_id": "bad", "action": "exec_python"}
    with serial.Serial(port, 115200, timeout=3) as s:
        s.reset_input_buffer()
        s.write(json.dumps(bad).encode() + b"\n")
        resp = _read_json_line(s)
    assert resp["status"] == "failed"
    assert resp["error"]["code"] == "unknown_action"


def test_rejects_malformed_json():
    port = _ae3_port()
    with serial.Serial(port, 115200, timeout=3) as s:
        s.reset_input_buffer()
        s.write(b"{not valid json\n")
        resp = _read_json_line(s)
    assert resp["status"] == "failed"
    assert resp["error"]["code"] == "bad_json"


def _read_json_line(s):
    buf = b""
    while b"\n" not in buf:
        chunk = s.read(256)
        if not chunk:
            break
        buf += chunk
    return json.loads(buf.decode())
