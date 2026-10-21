#!/usr/bin/env python3
"""pool_selfcheck.py — the boot camera self-check behind `pool_capture.py run --self-check`.

Purpose (Nick, 10/15 ~3:40 PM, plan A): at switch-on the rig checks that all three cameras work
and shows the verdict on the LiFePO4wered/Pi+ LED, his only UI underwater. It does NOT fix
anything: no power cycle, no USB reset, no retries. On a fault Nick switches off and on.

What "working" means here (read-only; nothing is captured or written to a board):
  - openmv_n6 / openmv_ae3: the board's /dev/serial/by-id port is present (waiting up to
    BOARD_WAIT_S for a late enumeration, like the loop's own wait_for_boards) and the board
    answers one ``get_device_info`` (scripts/board_probe.first_contact: the same first-contact
    probe the start/stop gate ran on 119/119 cold boots on 10/15). That catches an unplugged or
    dropped board ("absent") and the 10/14 AE3 wedge ("write_timeout" / "no_reply").
  - imx708: libcamera lists it (`rpicam-hello --list-cameras`), and the camera can be opened
    for a short preview-less stream (`rpicam-hello -n -t 300`). That catches an absent sensor
    and a camera held by another process ("Pipeline handler in use").
Takes ~5-25 s. The loop then starts set 1 exactly as without the flag.

Camera numbers (the LED fault code is N quick flashes): 1 = IMX708, 2 = N6, 3 = AE3.

Inputs: the board serials (hil_soak.SERIALS). Outputs: one JSON-able dict (run_manifest
"self_check" + one pool.log line). Limitation: a camera that answers but then fails to capture
is not flagged by this check (0 such cases in the 10/15 gates: 119/119 contacts ok and every
still verified).

CLI (bench, on the Pi; prints the dict):
    ~/nereus-camera-test-rig/.venv/bin/python scripts/pool_selfcheck.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# board_probe (and through it openmv/common) is imported lazily inside check_board: importing
# pool_capture must never fail because of the self-check (the loop has to start regardless).

CAMERA_CODES = {"imx708": 1, "openmv_n6": 2, "openmv_ae3": 3}
BOARD_WAIT_S = 20.0           # same bound as the loop's wait_for_boards before a set
IMX_OPEN_MS = 300             # short preview-less stream: proves the pipeline can be acquired
IMX_TIMEOUT_S = 20.0


def check_board(serial: str, wait_s: float = BOARD_WAIT_S, probe=None, port_of=None,
                sleep=time.sleep) -> dict:
    """Present on USB (≤ wait_s) and answers get_device_info. {"ok", "why", ...}."""
    if probe is None or port_of is None:
        import board_probe  # noqa: E402  (first_contact, by_id_port)
        probe = probe or board_probe.first_contact
        port_of = port_of or board_probe.by_id_port
    t0 = time.monotonic()
    while port_of(serial) is None and time.monotonic() - t0 < wait_s:
        sleep(1.0)
    rec = probe(serial)
    why = "ok" if rec.get("ok") else rec.get("stage", "?")
    if why == "replied":  # a reply that is not "completed", e.g. a board at its REPL echoing
        why = "bad_reply"  # the request back (bench 10/15): not an OK, so say so
    out = {"ok": bool(rec.get("ok")), "why": why, "seconds": round(time.monotonic() - t0, 2)}
    for k in ("error", "write_s", "reply_s", "pending_bytes"):
        if k in rec:
            out[k] = rec[k]
    return out


def _run(cmd: list[str], timeout: float) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        return -9, "timeout"
    except OSError as exc:
        return -1, str(exc)


def check_imx708(run=_run) -> dict:
    """Listed by libcamera and openable. {"ok", "why", ...}."""
    t0 = time.monotonic()
    rc, out = run(["rpicam-hello", "--list-cameras"], IMX_TIMEOUT_S)
    if rc != 0 or "imx708" not in out:
        return {"ok": False, "why": "not_listed", "rc": rc, "tail": out.strip()[-200:],
                "seconds": round(time.monotonic() - t0, 2)}
    rc, out = run(["rpicam-hello", "-n", "-t", str(IMX_OPEN_MS)], IMX_TIMEOUT_S)
    if rc != 0:
        why = "in_use" if "in use" in out.lower() else "open_failed"
        return {"ok": False, "why": why, "rc": rc, "tail": out.strip()[-200:],
                "seconds": round(time.monotonic() - t0, 2)}
    return {"ok": True, "why": "ok", "seconds": round(time.monotonic() - t0, 2)}


def run_check(serials: dict, board_check=check_board, imx_check=check_imx708) -> dict:
    """All three, IMX708 first (camera 1), then N6 (2), AE3 (3). Never raises."""
    t0 = time.monotonic()
    cams: dict = {}
    for cam in CAMERA_CODES:
        try:
            cams[cam] = imx_check() if cam == "imx708" else board_check(serials[cam])
        except Exception as exc:  # noqa: BLE001 - a check that crashes counts as a fault
            cams[cam] = {"ok": False, "why": "check_error", "error": repr(exc)[:200]}
    faults = [c for c in CAMERA_CODES if not cams[c]["ok"]]
    return {"ok": not faults, "cameras": cams, "faults": faults,
            "codes": [CAMERA_CODES[c] for c in faults],
            "seconds": round(time.monotonic() - t0, 2)}


def led_mode(result: dict) -> str:
    """The LedDriver mode for a verdict: "verdict_ok" or "fault:<codes>" (e.g. "fault:2,3")."""
    return "verdict_ok" if result["ok"] else "fault:" + ",".join(map(str, result["codes"]))


def verdict_led_mode(verdict_mode: str, low_battery: bool, recording: bool = False) -> str:
    """The LED mode: low battery (Nick's B) > the boot verdict. An OK verdict plays its blinks
    once and then holds steady (the driver times that, pool_led.OK_BLINKS), and pulses while a
    clip records (Nick 10/20); a fault code stays until switch-off, recording or not."""
    if low_battery:
        return "lowbatt"
    if verdict_mode == "verdict_ok" and recording:
        return "recording"
    return verdict_mode


def summary(result: dict) -> str:
    """The pool.log line."""
    if result["ok"]:
        return (f"SELF-CHECK OK 3/3 ({result['seconds']:.0f} s) -> LED slow blink "
                "(2 s on / 2 s off) x5, then steady; pulsing while a clip records")
    parts = [f"{c} ({result['cameras'][c]['why']})" for c in result["faults"]]
    codes = " then ".join(f"{n} flashes" for n in result["codes"])
    return (f"SELF-CHECK FAULT {3 - len(result['faults'])}/3 ok: {', '.join(parts)} "
            f"({result['seconds']:.0f} s) -> LED code {codes} + dark, repeating")


def main() -> int:
    from hil_soak import SERIALS  # noqa: E402  (reuse, not a copy)
    result = run_check(SERIALS)
    print(json.dumps(result, indent=2))
    print(summary(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
