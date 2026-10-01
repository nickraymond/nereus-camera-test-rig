"""Run the OpenMV compression probe on one board, from the Pi, safely (Phase 2).

    python -m compression_study.phase2.pi_run_probe --board n6 --serial 0200... --out DIR

Rig-safety rules from the review (the owner is away, nobody can replug a board):
- Refuses to start while the workbench runs a recipe or a HIL soak is running.
- Counts kernel USB re-enumerations and ``error -71`` before and after; any new -71, or a
  board that does not answer the rig handshake after the reset, marks the board ``stop`` for
  the night (written to ``<out>/<board>_status.json``) — no retry loops, no USB resets.
- ``mpremote run`` streams results over the console (nothing written to /flash); then
  ``mpremote reset`` restores the rig service, then a ``get_device_info`` handshake.
- AE3: reset before the probe and wait 35 s between mpremote sessions.
- The UPS (LiFePO4wered) output voltage × current is sampled every ~0.25 s for energy.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

MPREMOTE = str(Path.home() / "nereus-camera-test-rig/.venv/bin/mpremote")
PROBE = REPO / "openmv/probes/compress_probe_v5.py"
PORTS = {
    "n6": "/dev/serial/by-id/usb-MicroPython_Pyboard_Virtual_Comm_Port_in_FS_Mode_{s}-if00",
    "ae3": "/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_{s}-if00",
}
AE3_SETTLE_S = 35


def kernel_usb_counts() -> dict:
    out = subprocess.run(["journalctl", "-k", "--since", "-24h", "--no-pager"],
                         capture_output=True, text=True).stdout
    return {"usb_1_1_lines": out.count("usb 1-1"), "error_71": out.count("error -71"),
            "new_device": out.count("New USB device found")}


def guard_idle() -> None:
    try:
        st = json.load(urllib.request.urlopen("http://localhost:8088/api/runner", timeout=3))
        if st.get("state") not in ("idle", None):
            raise SystemExit(f"workbench is {st.get('state')} ({st.get('recipe')}): not touching "
                             "the boards")
    except OSError:
        pass  # workbench not running at all: fine
    soak = subprocess.run(["pgrep", "-f", "[h]il_soak"], capture_output=True, text=True)
    if soak.stdout.strip():
        raise SystemExit("a HIL soak is running: not touching the boards")


class PowerSampler(threading.Thread):
    def __init__(self, period: float = 0.25):
        super().__init__(daemon=True)
        self.period, self.samples, self.stop_flag = period, [], False

    @staticmethod
    def _get(name: str):
        r = subprocess.run(["lifepo4wered-cli", "get", name], capture_output=True, text=True)
        try:
            return int(r.stdout.strip())
        except ValueError:
            return None

    def run(self):
        while not self.stop_flag:
            t = time.time()
            v, i = self._get("VOUT"), self._get("IOUT")
            self.samples.append((t, v, i))
            time.sleep(max(0.0, self.period - (time.time() - t)))


def mp(port: str, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run([MPREMOTE, "connect", port, *args], capture_output=True, text=True,
                          timeout=timeout)


def handshake(serial: str, board: str) -> dict:
    from nereus_camera_test_rig.cameras.openmv_usb import OpenMvUsbCamera

    cam = OpenMvUsbCamera(serial_number=serial, board=board)
    try:
        return cam.get_device_info()
    finally:
        cam.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", choices=("n6", "ae3"), required=True)
    ap.add_argument("--serial", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    port = PORTS[args.board].format(s=args.serial)
    status = {"board": args.board, "serial": args.serial, "port": port, "steps": []}
    status_path = args.out / f"{args.board}_status.json"

    def step(name, **kw):
        status["steps"].append({"t": time.time(), "step": name, **kw})
        status_path.write_text(json.dumps(status, indent=1))
        print(f"[{args.board}] {name} {kw}", flush=True)

    guard_idle()
    before = kernel_usb_counts()
    step("usb_before", **before)
    if not Path(port).exists():
        step("stop", reason="port missing before start")
        return 2
    if args.board == "ae3":
        r = mp(port, "reset")
        step("ae3_reset_before", rc=r.returncode)
        time.sleep(AE3_SETTLE_S)
    power = PowerSampler()
    power.start()
    time.sleep(5)  # idle baseline
    lines = []
    t0 = time.time()
    proc = subprocess.Popen([MPREMOTE, "connect", port, "run", str(PROBE)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            lines.append((time.time(), line.rstrip("\n")))
            if time.time() - t0 > 400:
                proc.kill()
                lines.append((time.time(), "#E host timeout 400 s"))
                break
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    time.sleep(3)
    power.stop_flag = True
    power.join(timeout=5)
    step("probe_done", rc=proc.returncode, lines=len(lines), seconds=round(time.time() - t0, 1))
    with (args.out / f"{args.board}_probe.jsonl").open("w") as fh:
        for t, line in lines:
            fh.write(json.dumps({"t": t, "line": line}) + "\n")
    (args.out / f"{args.board}_power.json").write_text(json.dumps(power.samples))
    # restore the rig service, then prove it answers
    if args.board == "ae3":
        time.sleep(AE3_SETTLE_S)
    r = mp(port, "reset")
    step("reset_after", rc=r.returncode, err=r.stderr[-200:])
    time.sleep(8)
    try:
        info = handshake(args.serial, args.board)
        step("handshake", ok=True, firmware=info.get("firmware"),
             flash_free=info.get("flash_free_bytes"))
    except Exception as exc:  # noqa: BLE001 — record and stop, never retry
        step("handshake", ok=False, error=f"{type(exc).__name__}: {exc}")
    after = kernel_usb_counts()
    step("usb_after", **after)
    bad = after["error_71"] > before["error_71"] or not status["steps"][-2].get("ok")
    step("stop" if bad else "ok", new_enumerations=after["new_device"] - before["new_device"])
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
