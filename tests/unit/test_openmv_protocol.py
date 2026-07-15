"""Unit tests for the shared OpenMV command protocol codec — Spec §8.

Runs on the host (Mac) with no hardware. The same module runs on the board under
MicroPython, so these tests pin the wire contract both sides depend on.
"""

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from openmv.common import command_protocol as cp  # noqa: E402


def test_encode_message_is_single_newline_terminated_line():
    raw = cp.encode_message({"a": 1, "b": "x"})
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1
    assert json.loads(raw.decode("utf-8")) == {"a": 1, "b": "x"}


def test_encode_decode_round_trip():
    msg = cp.make_request("capture_image", "cid-1", {"framesize": "HD", "jpeg_quality": 90})
    assert cp.decode_message(cp.encode_message(msg)) == msg


def test_decode_accepts_bytes_and_str_with_or_without_newline():
    msg = {"version": 1, "command_id": "z", "status": "completed"}
    line = json.dumps(msg)
    assert cp.decode_message(line) == msg
    assert cp.decode_message(line + "\n") == msg
    assert cp.decode_message(line.encode("utf-8") + b"\n") == msg


def test_decode_rejects_bad_json():
    with pytest.raises(cp.ProtocolError) as exc:
        cp.decode_message(b"{not json")
    assert exc.value.code == cp.ERR_BAD_JSON


def test_decode_rejects_non_object():
    with pytest.raises(cp.ProtocolError) as exc:
        cp.decode_message(b"[1, 2, 3]")
    assert exc.value.code == cp.ERR_BAD_JSON


def test_validate_request_accepts_allowlisted_actions():
    for action in cp.ALLOWED_ACTIONS:
        req = cp.make_request(action, "cid", {"k": "v"})
        got_action, cid, settings = cp.validate_request(req)
        assert got_action == action
        assert cid == "cid"
        assert settings == {"k": "v"}


def test_validate_request_rejects_unknown_action():
    req = cp.make_request("rm_rf", "cid")
    # An unknown action is exactly the arbitrary-exec vector §8 forbids.
    req["action"] = "exec_python"
    with pytest.raises(cp.ProtocolError) as exc:
        cp.validate_request(req)
    assert exc.value.code == cp.ERR_UNKNOWN_ACTION


def test_validate_request_rejects_bad_version():
    req = {"version": 99, "command_id": "cid", "action": "get_device_info"}
    with pytest.raises(cp.ProtocolError) as exc:
        cp.validate_request(req)
    assert exc.value.code == cp.ERR_UNSUPPORTED_VERSION


def test_validate_request_rejects_missing_action():
    req = {"version": 1, "command_id": "cid"}
    with pytest.raises(cp.ProtocolError) as exc:
        cp.validate_request(req)
    assert exc.value.code == cp.ERR_MISSING_FIELD


def test_validate_request_rejects_non_object_settings():
    req = {"version": 1, "command_id": "cid", "action": "capture_image", "settings": [1, 2]}
    with pytest.raises(cp.ProtocolError) as exc:
        cp.validate_request(req)
    assert exc.value.code == cp.ERR_MISSING_FIELD


def test_make_request_omits_empty_settings():
    assert "settings" not in cp.make_request("get_device_info", "cid")
    assert "settings" not in cp.make_request("get_device_info", "cid", {})


def test_response_builders_shape():
    ok = cp.completed_response("cid", {"filename": "a.jpg"})
    assert ok["status"] == "completed" and ok["output"]["filename"] == "a.jpg"
    bad = cp.failed_response("cid", cp.ERR_CAPTURE_FAILED, "boom")
    assert bad["status"] == "failed"
    assert bad["error"] == {"code": cp.ERR_CAPTURE_FAILED, "message": "boom"}
    sending = cp.sending_response("cid", "a.jpg", 10, "deadbeef")
    assert sending["status"] == "sending"
    assert sending["transfer"] == {"filename": "a.jpg", "size_bytes": 10, "sha256": "deadbeef"}


def test_start_stream_is_allowlisted():
    assert cp.is_allowed("start_stream")
    action, cid, settings = cp.validate_request(
        cp.make_request("start_stream", "cid", {"framesize": "VGA", "jpeg_quality": 70})
    )
    assert action == "start_stream"


def test_frame_response_shape():
    fr = cp.frame_response("cid", seq=3, size_bytes=2048, sharpness=2048, width=640, height=400)
    assert fr["status"] == "frame"
    assert fr["frame"] == {
        "seq": 3, "size_bytes": 2048, "sharpness": 2048, "width": 640, "height": 400,
    }
