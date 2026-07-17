"""Hardware smoke test for the OpenMV N6 — Spec §24 (hardware), Phase 3 exit criteria.

Runs on the Pi against a real N6 with the capture service deployed (see
``scripts/test_openmv_n6.sh``, which deploys then invokes these). Excluded from the
default suite (``norecursedirs = tests/hardware``); skips cleanly when no board or
pyserial is present, so it never fails a hardware-less CI run.

Proves the Phase 3 exit criteria: discover the N6 by USB identity, capture N stills in a
row each retrieved with a matching checksum, and reject a bad command cleanly.
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

# The N6 validated during Phase 3 bring-up. Override via the N6_SERIAL env var.
N6_SERIAL = os.environ.get("N6_SERIAL", "005537493543")


def _n6_port():
    port = find_port(N6_SERIAL)
    if not port:
        pytest.skip("N6 (serial %s) not connected" % N6_SERIAL)
    return port


def test_device_info():
    _n6_port()
    cam = OpenMvUsbCamera(serial_number=N6_SERIAL, board="n6")
    try:
        info = cam.get_device_info()
    finally:
        cam.close()
    assert info["board"] == "n6"
    assert info["sensor"] == "PAG7936"
    assert info["platform"] == "openmv"
    assert info.get("firmware")


def test_repeated_capture_with_checksums(tmp_path):
    _n6_port()
    cam = OpenMvUsbCamera(serial_number=N6_SERIAL, board="n6")
    shas = []
    try:
        for i in range(3):
            dest = tmp_path / ("n6_%d.jpg" % i)
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

    This is the automated form of the manual ``mpremote reset`` that cleared the AE3's
    stuck-AWB green cast on 2026-07-16 (firmware 3A state survives ``sensor.reset()``).
    Measured ~3.2-3.7 s to service-ready on both boards.
    """
    _n6_port()
    cam = OpenMvUsbCamera(serial_number=N6_SERIAL, board="n6")
    try:
        out = cam.reset_board()
        assert out["info"]["board"] == "n6"
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


def test_rejects_unknown_action():
    port = _n6_port()
    bad = {"version": 1, "command_id": "bad", "action": "exec_python"}
    with serial.Serial(port, 115200, timeout=3) as s:
        s.reset_input_buffer()
        s.write(json.dumps(bad).encode() + b"\n")
        resp = _read_json_line(s)
    assert resp["status"] == "failed"
    assert resp["error"]["code"] == "unknown_action"


def test_rejects_malformed_json():
    port = _n6_port()
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
