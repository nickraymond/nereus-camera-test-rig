"""OpenMV AE3 capture service entry point — Spec §7, §8.

Auto-runs at boot (MicroPython runs ``main.py`` after ``boot.py``). Reads
newline-delimited JSON commands from USB, validates each against the allowlist,
dispatches to the shared services, and replies with structured JSON (+ framed binary for
``get_file``). One bad command never takes the service down.

**Board difference from the N6 (verified on hardware 2026-07-15).** The AE3 is an Alif
Ensemble part; its firmware has **no ``pyb`` module**, so ``pyb.USB_VCP()`` (used by the
N6) is unavailable *on any AE3 firmware* — ``pyb`` is STM32-specific. The AE3's USB CDC is
reached instead through the standard MicroPython console streams ``sys.stdin.buffer`` /
``sys.stdout.buffer`` with a ``select.poll`` for non-blocking reads. ``_UsbVcp`` below
wraps those to expose the same ``any()`` / ``read(n)`` / ``write(bytes)`` interface the
shared ``capture_service`` and the dispatch loop expect — so this is the *only*
board-specific code, and the shared services stay untouched (CLAUDE.md §6/§36).

The dispatch logic (``_handle_line``) is intentionally identical to the N6's; only the USB
acquisition differs.

Recoverability: the console keyboard-interrupt is left at its default (enabled), so a
Ctrl-C from mpremote / the deploy tool raises ``KeyboardInterrupt`` (a ``BaseException``,
not caught by ``except Exception`` below), drops out of ``run()`` to the REPL, and lets a
redeploy regain control. The host protocol is JSON text and never sends 0x03. Binary only
flows board -> host.
"""

import select
import sys
import time

import board_config
import capture_service
import command_protocol as cp
import device_info


class _UsbVcp:
    """USB CDC shim exposing a ``pyb.USB_VCP``-like interface over the console streams.

    ``any()`` reports whether at least one byte is readable (non-blocking, via poll);
    ``read(n)`` returns up to ``n`` currently-available bytes without blocking; ``write``
    sends raw bytes to the host, looping over partial writes so full JPEG payloads are
    delivered intact (§10). Reads go one byte at a time guarded by the poll so a read can
    never block the service — command lines are short, and the outer loop drains quickly.
    """

    def __init__(self):
        self._in = sys.stdin.buffer
        self._out = sys.stdout.buffer
        self._poll = select.poll()
        self._poll.register(self._in, select.POLLIN)

    def any(self):
        return 1 if self._poll.poll(0) else 0

    def read(self, n):
        out = bytearray()
        while len(out) < n and self._poll.poll(0):
            b = self._in.read(1)
            if not b:
                break
            out += b
        return bytes(out)

    def write(self, data):
        mv = memoryview(data)
        total = 0
        n = len(mv)
        while total < n:
            w = self._out.write(mv[total:])
            if w:
                total += w
            # w is None/0 when the CDC would block; retry until the host drains it.


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
        elif action == "reset_board":
            # Acks then machine.reset()s — never returns; the board reboots into this
            # service with fresh firmware 3A state (stale-AWB hazard, capture_service).
            capture_service.reset_board(usb, command_id)
        else:  # pragma: no cover - validate_request already gates the allowlist
            _send(usb, cp.failed_response(command_id, cp.ERR_UNKNOWN_ACTION, action))
    except cp.ProtocolError as exc:
        _send(usb, cp.failed_response(command_id, exc.code, exc.message))
    except Exception as exc:  # fail loudly but keep serving (CLAUDE.md §17)
        _send(usb, cp.failed_response(command_id, cp.ERR_IO_ERROR, str(exc)))


def run():
    """Read-dispatch loop. Accumulates bytes and processes complete newline lines."""
    usb = _UsbVcp()
    buffer = b""
    while True:
        chunk = usb.read(256)
        if chunk:
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line.strip():
                    _handle_line(usb, line)
        else:
            time.sleep_ms(5)


if __name__ == "__main__":
    run()
