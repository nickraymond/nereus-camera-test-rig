#!/usr/bin/env python3
"""n6_sleep_power.py — N6 power in its low-power states (Nick, 10/23), INA3221 CH1 at 10 Hz.

Runs ``openmv/probes/n6_sleep_probe_v5.py`` on the N6 with ``mpremote run``, time-stamps each ``#P``
phase line on the Pi monotonic clock, and keeps logging after deep sleep until the board's
service is back (rig ``get_device_info``) plus 20 s ("service_idle_after"). Each phase's power =
the mean of the rows from 2 s after its start to 1 s before the next mark (settling excluded).
Reuses n6_power_sweep's logger / window statistics. Card: docs/n6_eval/TEST_CARD_POWER_DOE.md (sleep).

Usage (Pi, repo root): python scripts/n6_sleep_power.py --out ~/n6_eval/sleep_<ts>
Outputs: power.csv, marks.json, phases.csv, mpremote.log. mW is "not validated" (must do 3 pending).
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src"), str(ROOT)]
import ina3221_log as ina  # noqa: E402
import n6_power_sweep as sw  # noqa: E402

PORT = ("/dev/serial/by-id/usb-MicroPython_Pyboard_Virtual_Comm_Port_in_FS_Mode_"
        "020023000450433547373200-if00")


HOLD_S = 20.0  # the probe's HOLD_MS


def phase_windows(marks: list[dict], end: float) -> list[tuple[str, float, float]]:
    """(name, t0, t1) per started phase: start + 2 s to min(next start, start + HOLD_S) - 1 s.

    Capped at the hold, because deep sleep's next mark (the board back on USB) comes after its
    reboot, which must not be counted as sleep."""
    starts = [m for m in marks if m["what"] == "start"]
    out = []
    for i, m in enumerate(starts):
        nxt = starts[i + 1]["t"] if i + 1 < len(starts) else end
        out.append((m["name"], m["t"] + 2.0, min(nxt, m["t"] + HOLD_S) - 1.0))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=False)
    from smbus2 import SMBus

    from nereus_camera_test_rig.cameras.openmv_usb import OpenMvUsbCamera

    rows: list = []
    marks: list = []
    stop = threading.Event()
    with SMBus(1) as bus:
        th = threading.Thread(target=sw.logger, args=(bus, rows, stop), daemon=True)
        th.start()
        p = subprocess.Popen([str(ROOT / ".venv/bin/mpremote"), "connect", PORT, "run",
                              str(ROOT / "openmv/probes/n6_sleep_probe_v5.py")],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        log = []
        for line in p.stdout:
            t = time.monotonic()
            log.append(f"{t:.3f} {line.rstrip()}")
            if line.startswith("#P"):
                parts = line.split(None, 3)
                marks.append({"t": t, "name": parts[1], "what": " ".join(parts[2:]).strip()})
        p.wait()
        t_exit = time.monotonic()
        marks.append({"t": t_exit, "name": "mpremote_exit", "what": f"rc {p.returncode}"})
        # Deep sleep: the board leaves USB; wait for the service to answer again (<= 90 s).
        back = None
        while time.monotonic() - t_exit < 90:
            time.sleep(2)
            try:
                OpenMvUsbCamera("020023000450433547373200").get_device_info()
                back = time.monotonic()
                break
            except Exception:
                continue
        if back:
            marks.append({"t": back, "name": "service_idle_after", "what": "start"})
            time.sleep(20)
            marks.append({"t": time.monotonic(), "name": "end", "what": "end"})
        stop.set()
        th.join(timeout=2)
    end = time.monotonic()
    (out / "mpremote.log").write_text("\n".join(log) + "\n")
    (out / "marks.json").write_text(json.dumps(marks, indent=1))
    with (out / "power.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["mono_s", "bus_v", "shunt_mv", "ma", "mw", "error"])
        w.writeheader()
        w.writerows(rows)
    phases = []
    for name, t0, t1 in phase_windows(marks, end):
        phases.append({"phase": name, "seconds": round(t1 - t0, 1), **sw.window_stats(rows, t0, t1)})
        print(json.dumps(phases[-1]), flush=True)
    with (out / "phases.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["phase", "seconds", "n_power", "v_mean", "ma_mean",
                                          "ma_sd", "mw_mean", "read_errors"], extrasaction="ignore")
        w.writeheader()
        w.writerows(phases)
    print("marks:", [(m["name"], m["what"]) for m in marks], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
