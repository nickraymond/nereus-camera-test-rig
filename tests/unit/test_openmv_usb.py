"""Unit tests for the host OpenMV USB adapter — Spec §8, §10, §19.

A ``FakeBoard`` transport speaks the *real* protocol codec back to the adapter, so the
whole capture -> retrieve -> verify flow is exercised on the host with no hardware. The
board's own sensor path is validated separately by the hardware smoke test.
"""

import hashlib
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from openmv.common import command_protocol as cp  # noqa: E402

from nereus_camera_test_rig.cameras.openmv_usb import OpenMvUsbCamera  # noqa: E402
from nereus_camera_test_rig.models import CaptureRequest  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeBoard:
    """Loopback transport emulating the on-board JSON service (read/write interface).

    ``captured_bytes`` are what a snapshot "produces"; ``corrupt_sha`` forces the capture
    metadata's sha256 to disagree with the real bytes (to exercise §19 verification).
    """

    DEVICE_INFO = {
        "platform": "openmv", "board": "n6", "device_id": "openmv-n6-001",
        "sensor": "PAG7936", "firmware": "1.26.0", "mount_rotation_deg": 90,
    }

    def __init__(self, captured_bytes=b"\xff\xd8fake-jpeg-body\xff\xd9", corrupt_sha=False):
        self.captured = captured_bytes
        self.corrupt_sha = corrupt_sha
        self.files = {}
        self._in = bytearray()
        self._out = bytearray()
        self.seen_actions = []

    # transport interface used by _SerialIO ---------------------------------
    def write(self, data):
        self._in += data
        while b"\n" in self._in:
            idx = self._in.index(b"\n")
            line = bytes(self._in[:idx])
            del self._in[: idx + 1]
            if line.strip():
                self._handle(line)

    def read(self, n):
        out = bytes(self._out[:n])
        del self._out[:n]
        return out

    # board behaviour -------------------------------------------------------
    def _emit(self, message):
        self._out += cp.encode_message(message)

    def _handle(self, line):
        command_id = None
        try:
            req = cp.decode_message(line)
            command_id = req.get("command_id")
            action, command_id, settings = cp.validate_request(req)
            self.seen_actions.append(action)
            if action == "get_device_info":
                self._emit(cp.completed_response(command_id, dict(self.DEVICE_INFO)))
            elif action == "capture_image":
                self._do_capture(command_id, settings)
            elif action == "get_file":
                self._do_get_file(command_id, settings.get("filename"))
        except cp.ProtocolError as exc:
            self._emit(cp.failed_response(command_id, exc.code, exc.message))

    def _do_capture(self, command_id, settings):
        name = settings.get("filename") or "capture.jpg"
        data = self.captured
        self.files[name] = data
        real_sha = _sha(data)
        meta = {
            "filename": name, "width": 1280, "height": 800, "format": "jpeg",
            "framesize": settings.get("framesize", "HD"),
            "pixel_format": settings.get("pixel_format", "RGB565"),
            "jpeg_quality": settings.get("jpeg_quality", 90),
            "size_bytes": len(data),
            "sha256": ("0" * 64) if self.corrupt_sha else real_sha,
            "mount_rotation_deg": 90,
        }
        self._emit(cp.completed_response(command_id, meta))

    def _do_get_file(self, command_id, filename):
        data = self.files.get(filename)
        if data is None:
            self._emit(cp.failed_response(command_id, cp.ERR_FILE_NOT_FOUND, "no such file"))
            return
        self._emit(cp.sending_response(command_id, filename, len(data), _sha(data)))
        self._out += data  # raw framed payload
        self._emit(cp.completed_response(command_id, {
            "filename": filename, "size_bytes": len(data), "sha256": _sha(data)}))


def test_get_device_info_populates_identity():
    cam = OpenMvUsbCamera(serial_number="005537493543", transport=FakeBoard())
    info = cam.get_device_info()
    assert info["board"] == "n6"
    assert info["sensor"] == "PAG7936"


def test_capture_image_round_trips_bytes_and_checksum(tmp_path):
    board = FakeBoard(captured_bytes=b"\xff\xd8" + b"payload" * 100 + b"\xff\xd9")
    cam = OpenMvUsbCamera(serial_number="s", transport=board)
    dest = tmp_path / "n6" / "image.jpg"
    request = CaptureRequest(kind="image", settings={"framesize": "HD"})
    result = cam.capture_image(str(dest), request)

    assert result.ok, result.error
    assert dest.is_file()
    assert dest.read_bytes() == board.captured
    assert result.sha256 == _sha(board.captured)
    assert result.width == 1280 and result.height == 800
    assert result.sensor_metadata["mount_rotation_deg"] == 90
    # The host set the filename from the destination basename.
    assert "image.jpg" in board.files


def test_capture_detects_checksum_mismatch(tmp_path):
    # Board reports a sha that disagrees with the bytes -> must fail, not silently pass.
    cam = OpenMvUsbCamera(serial_number="s", transport=FakeBoard(corrupt_sha=True))
    dest = tmp_path / "image.jpg"
    result = cam.capture_image(str(dest), CaptureRequest(kind="image"))
    assert not result.ok
    assert result.error["code"] == "checksum_mismatch"


def test_capture_reports_board_failure(tmp_path):
    # A get_file for a name the board never stored -> structured failure surfaced.
    # Force capture to succeed but wipe the stored file before get_file runs.
    class DropFileBoard(FakeBoard):
        def _do_capture(self, command_id, settings):
            super()._do_capture(command_id, settings)
            self.files.clear()

    cam = OpenMvUsbCamera(serial_number="s", transport=DropFileBoard())
    dest = tmp_path / "image.jpg"
    result = cam.capture_image(str(dest), CaptureRequest(kind="image"))
    assert not result.ok
    assert result.error["code"] == cp.ERR_FILE_NOT_FOUND


def test_capture_video_reports_unsupported(tmp_path):
    cam = OpenMvUsbCamera(serial_number="s", transport=FakeBoard())
    result = cam.capture_video(str(tmp_path / "v.mjpeg"), CaptureRequest(kind="video"))
    assert not result.ok
    assert result.error["code"] == "not_supported"


def test_health_check_ok():
    cam = OpenMvUsbCamera(serial_number="s", transport=FakeBoard())
    health = cam.health_check()
    assert health["healthy"] is True
    assert health["board"] == "n6"


def test_only_allowlisted_actions_are_sent(tmp_path):
    board = FakeBoard()
    cam = OpenMvUsbCamera(serial_number="s", transport=board)
    cam.capture_image(str(tmp_path / "i.jpg"), CaptureRequest(kind="image"))
    assert set(board.seen_actions) <= set(cp.ALLOWED_ACTIONS)
    assert board.seen_actions == ["capture_image", "get_file"]
