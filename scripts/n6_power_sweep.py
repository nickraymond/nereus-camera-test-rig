#!/usr/bin/env python3
"""n6_power_sweep.py — N6 power DOE: framesize x on-board AprilTags x fps (Nick, 10/23).

Purpose: Nick's matrix on nereus001's spare N6 — {HD, VGA} x {tags off, tag25h9 on the N6} x
  {10, 15, 30} fps asked — with the INA3221 CH1 (the N6's 5 V) logged at 10 Hz the whole time.
  Card: docs/n6_eval/TEST_CARD_POWER_DOE.md.

How (reused, not new): each streaming cell is one ``scripts/sync_video.py`` clip of the N6 alone
  (``--apriltags --tag-family TAG25H9 --meta --timing`` when tags are on); the power rows come from
  ``scripts/ina3221_log.read_channel`` in a thread of this process (read-only registers). A cell's
  window = its first to last frame on the Pi monotonic clock (frames.csv ``host_rx_mono``), so the
  board reset and warm-up are not counted. Idle cells = the rig service waiting for a command.
  Two replicates; the second runs the cells in reverse order, so slow drift shows as a rep gap.

Usage (Pi, repo root): python scripts/n6_power_sweep.py --out ~/n6_eval/power_doe [--seconds 20]
Outputs: <out>/power.csv (every INA row), <out>/<rep>_<cell>/ (sync_video files),
  <out>/sweep.csv + sweep.json (one row per cell x rep: delivered fps, mean V / mA / mW over the
  window, n rows, median detect ms, mean tags per frame). mW is "not validated" until the card's
  known-load check (SPEC_n6_eval must do 3) passes.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ina3221_log as ina  # noqa: E402

N6 = "020023000450433547373200"
SIZES = ("HD", "VGA")
FPS = (10, 15, 30)


def cells() -> list[dict]:
    return [{"size": s, "tags": t, "fps": f} for s in SIZES for t in (False, True) for f in FPS]


def cell_name(c: dict) -> str:
    return f"{c['size']}_{'tags' if c['tags'] else 'notags'}_{c['fps']}fps"


def logger(bus, rows: list, stop: threading.Event, hz: float = 10.0) -> None:
    period = 1.0 / hz
    while not stop.is_set():
        t = time.monotonic()
        try:
            v, mv, ma = ina.read_channel(bus, ina.ADDR, 1)
            rows.append({"mono_s": t, "bus_v": v, "shunt_mv": mv, "ma": ma, "mw": v * ma})
        except OSError as exc:  # recorded, never silent
            rows.append({"mono_s": t, "error": repr(exc)})
        time.sleep(max(0.0, period - (time.monotonic() - t)))


def window_stats(rows: list, t0: float, t1: float) -> dict:
    w = [r for r in rows if t0 <= r["mono_s"] <= t1 and "ma" in r]
    if not w:
        return {"n_power": 0}
    return {"n_power": len(w), "v_mean": round(statistics.mean(r["bus_v"] for r in w), 3),
            "ma_mean": round(statistics.mean(r["ma"] for r in w), 1),
            "ma_sd": round(statistics.pstdev(r["ma"] for r in w), 1),
            "mw_mean": round(statistics.mean(r["mw"] for r in w), 0),
            "read_errors": sum(1 for r in rows if t0 <= r["mono_s"] <= t1 and "error" in r)}


def frame_stats(csv_path: Path, seconds: float) -> dict:
    fr = list(csv.DictReader(csv_path.open())) if csv_path.exists() else []
    if len(fr) < 2:
        return {"frames": len(fr)}
    t = [float(r["host_rx_mono"]) for r in fr]
    det = [float(r["detect_ms"]) for r in fr if r.get("detect_ms")]
    tags = [int(r["tags"]) for r in fr if r.get("tags") not in (None, "")]
    return {"frames": len(fr), "t0": t[0], "t1": t[-1],
            "fps_delivered": round((len(t) - 1) / (t[-1] - t[0]), 2),
            "fps_clip": round(len(t) / seconds, 2),
            "detect_ms_median": statistics.median(det) if det else None,
            "tags_mean": round(statistics.mean(tags), 2) if tags else None,
            "bytes_mean": round(statistics.mean(int(r["bytes"]) for r in fr))}


def run_cell(c: dict, out: Path, seconds: float) -> dict:
    args = [sys.executable, str(ROOT / "scripts/sync_video.py"), "--cameras", "openmv_n6",
            "--n6-serial", N6, "--seconds", str(seconds), "--board-framesize", c["size"],
            "--board-quality", "70", "--board-fps", str(c["fps"]), "--meta", "--timing",
            "--out", str(out)]
    if c["tags"]:
        args += ["--apriltags", "--tag-family", "TAG25H9"]
    p = subprocess.run(args, capture_output=True, text=True, timeout=seconds + 120)
    (out / "sync_video.log").write_text(p.stdout + p.stderr)
    return {"sync_video_rc": p.returncode, **frame_stats(out / "openmv_n6_frames.csv", seconds)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--reps", type=int, default=2)
    a = ap.parse_args(argv)
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=False)  # never overwrite a run
    from smbus2 import SMBus

    rows: list = []
    stop = threading.Event()
    results = []
    with SMBus(1) as bus:
        ids = ina.identify(bus, ina.ADDR)
        if ids["manuf"] != ina.TI:
            print(f"n6_power_sweep: no INA3221 ({ids})", file=sys.stderr)
            return 1
        th = threading.Thread(target=logger, args=(bus, rows, stop), daemon=True)
        th.start()
        plan = [("idle_start", None)]
        for rep in range(1, a.reps + 1):
            order = cells() if rep % 2 else list(reversed(cells()))
            plan += [(f"r{rep}", c) for c in order]
        plan.append(("idle_end", None))
        for tag, c in plan:
            name = tag if c is None else f"{tag}_{cell_name(c)}"
            t_start = time.monotonic()
            if c is None:
                time.sleep(a.seconds)
                r = {"cell": name, "rep": None, "size": "idle", "tags": False, "fps": 0,
                     "t0": t_start, "t1": time.monotonic()}
            else:
                r = {"cell": name, "rep": int(tag[1:]), **c,
                     **run_cell(c, out / name, a.seconds)}
            if "t0" in r:
                r.update(window_stats(rows, r["t0"], r["t1"]))
            results.append(r)
            print(json.dumps({k: r.get(k) for k in ("cell", "fps_delivered", "ma_mean", "mw_mean",
                                                     "detect_ms_median", "tags_mean", "n_power")}),
                  flush=True)
        stop.set()
        th.join(timeout=2)
    with (out / "power.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["mono_s", "bus_v", "shunt_mv", "ma", "mw", "error"])
        w.writeheader()
        w.writerows(rows)
    keys = ["cell", "rep", "size", "tags", "fps", "fps_delivered", "fps_clip", "frames",
            "v_mean", "ma_mean", "ma_sd", "mw_mean", "n_power", "read_errors",
            "detect_ms_median", "tags_mean", "bytes_mean", "sync_video_rc"]
    with (out / "sweep.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)
    (out / "sweep.json").write_text(json.dumps({"ina": ids, "seconds": a.seconds,
                                                "mw_status": "not validated (must do 3 pending)",
                                                "cells": results}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
