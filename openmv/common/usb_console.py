"""USB CDC over the MicroPython console streams — shared by the N6 and AE3 services.

A ``pyb.USB_VCP``-like object (``any()`` / ``read(n)`` / ``write(bytes)``) built on
``sys.stdin.buffer`` / ``sys.stdout.buffer`` with ``select.poll`` for non-blocking reads.

- **AE3:** the Alif firmware has no ``pyb`` module on any version (verified 2026-07-15).
- **N6 on OpenMV v5.0.1** (MicroPython 1.28, ``nereus002`` 2026-09-28): ``pyb`` still
  imports but has **no ``USB_VCP``**, so the N6 service falls back to this class there.

Recoverability: Ctrl-C (0x03) is left enabled, so mpremote / the deploy tool can always
break into the REPL. The host protocol is JSON text and never sends 0x03; binary only
flows board -> host.
"""

import select
import sys


class UsbConsole:
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
