"""OpenMV AE3 capture service entry point — Spec §7, §8.

Auto-runs at boot (MicroPython runs ``main.py`` after ``boot.py``). Owns the USB CDC
serial via ``pyb.USB_VCP``, reads newline-delimited JSON commands, validates each
against the allowlist, dispatches to the shared services, and replies with structured
JSON (+ framed binary for ``get_file``). One bad command never takes the service down.

This dispatch logic is **board-agnostic and identical to the N6's** ``n6/main.py`` — the
N6 and AE3 differ only in which ``board_config`` is deployed as ``board_config.py``
(CLAUDE.md §6/§36). It is deliberately kept as a per-board copy (not hoisted into
``openmv/common``) so each board deploys and debugs independently; if these ever need to
diverge, they can, without touching the other board.

Recoverability: the host only ever sends text (JSON) to the board, so the default Ctrl-C
interrupt is left enabled — a raw-REPL break (mpremote / deploy tool) can always regain
control for redeploy. Binary only flows board -> host.
"""

import time

import board_config
import capture_service
import command_protocol as cp
import device_info
import pyb


def _send(usb, message):
    usb.write(cp.encode_message(message))


def _handle_line(usb, line):
    """Handle one command line; always answer with a single structured response."""
    command_id = None
    try:
        request = cp.decode_message(line)
        # Read command_id first so failures can still echo it.
        command_id = request.get("command_id")
        action, command_id, settings = cp.validate_request(request)

        if action == "get_device_info":
            _send(usb, cp.completed_response(command_id, device_info.build(board_config)))
        elif action == "capture_image":
            output = capture_service.capture_image(board_config, settings)
            _send(usb, cp.completed_response(command_id, output))
        elif action == "get_file":
            # send_file emits its own framed response(s).
            capture_service.send_file(usb, command_id, settings.get("filename"))
        elif action == "start_stream":
            # Blocks in a focus-stream loop until the host sends any byte (§ focus stream).
            capture_service.stream_frames(usb, command_id, board_config, settings)
        else:  # pragma: no cover - validate_request already gates the allowlist
            _send(usb, cp.failed_response(command_id, cp.ERR_UNKNOWN_ACTION, action))
    except cp.ProtocolError as exc:
        _send(usb, cp.failed_response(command_id, exc.code, exc.message))
    except Exception as exc:  # fail loudly but keep serving (CLAUDE.md §17)
        _send(usb, cp.failed_response(command_id, cp.ERR_IO_ERROR, str(exc)))


def run():
    """Read-dispatch loop. Accumulates bytes and processes complete newline lines."""
    usb = pyb.USB_VCP()
    buffer = b""
    while True:
        pending = usb.any()
        if pending:
            buffer += usb.read(pending)
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line.strip():
                    _handle_line(usb, line)
        else:
            time.sleep_ms(5)


if __name__ == "__main__":
    run()
