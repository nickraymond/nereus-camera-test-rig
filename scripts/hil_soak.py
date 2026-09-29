#!/usr/bin/env python3
"""hil_soak.py — hardware-in-the-loop soak: one 3-camera RAW capture set every N minutes.

Run ON the rig Pi from the repo root (foreground loop, not a daemon — SPEC §2; detach it
with ``setsid nohup`` for an overnight run):

    .venv/bin/python scripts/hil_soak.py run [--interval-min 5] [--hours 12]
    .venv/bin/python scripts/hil_soak.py summary results/hil_soak/<UTC>

Each cycle runs ``nereus-rig experiment --raw --no-analysis`` in a **fresh process** (a crash,
hang or leak ends that cycle only; a hard timeout kills a stuck one) into
``results/hil_soak/<UTC>/<date>/exp_…``, then appends one JSON line to ``soak.jsonl``: per
camera the still + RAW status, error, duration, exposure / gain, and the Pi's CPU
temperature, available RAM, free disk, throttle flags and which OpenMV boards are on USB.
Nothing is ever deleted. Stop early with Ctrl-C, ``kill``, or by creating ``<run>/STOP``.
Stop Nick's workbench recipe first (``curl -X POST localhost:8088/api/stop``).

A capture-only rehearsal of the S7 ``nereus-rig soak`` loop (no correction step yet).
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERIALS = {"openmv_n6": "020023000450433547373200", "openmv_ae3": "0829c14000000000"}
CYCLE_TIMEOUT_S = 600  # a healthy cycle takes ~90-110 s on nereus002


def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def pi_health(run_dir: Path) -> dict:
    temp = _read("/sys/class/thermal/thermal_zone0/temp")
    mem = {k: int(v.split()[0]) for k, v in
           (line.split(":", 1) for line in _read("/proc/meminfo").splitlines())
           if k in ("MemAvailable", "MemTotal")}
    try:
        throttled = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                                   text=True, timeout=5).stdout.strip().split("=")[-1]
    except (OSError, subprocess.SubprocessError):
        throttled = None
    by_id = Path("/dev/serial/by-id")
    present = [p.name for p in by_id.iterdir()] if by_id.is_dir() else []
    return {"cpu_temp_c": int(temp) / 1000 if temp else None,
            "mem_available_mb": mem.get("MemAvailable", 0) // 1024,
            "disk_free_gb": round(shutil.disk_usage(run_dir).free / 1e9, 2),
            "throttled": throttled,
            "usb": {cam: any(s in n for n in present) for cam, s in SERIALS.items()}}


def cameras_from_record(exp_dir: Path) -> dict:
    """Per camera: still + RAW status, error, duration, exposure / gain (from experiment.json)."""
    rec = json.loads((exp_dir / "experiment.json").read_text())
    out: dict = {}
    for key, kind in (("captures", "still"), ("raw_captures", "raw")):
        for c in rec.get(key, []):
            cam = (c.get("camera") or {})
            name = {"imx708": "imx708", "n6": "openmv_n6", "ae3": "openmv_ae3"}.get(
                cam.get("board") or cam.get("sensor") or "", cam.get("board") or "?")
            m = c.get("sensor_metadata") or {}
            exposure = m.get("exposure_us") or m.get("ExposureTime")
            gain = m.get("gain_db")
            if gain is None and m.get("AnalogueGain"):
                gain = round(20 * math.log10(m["AnalogueGain"]), 2)
            out.setdefault(name, {})[kind] = {
                "status": c.get("status"), "error": (c.get("error") or {}).get("code"),
                "seconds": round(c.get("duration_seconds") or 0, 1),
                "exposure_us": exposure, "gain_db": gain, "bytes": c.get("size_bytes")}
    return out


def run(args) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = ROOT / "results" / "hil_soak" / stamp
    run_dir.mkdir(parents=True, exist_ok=False)
    log = run_dir / "soak.jsonl"
    end = time.monotonic() + args.hours * 3600
    # This run's own copy of the rig config, writing experiments into the run folder.
    sys.path.insert(0, str(ROOT / "src"))
    import yaml

    from nereus_camera_test_rig import config as config_mod

    cfg = config_mod.load_rig_config(ROOT / args.config)
    cfg.setdefault("rig", {})["results_directory"] = str(run_dir)
    run_config = run_dir / "rig.yaml"
    run_config.write_text(yaml.safe_dump(cfg, sort_keys=False))
    (run_dir / "soak.json").write_text(json.dumps(
        {"started_utc": stamp, "interval_min": args.interval_min, "hours": args.hours,
         "config": args.config, "command": "experiment --raw --no-analysis"}, indent=1) + "\n")
    print(f"HIL soak -> {run_dir} (every {args.interval_min} min for {args.hours} h)", flush=True)
    cycle = 0
    while time.monotonic() < end and not (run_dir / "STOP").exists():
        t0 = time.monotonic()
        cycle += 1
        line: dict = {"cycle": cycle, "utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"), "pi_before": pi_health(run_dir)}
        before = set(run_dir.glob("*/exp_*"))
        cmd = [sys.executable, "-m", "nereus_camera_test_rig.cli", "--config", str(run_config),
               "experiment", "--type", "hil_soak", "--env", "hil-overnight", "--raw",
               "--no-analysis", "--notes", f"hil_soak {stamp} cycle {cycle}"]
        try:
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                  timeout=CYCLE_TIMEOUT_S)
            line["exit_code"] = proc.returncode
            line["stdout_tail"] = proc.stdout.strip().splitlines()[-6:]
            if proc.returncode != 0:  # 1 = partial run; a traceback lands here too
                line["stderr_tail"] = proc.stderr.strip().splitlines()[-12:]
        except subprocess.TimeoutExpired:
            line["exit_code"] = "timeout"
        new = sorted(set(run_dir.glob("*/exp_*")) - before)
        if new:
            line["experiment"] = str(new[-1].relative_to(run_dir))
            try:
                line["cameras"] = cameras_from_record(new[-1])
            except (OSError, ValueError) as exc:
                line["record_error"] = str(exc)
        line["cycle_seconds"] = round(time.monotonic() - t0, 1)
        line["pi_after"] = pi_health(run_dir)
        with log.open("a") as fh:
            fh.write(json.dumps(line) + "\n")
        cams = line.get("cameras", {})
        states = " ".join(f"{k}={v.get('still', {}).get('status', '-')}/"
                          f"{v.get('raw', {}).get('status', '-')}" for k, v in cams.items())
        print(f"[{line['utc']}] cycle {cycle}: exit {line['exit_code']} in "
              f"{line['cycle_seconds']} s · {states}", flush=True)
        wait = args.interval_min * 60 - (time.monotonic() - t0)
        while wait > 0 and not (run_dir / "STOP").exists() and time.monotonic() < end:
            time.sleep(min(wait, 10))
            wait -= 10
    print(f"HIL soak done: {cycle} cycles in {run_dir}", flush=True)
    return 0


def summary(args) -> int:
    lines = [json.loads(s) for s in (Path(args.run_dir) / "soak.jsonl").read_text().splitlines()]
    print(f"{len(lines)} cycles, {lines[0]['utc']} .. {lines[-1]['utc']}")
    names = sorted({c for ln in lines for c in ln.get("cameras", {})})
    for cam in names:
        for kind in ("still", "raw"):
            rows = [ln.get("cameras", {}).get(cam, {}).get(kind) for ln in lines]
            ok = sum(1 for r in rows if r and r["status"] == "completed")
            errs: dict = {}
            for ln, r in zip(lines, rows):
                if not r or r["status"] != "completed":
                    code = (r or {}).get("error") or "missing"
                    errs.setdefault(code, []).append(ln["cycle"])
            secs = [r["seconds"] for r in rows if r and r["status"] == "completed"]
            print(f"  {cam:11s} {kind:5s} {ok}/{len(lines)} ok"
                  + (f", median {sorted(secs)[len(secs) // 2]} s" if secs else "")
                  + "".join(f"; {k} in cycles {v[:8]}{'…' if len(v) > 8 else ''}"
                            for k, v in errs.items()))
    temps = [ln["pi_after"]["cpu_temp_c"] for ln in lines if ln["pi_after"]["cpu_temp_c"]]
    mem = [ln["pi_after"]["mem_available_mb"] for ln in lines]
    print(f"  pi: cpu {min(temps)}–{max(temps)} °C, mem available min {min(mem)} MB, "
          f"disk free {lines[-1]['pi_after']['disk_free_gb']} GB, throttled "
          f"{sorted({ln['pi_after']['throttled'] for ln in lines})}")
    drops = [(ln["cycle"], k) for ln in lines for k, v in ln["pi_after"]["usb"].items() if not v]
    print(f"  usb missing after cycle: {drops[:12] or 'never'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--interval-min", type=float, default=5.0)
    r.add_argument("--hours", type=float, default=12.0)
    r.add_argument("--config", default="configs/rig.example.yaml")
    s = sub.add_parser("summary")
    s.add_argument("run_dir")
    args = ap.parse_args()
    return run(args) if args.cmd == "run" else summary(args)


if __name__ == "__main__":
    sys.exit(main())
