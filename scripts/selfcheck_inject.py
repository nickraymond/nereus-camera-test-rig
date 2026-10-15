#!/usr/bin/env python3
"""selfcheck_inject.py — BENCH ONLY: inject (and clear) one camera fault for the --self-check
fault-injection test (docs/selfcheck_20261015/PREREG.md). Never used by the pool loop.

The rig is sealed in its housing, so a cable cannot be pulled. Instead:
  detach <n6|ae3>   "unplugged": usbfs USBDEVFS_DISCONNECT releases the board's CDC serial
                    interface, so its /dev/ttyACM* and /dev/serial/by-id link vanish exactly as
                    on an unplug. No root: Nick's udev rule gives plugdev rw on OpenMV (37c5)
                    USB nodes. The board itself keeps running.
  attach <n6|ae3>   undo: USBDEVFS_CONNECT lets cdc_acm bind again (the link comes back).
  repl <n6|ae3>     "wedged": mpremote interrupts main.py and leaves the board at its REPL, so
                    it no longer answers get_device_info (the 10/14 AE3 wedge's symptom).
  reset <n6|ae3>    undo: mpremote reset (main.py runs again; verified on both boards 10/15).
  hold-imx          "IMX708 in use": a background `rpicam-hello -n -t 0` holds the camera.
  release-imx       undo: stop that holder (pid file).
  status            which faults look active (by-id links, holder pid).

Verified 2026-10-15 ~3:47 PM on nereus002 (N6): detach rc 0, the link vanished; attach, the
link was back in 2 s and board_probe answered ok.
Usage (on the Pi): ~/nereus-camera-test-rig/.venv/bin/python scripts/selfcheck_inject.py detach ae3
"""

from __future__ import annotations

import fcntl
import os
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board_probe  # noqa: E402

SERIALS = {"n6": "10003500025043364d343000", "ae3": "0829c14000000000"}
USBDEVFS_IOCTL = 0xC0105512   # _IOWR('U', 18, struct usbdevfs_ioctl) on 64-bit (16 bytes)
USBDEVFS_DISCONNECT = 0x5516  # _IO('U', 22)
USBDEVFS_CONNECT = 0x5517     # _IO('U', 23)
CDC_IFNO = 0                  # the CDC comm interface; cdc_acm then releases/claims data (1)
MPREMOTE = os.path.expanduser("~/nereus-camera-test-rig/.venv/bin/mpremote")
HOLD_PID = Path("/tmp/selfcheck_inject_imx.pid")


def _usb_port(board: str) -> str:
    dev = board_probe.usb_device(SERIALS[board])
    if not dev:
        sys.exit(f"{board}: not on USB")
    return dev["port"]


def _usbfs(board: str, code: int) -> int:
    port = _usb_port(board)
    base = Path("/sys/bus/usb/devices") / port
    node = "/dev/bus/usb/%03d/%03d" % (int((base / "busnum").read_text()),
                                       int((base / "devnum").read_text()))
    fd = os.open(node, os.O_RDWR)
    try:
        return fcntl.ioctl(fd, USBDEVFS_IOCTL, bytearray(struct.pack("iiP", CDC_IFNO, code, 0)))
    finally:
        os.close(fd)


def _wait_link(board: str, present: bool, timeout_s: float = 10.0) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        if (board_probe.by_id_port(SERIALS[board]) is not None) == present:
            return True
        time.sleep(0.25)
    return False


def _port(board: str) -> str:
    p = board_probe.by_id_port(SERIALS[board])
    if not p:
        sys.exit(f"{board}: no by-id port")
    return p


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, board = argv[0], (argv[1] if len(argv) > 1 else None)
    if cmd in ("detach", "attach", "repl", "reset") and board not in SERIALS:
        sys.exit("board must be n6 or ae3")
    if cmd == "detach":
        _usbfs(board, USBDEVFS_DISCONNECT)
        ok = _wait_link(board, present=False)
        print(f"detach {board}: link {'gone' if ok else 'STILL PRESENT'}")
        return 0 if ok else 1
    if cmd == "attach":
        try:
            _usbfs(board, USBDEVFS_CONNECT)
        except OSError as exc:  # EBUSY = already bound
            print(f"attach {board}: {exc}")
        ok = _wait_link(board, present=True)
        print(f"attach {board}: link {'back' if ok else 'MISSING'}")
        return 0 if ok else 1
    if cmd in ("repl", "reset"):
        mp = [MPREMOTE, "connect", _port(board)] + (["exec", "pass"] if cmd == "repl"
                                                     else ["reset"])
        rc = subprocess.run(mp, timeout=60).returncode
        if cmd == "reset":
            time.sleep(1)
            ok = _wait_link(board, present=True, timeout_s=20)
            print(f"reset {board}: rc {rc}, link {'back' if ok else 'MISSING'}")
            return 0 if rc == 0 and ok else 1
        print(f"repl {board}: rc {rc} (board left at its REPL)")
        return rc
    if cmd == "hold-imx":
        p = subprocess.Popen(["rpicam-hello", "-n", "-t", "0"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        HOLD_PID.write_text(str(p.pid))
        time.sleep(3)
        alive = p.poll() is None
        print(f"hold-imx: pid {p.pid} {'holding' if alive else 'EXITED rc=%s' % p.returncode}")
        return 0 if alive else 1
    if cmd == "release-imx":
        try:
            pid = int(HOLD_PID.read_text())
            os.killpg(pid, signal.SIGTERM)
            time.sleep(2)
        except (OSError, ValueError) as exc:
            print(f"release-imx: {exc}")
        HOLD_PID.unlink(missing_ok=True)
        print("release-imx: done")
        return 0
    if cmd == "status":
        for b, s in SERIALS.items():
            print(f"{b}: link {'present' if board_probe.by_id_port(s) else 'ABSENT'}")
        print(f"imx holder: {HOLD_PID.read_text() if HOLD_PID.exists() else 'none'}")
        return 0
    sys.exit(f"unknown command {cmd}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
