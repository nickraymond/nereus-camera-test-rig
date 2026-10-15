#!/usr/bin/env python3
"""board_probe.py — first-contact probe + recovery ladder for the OpenMV boards (N6 / AE3).

Purpose: see exactly how a board answers its first command after a cold boot, and recover a
  board that does not, without a power cycle. Built for the AE3 cold-boot wedge (pool007,
  2026-10-14: the AE3 enumerated, its USB control endpoint answered all run, but its CDC
  never accepted a byte — every host write timed out). See docs/rig_gate_20261014/PREREG.md.

First contact (observation only, before any capture):
  1. open the by-id port and read whatever the board already sent (1 s): a traceback or a REPL
     banner here means main.py is not serving;
  2. write ``get_device_info`` with a short write timeout (3 s) — a timeout here IS the wedge;
  3. read the reply (5 s). The reply carries the board's uptime and boot timings (main.py).

Recovery ladder (only for a board that failed first contact; each step logged, stops at the
  first step after which the board answers):
  L1 DTR off/on (does the board's USB control endpoint answer?), then retry;
  L2 close + reopen the port, then retry;
  L3 USB port reset (USBDEVFS_RESET on /dev/bus/usb/BBB/DDD — no root: Nick's udev rule gives
     plugdev rw on OpenMV nodes), then retry. A healthy AE3 survives this 5/5, back in ~1.1 s
     (measured 2026-10-14 7:35 PM). The old rule "never USB-reset the AE3" came from boards
     that had already crashed hard (they dropped off the bus, error -71): a board in that state
     needs a power cycle whatever we do, so L3 cannot make it worse;
  L4 Ctrl-C (does a MicroPython VM answer?) and, if a REPL appears, ``machine.reset()``, then
     retry.

Usage (on the Pi, from the repo root):
  python scripts/board_probe.py probe 0829c14000000000            # first contact only
  python scripts/board_probe.py recover 0829c14000000000          # probe, ladder on failure
  python scripts/board_probe.py usbreset 0829c14000000000         # L3 alone
Output: one JSON object on stdout. Exit 0 = the board answers, 1 = it does not.

Limits: Linux only (sysfs + USBDEVFS_RESET). Never sends a capture: no camera session is used,
  so the AE3's one-camera-session-per-boot limit (OQ-19) is not touched.
"""

from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openmv.common import command_protocol as cp  # noqa: E402

USBDEVFS_RESET = 0x5514          # _IO('U', 20), linux/usbdevice_fs.h
BAUD = 115200
PENDING_S = 1.0
WRITE_TIMEOUT_S = 3.0
REPLY_TIMEOUT_S = 5.0
REENUM_TIMEOUT_S = 15.0


def by_id_port(serial_number: str) -> str | None:
    """The board's CDC tty via /dev/serial/by-id (never a fixed ttyACM number, §12)."""
    hits = sorted(glob.glob(f"/dev/serial/by-id/*{serial_number}*-if00"))
    return hits[0] if hits else None


def usb_device(serial_number: str) -> dict | None:
    """sysfs facts for the board's USB device: port name (e.g. 1-1.2), bus/dev numbers."""
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            if Path(d, "serial").read_text().strip() != serial_number:
                continue
            return {"port": Path(d).name, "busnum": int(Path(d, "busnum").read_text()),
                    "devnum": int(Path(d, "devnum").read_text()),
                    "product": Path(d, "idProduct").read_text().strip()}
        except OSError:
            continue
    return None


def _pi_uptime() -> float | None:
    """Pi uptime (s): compared with the board's own ticks_ms it shows whether the board lost
    power when the Pi did (a board that kept power has been up far longer)."""
    try:
        return round(float(Path("/proc/uptime").read_text().split()[0]), 1)
    except (OSError, ValueError, IndexError):
        return None


def _open(port: str):
    import serial

    return serial.Serial(port, BAUD, timeout=0.1, write_timeout=WRITE_TIMEOUT_S)


def _read_for(ser, seconds: float) -> bytes:
    out = bytearray()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        chunk = ser.read(4096)
        if chunk:
            out += chunk
    return bytes(out)


def first_contact(serial_number: str, pending_s: float = PENDING_S,
                  reply_timeout_s: float = REPLY_TIMEOUT_S) -> dict:
    """One ``get_device_info`` exchange, timed, with whatever the board had already sent."""
    t0 = time.monotonic()
    rec: dict = {"ok": False, "port": by_id_port(serial_number), "usb": usb_device(serial_number),
                 "pi_uptime_s": _pi_uptime()}
    if not rec["port"]:
        rec["stage"] = "absent"
        return rec
    try:
        ser = _open(rec["port"])
    except OSError as exc:
        rec.update(stage="open_failed", error=str(exc))
        return rec
    try:
        pending = _read_for(ser, pending_s) if pending_s > 0 else b""
        rec["pending_bytes"] = len(pending)
        if pending:
            rec["pending_head"] = pending[:300].decode("utf-8", "replace")
        command_id = uuid.uuid4().hex[:12]
        t_w = time.monotonic()
        try:
            ser.write(cp.encode_message(cp.make_request("get_device_info", command_id)))
            rec["write_ok"] = True
        except OSError as exc:  # pyserial SerialTimeoutException: the board takes no input
            rec.update(write_ok=False, stage="write_timeout", error=str(exc),
                       write_s=round(time.monotonic() - t_w, 2))
            return rec
        rec["write_s"] = round(time.monotonic() - t_w, 3)
        buf = bytearray()
        console: list[str] = []
        deadline = time.monotonic() + reply_timeout_s
        while time.monotonic() < deadline:
            buf += ser.read(4096)
            while b"\n" in buf:
                line, _, rest = bytes(buf).partition(b"\n")
                buf = bytearray(rest)
                text = line.decode("utf-8", "replace").strip()
                if text.startswith("{"):
                    try:
                        msg = cp.decode_message(line)
                    except Exception:  # noqa: BLE001 - a garbled line is just console noise
                        console.append(text[:200])
                        continue
                    if msg.get("command_id") == command_id:
                        rec.update(ok=msg.get("status") == "completed", stage="replied",
                                   reply_s=round(time.monotonic() - t_w, 3),
                                   info=msg.get("output") or {}, console=console[-5:])
                        return rec
                elif text:
                    console.append(text[:200])
        rec.update(stage="no_reply", console=console[-5:],
                   tail=bytes(buf[-200:]).decode("utf-8", "replace"))
        return rec
    finally:
        ser.close()
        rec["seconds"] = round(time.monotonic() - t0, 2)


def dtr_toggle(serial_number: str) -> dict:
    """L1: SET_CONTROL_LINE_STATE twice. An error here means the board's EP0 is dead too."""
    port = by_id_port(serial_number)
    if not port:
        return {"ok": False, "error": "absent"}
    try:
        ser = _open(port)
        ser.dtr = False
        time.sleep(0.2)
        ser.dtr = True
        time.sleep(0.2)
        ser.close()
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def usb_reset(serial_number: str) -> dict:
    """L3: USB port reset of this one board, then wait for its tty to come back."""
    dev = usb_device(serial_number)
    if not dev:
        return {"ok": False, "error": "absent"}
    node = "/dev/bus/usb/%03d/%03d" % (dev["busnum"], dev["devnum"])
    t0 = time.monotonic()
    try:
        fd = os.open(node, os.O_WRONLY)
        try:
            fcntl.ioctl(fd, USBDEVFS_RESET, 0)
        finally:
            os.close(fd)
    except OSError as exc:
        return {"ok": False, "node": node, "error": str(exc)}
    while time.monotonic() - t0 < REENUM_TIMEOUT_S:
        time.sleep(0.3)
        if by_id_port(serial_number) and usb_device(serial_number):
            time.sleep(0.5)  # let cdc_acm finish binding
            return {"ok": True, "node": node, "seconds": round(time.monotonic() - t0, 2),
                    "usb_after": usb_device(serial_number)}
    return {"ok": False, "node": node, "error": "tty did not come back",
            "seconds": round(time.monotonic() - t0, 2)}


def ctrl_c_reset(serial_number: str) -> dict:
    """L4: Ctrl-C; if a REPL answers, machine.reset() so main.py (the service) starts fresh."""
    port = by_id_port(serial_number)
    if not port:
        return {"ok": False, "error": "absent"}
    try:
        ser = _open(port)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        try:
            ser.write(b"\r\x03\x03")
        except OSError as exc:
            return {"ok": False, "stage": "write_timeout", "error": str(exc)}
        text = _read_for(ser, 2.0).decode("utf-8", "replace")
        rec = {"repl": ">>>" in text, "head": text[-300:]}
        if rec["repl"]:
            try:
                ser.write(b"\x02import machine; machine.reset()\r\n")  # Ctrl-B: friendly REPL
            except OSError as exc:
                rec["reset_error"] = str(exc)
        rec["ok"] = rec["repl"]
        return rec
    finally:
        try:
            ser.close()
        except OSError:
            pass


def wait_tty(serial_number: str, timeout_s: float = REENUM_TIMEOUT_S) -> float | None:
    t0 = time.monotonic()
    time.sleep(1.0)  # a board that is resetting is still listed for a moment
    while time.monotonic() - t0 < timeout_s:
        if by_id_port(serial_number):
            time.sleep(1.0)
            return round(time.monotonic() - t0, 2)
        time.sleep(0.3)
    return None


def recover(serial_number: str, first: dict | None = None) -> dict:
    """First contact, then the ladder L1..L4 until the board answers. Returns the full record."""
    first = first or first_contact(serial_number)
    rec = {"serial": serial_number, "first": first, "steps": [], "ok": bool(first.get("ok"))}
    if rec["ok"]:
        return rec
    ladder = [("L1_dtr", dtr_toggle), ("L2_reopen", None), ("L3_usb_reset", usb_reset),
              ("L4_ctrl_c_reset", ctrl_c_reset)]
    for name, fn in ladder:
        t0 = time.monotonic()
        step: dict = {"step": name}
        if fn is not None:
            step["action"] = fn(serial_number)
            if name == "L4_ctrl_c_reset" and step["action"].get("repl"):
                step["tty_back_s"] = wait_tty(serial_number)
        step["retry"] = first_contact(serial_number, pending_s=0.5)
        step["seconds"] = round(time.monotonic() - t0, 2)
        rec["steps"].append(step)
        if step["retry"].get("ok"):
            rec.update(ok=True, recovered_by=name)
            break
    return rec


def summary(rec: dict) -> str:
    """One line for logs: how the board answered and which step (if any) recovered it."""
    f = rec.get("first") or rec
    if f.get("ok"):
        return "ok %.2fs" % (f.get("reply_s") or 0)
    steps = ",".join("%s:%s" % (s["step"].split("_")[0], s["retry"].get("stage"))
                     for s in rec.get("steps", []))
    return "%s -> %s%s" % (f.get("stage"), steps or "-",
                           " RECOVERED by %s" % rec["recovered_by"] if rec.get("ok") else "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=("probe", "recover", "usbreset"))
    ap.add_argument("serial")
    a = ap.parse_args()
    if a.action == "probe":
        rec = first_contact(a.serial)
    elif a.action == "recover":
        rec = recover(a.serial)
    else:
        rec = usb_reset(a.serial)
    print(json.dumps(rec, indent=2))
    return 0 if rec.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
