"""OpenMV USB command protocol — Spec §8, §10.

The single source of truth for the wire format spoken between the Pi host and an
OpenMV board over USB CDC serial. This module is deliberately **pure** (only
``import json``) so the exact same file runs on the board under MicroPython and is
imported by the host under CPython — consistent behaviour from one source (§36),
not two drifting copies.

Wire format
-----------
Every control message is one line of UTF-8 JSON terminated by ``\\n``. Requests and
responses never rely on byte-exact JSON — both sides ``json.loads`` — so differences
between CPython and MicroPython ``json.dumps`` spacing are irrelevant.

Request  (host -> board)::

    {"version":1,"command_id":"<id>","action":"<action>","settings":{...}}\\n

Control response (board -> host)::

    {"version":1,"command_id":"<id>","status":"completed","output":{...}}\\n
    {"version":1,"command_id":"<id>","status":"failed","error":{"code":..,"message":..}}\\n

File transfer (board -> host), framed per §10 — never mix JSON and raw binary
without a length::

    {"version":1,"command_id":"<id>","status":"sending","transfer":{"filename":..,"size_bytes":N,"sha256":..}}\\n
    <exactly N raw bytes>
    {"version":1,"command_id":"<id>","status":"completed","output":{"filename":..,"size_bytes":N,"sha256":..}}\\n

Security (§8): commands are validated against ``ALLOWED_ACTIONS`` — there is **no**
arbitrary remote code execution. Unknown actions are rejected with a structured error.

MicroPython constraints (CLAUDE.md §23): no ``from __future__``, no ``typing`` import,
small allocations. Keep it copyable-unchanged to the board.
"""

import json

PROTOCOL_VERSION = 1

#: The complete command allowlist (§8). Anything else is rejected.
ALLOWED_ACTIONS = ("get_device_info", "capture_image", "get_file")

LINE_TERMINATOR = b"\n"

# Structured error codes (kept stable — the host branches on these).
ERR_BAD_JSON = "bad_json"
ERR_UNSUPPORTED_VERSION = "unsupported_version"
ERR_MISSING_FIELD = "missing_field"
ERR_UNKNOWN_ACTION = "unknown_action"
ERR_CAPTURE_FAILED = "capture_failed"
ERR_FILE_NOT_FOUND = "file_not_found"
ERR_IO_ERROR = "io_error"


class ProtocolError(Exception):
    """A malformed or disallowed message. Carries a stable ``code`` for responses.

    Uses ``super().__init__`` (not ``Exception.__init__(self, ...)``) — MicroPython's
    builtin types do not expose ``__init__``, so the explicit form raises AttributeError
    on the board (found by the N6 hardware smoke test).
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# -- encoding ---------------------------------------------------------------
def encode_message(message):
    """Serialize a dict to a single newline-terminated UTF-8 JSON line (bytes)."""
    return json.dumps(message).encode("utf-8") + LINE_TERMINATOR


def decode_message(line):
    """Parse one JSON line (str or bytes, trailing newline optional) into a dict.

    Raises ``ProtocolError(ERR_BAD_JSON)`` on invalid JSON or a non-object payload.
    """
    if isinstance(line, (bytes, bytearray)):
        line = bytes(line).decode("utf-8")
    line = line.strip()
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        raise ProtocolError(ERR_BAD_JSON, "message is not valid JSON")
    if not isinstance(obj, dict):
        raise ProtocolError(ERR_BAD_JSON, "message must be a JSON object")
    return obj


# -- message builders -------------------------------------------------------
def make_request(action, command_id, settings=None, version=PROTOCOL_VERSION):
    """Build a request dict. ``settings`` omitted when None to keep lines compact."""
    req = {"version": version, "command_id": command_id, "action": action}
    if settings:
        req["settings"] = settings
    return req


def completed_response(command_id, output=None, version=PROTOCOL_VERSION):
    resp = {"version": version, "command_id": command_id, "status": "completed"}
    if output is not None:
        resp["output"] = output
    return resp


def failed_response(command_id, code, message, version=PROTOCOL_VERSION):
    return {
        "version": version,
        "command_id": command_id,
        "status": "failed",
        "error": {"code": code, "message": message},
    }


def sending_response(command_id, filename, size_bytes, sha256, version=PROTOCOL_VERSION):
    """Header line that precedes a framed binary payload (get_file)."""
    return {
        "version": version,
        "command_id": command_id,
        "status": "sending",
        "transfer": {"filename": filename, "size_bytes": size_bytes, "sha256": sha256},
    }


# -- validation -------------------------------------------------------------
def validate_request(request):
    """Validate a decoded request against the protocol + allowlist.

    Returns ``(action, command_id, settings)`` on success. Raises ``ProtocolError``
    with a stable code otherwise, so the caller can reply with a structured failure.
    """
    command_id = request.get("command_id")
    version = request.get("version")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            ERR_UNSUPPORTED_VERSION, "unsupported protocol version: " + repr(version)
        )
    action = request.get("action")
    if not action:
        raise ProtocolError(ERR_MISSING_FIELD, "request missing 'action'")
    if action not in ALLOWED_ACTIONS:
        raise ProtocolError(ERR_UNKNOWN_ACTION, "action not allowed: " + repr(action))
    settings = request.get("settings") or {}
    if not isinstance(settings, dict):
        raise ProtocolError(ERR_MISSING_FIELD, "'settings' must be an object")
    return action, command_id, settings


def is_allowed(action):
    """True if ``action`` is in the command allowlist."""
    return action in ALLOWED_ACTIONS
