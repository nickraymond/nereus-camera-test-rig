#!/usr/bin/env python3
"""n6_deepsleep_retry.py — one N6 sleep variant per run, with power and USB timing (bench 4, 10/24).

Card: docs/n6_eval/TEST_CARD_BENCH_1024.md bench 4. Board side: openmv/probes/n6_deepsleep_v5.py.
Flow: hard reset (mpremote reset) -> wait for the service -> 10 s baseline -> mpremote run the
variant -> poll the board's /dev/serial/by-id node (gone / back) -> wait for the service -> read
machine.reset_cause() + /flash/ds_log.txt -> 10 s after. INA3221 CH1 at 10 Hz the whole time.
Gives up (exit 3) if the board is not back on USB within 60 s of leaving: the card's stop rule.

Usage (Pi, repo root): python scripts/n6_deepsleep_retry.py official --out ~/n6_eval/ds_<variant>_<ts>
Outputs: power.csv, events.json, summary.json, mpremote.log, board_log.txt.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src"), str(ROOT)]
import n6_power_sweep as sw  # noqa: E402

SERIAL = "020023000450433547373200"
PORT = f"/dev/serial/by-id/usb-MicroPython_Pyboard_Virtual_Comm_Port_in_FS_Mode_{SERIAL}-if00"
MPREMOTE = str(ROOT / ".venv/bin/mpremote")
VARIANTS = ("official", "csi_sleep", "csi_shutdown", "lightsleep")
HOLD_S = 20.0


def mp(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run([MPREMOTE, "connect", PORT, *args], capture_output=True, text=True,
                          timeout=timeout)


def wait_service(timeout: float = 30) -> float | None:
    from nereus_camera_test_rig.cameras.openmv_usb import OpenMvUsbCamera

    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            OpenMvUsbCamera(SERIAL).get_device_info()
            return time.monotonic()
        except Exception:
            time.sleep(1)
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("variant", choices=VARIANTS)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=False)
    from smbus2 import SMBus

    probe = out / "probe.py"
    probe.write_text(f'VARIANT = "{a.variant}"\n' + (ROOT / "openmv/probes/n6_deepsleep_v5.py").read_text())
    ev: list = []

    def mark(name, **kw):
        ev.append({"t": time.monotonic(), "event": name, **kw})
        print(json.dumps(ev[-1]), flush=True)

    rows: list = []
    stop = threading.Event()
    rc = 0
    with SMBus(1) as bus:
        th = threading.Thread(target=sw.logger, args=(bus, rows, stop), daemon=True)
        th.start()
        mp("exec", "import os\ntry:\n os.remove('/flash/ds_log.txt')\nexcept OSError:\n pass")
        mp("reset")
        mark("reset_sent")
        if wait_service() is None:
            mark("service_not_back_after_reset")
            rc = 2
        else:
            mark("baseline_start")
            time.sleep(10)
            mark("run_start")
            p = subprocess.Popen([MPREMOTE, "connect", PORT, "run", str(probe)],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            t_gone = t_back = None
            t_start = time.monotonic()
            while time.monotonic() - t_start < 120:
                present = os.path.exists(PORT)
                if t_gone is None and not present:
                    t_gone = time.monotonic()
                    mark("usb_gone")
                if t_gone is not None and present:
                    t_back = time.monotonic()
                    mark("usb_back")
                    break
                if t_gone is None and p.poll() is not None and a.variant == "lightsleep" \
                        and time.monotonic() - t_start > HOLD_S + 5:
                    break  # lightsleep may keep USB: the run simply ends
                if t_gone is not None and time.monotonic() - t_gone > 60:
                    mark("STUCK_not_back_60s")
                    rc = 3
                    break
                time.sleep(0.2)
            try:
                txt, _ = p.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                txt, _ = p.communicate()
            (out / "mpremote.log").write_text(txt or "")
            if rc == 0:
                t_svc = wait_service(40)
                mark("service_back" if t_svc else "service_not_back")
                r = mp("exec", "import machine\nprint('RC', machine.reset_cause())\n"
                       "print(open('/flash/ds_log.txt').read())")
                (out / "board_log.txt").write_text(r.stdout + r.stderr)
                mp("reset")
                wait_service()
                mark("after_start")
                time.sleep(10)
                mark("end")
        stop.set()
        th.join(timeout=2)
    with (out / "power.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["mono_s", "bus_v", "shunt_mv", "ma", "mw", "error"])
        w.writeheader()
        w.writerows(rows)
    (out / "events.json").write_text(json.dumps(ev, indent=1))
    t = {e["event"]: e["t"] for e in ev}
    summary = {"variant": a.variant, "rc": rc}
    if "baseline_start" in t:
        summary["baseline"] = sw.window_stats(rows, t["baseline_start"] + 1, t["run_start"] - 0.5)
    if "usb_gone" in t:
        summary["sleep"] = sw.window_stats(rows, t["usb_gone"] + 2, t["usb_gone"] + HOLD_S - 1)
        if "usb_back" in t:
            summary["usb_gone_s"] = round(t["usb_back"] - t["usb_gone"], 2)
    elif a.variant == "lightsleep":
        # No USB drop seen: measure from the run start + 3 s for the hold (the log says when it woke).
        summary["sleep_assumed_window"] = sw.window_stats(rows, t["run_start"] + 3, t["run_start"] + HOLD_S)
    if "after_start" in t:
        summary["after"] = sw.window_stats(rows, t["after_start"] + 1, t["end"])
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
