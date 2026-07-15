"""Host tool: discover OpenMV boards on USB — Spec §12, §15.

Enumerates OpenMV boards by **USB identity** (vendor id + serial number), never by a
fixed ``/dev/ttyACM*`` path — Linux renumbers those on reconnect (§12). The stable
handle is the USB serial number, which the host adapter uses to reopen the right board.

Optionally performs a protocol **handshake** (``get_device_info``) so discovery also
reports board/firmware from the running service, not just USB descriptors.

Verified on 2026-07-14: the N6 enumerates as VID:PID ``37c5:1206`` (MicroPython VCP),
serial ``005537493543``, driver ``cdc_acm``.

Usage::

    python -m host_tools.discover_openmv            # list boards (USB descriptors)
    python -m host_tools.discover_openmv --handshake  # also query the capture service
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The OpenMV protocol codec is the single source of truth shared with the board. This
# repo runs from a checkout (openmv/ is not part of the src wheel), so put the repo root
# on sys.path via __file__ rather than relying on the current working directory.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from openmv.common import command_protocol as cp  # noqa: E402

# USB vendor ids used by OpenMV / MicroPython boards. 0x37C5 is the current OpenMV VCP
# (verified on the N6); 0x1209 (pid.codes) covers older OpenMV Cam firmware.
OPENMV_USB_VIDS = (0x37C5, 0x1209)

# Default CDC serial parameters. Baud is nominal for USB CDC (the rate is ignored by the
# device) but pyserial still wants a value.
DEFAULT_BAUD = 115200
DEFAULT_HANDSHAKE_TIMEOUT = 3.0


class SerialUnavailable(RuntimeError):
    """pyserial is not installed. Install the 'serial' extra: pip install -e '.[serial]'."""


def _require_pyserial():
    try:
        import serial  # noqa: F401
        from serial.tools import list_ports
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise SerialUnavailable(
            "pyserial not installed; install the 'serial' extra "
            "(pip install -e '.[serial]')"
        ) from exc
    return list_ports


def discover(vids=OPENMV_USB_VIDS):
    """Return a list of candidate OpenMV boards as plain dicts (no port opened).

    Each entry: ``port``, ``serial_number``, ``vid``, ``pid``, ``product``,
    ``manufacturer``. Sorted by serial number for stable ordering.
    """
    list_ports = _require_pyserial()
    boards = []
    for p in list_ports.comports():
        if p.vid is None or p.vid not in vids:
            continue
        boards.append(
            {
                "port": p.device,
                "serial_number": p.serial_number,
                "vid": p.vid,
                "pid": p.pid,
                "product": p.product,
                "manufacturer": p.manufacturer,
            }
        )
    boards.sort(key=lambda b: (b.get("serial_number") or ""))
    return boards


def find_port(serial_number, vids=OPENMV_USB_VIDS):
    """Resolve a USB ``serial_number`` to its current device path, or ``None``.

    This is the identity-based lookup the host adapter uses instead of a fixed path.
    If ``serial_number`` is falsy and exactly one board is present, return that board's
    port (convenience for single-board rigs).
    """
    boards = discover(vids)
    if serial_number:
        for b in boards:
            if b["serial_number"] == serial_number:
                return b["port"]
        return None
    if len(boards) == 1:
        return boards[0]["port"]
    return None


def handshake(port, timeout=DEFAULT_HANDSHAKE_TIMEOUT):
    """Open ``port`` and query ``get_device_info``; return the device dict or None.

    Best-effort: any serial/timeout/protocol error yields ``None`` so discovery still
    lists the board from its USB descriptors even if the service isn't responding.
    """
    import serial

    try:
        with serial.Serial(port, DEFAULT_BAUD, timeout=timeout) as ser:
            ser.reset_input_buffer()
            ser.write(cp.encode_message(cp.make_request("get_device_info", "discover-0")))
            # Read one JSON line (pyserial's per-read timeout bounds each ser.read()).
            buf = bytearray()
            while b"\n" not in buf:
                chunk = ser.read(256)
                if not chunk:
                    break
                buf += chunk
            if b"\n" not in buf:
                return None
            resp = cp.decode_message(bytes(buf).split(b"\n", 1)[0])
            if resp.get("status") == "completed":
                return resp.get("output")
            return None
    except Exception:
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Discover OpenMV boards on USB (Spec §12).")
    parser.add_argument(
        "--handshake", action="store_true",
        help="also query get_device_info from each board's capture service",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args(argv)

    try:
        boards = discover()
    except SerialUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.handshake:
        for b in boards:
            b["device_info"] = handshake(b["port"])

    if args.json:
        print(json.dumps(boards, indent=2))
        return 0 if boards else 1

    if not boards:
        print("No OpenMV boards found (looked for VID " +
              ", ".join(hex(v) for v in OPENMV_USB_VIDS) + ").")
        return 1
    for b in boards:
        line = "{port}  serial={serial}  {vid:04x}:{pid:04x}  {product}".format(
            port=b["port"], serial=b["serial_number"],
            vid=b["vid"] or 0, pid=b["pid"] or 0, product=b["product"] or "",
        )
        info = b.get("device_info")
        if info:
            line += "  -> board={board} fw={fw}".format(
                board=info.get("board"), fw=info.get("firmware"))
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
