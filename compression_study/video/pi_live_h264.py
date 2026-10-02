"""Live path on the Pi: record 15 s straight to hardware H.264 at a budget (no master file).

    python -m compression_study.video.pi_live_h264 --out ~/video_study/live

For each resolution × budget: ``rpicam-vid --codec h264 --bitrate <budget×8/15> --intra 600``
for 15 s, with the UPS power meter sampled and the Pi's CPU time read from /proc/stat. Records
the file size against the budget (rate-control accuracy on a live scene), frames delivered
(from ``--save-pts``), CPU load and energy. Also one MJPEG q95 master recording per resolution
for comparison (what "record first, encode later" costs while recording).

Quality is not scored here: a live clip has no lossless original. The same hardware encoder
was scored from the saved master in ``pi_encode.py``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

from compression_study.phase2.pi_run_probe import PowerSampler, guard_idle  # noqa: E402

SECONDS = 15


def cpu_jiffies() -> tuple[int, int]:
    f = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    vals = [int(x) for x in f]
    idle = vals[3] + vals[4]
    return sum(vals), idle


def record(args: list[str], out: Path) -> dict:
    pts = out.with_suffix(".pts")
    cmd = [
        "rpicam-vid",
        "-n",
        "-t",
        str(SECONDS * 1000),
        "--framerate",
        "30",
        "--save-pts",
        str(pts),
        *args,
        "-o",
        str(out),
    ]
    power = PowerSampler()
    power.start()
    t0, (tot0, idle0) = time.time(), cpu_jiffies()
    r = subprocess.run(cmd, capture_output=True, text=True)
    wall, (tot1, idle1) = time.time() - t0, cpu_jiffies()
    power.stop_flag = True
    power.join(timeout=5)
    s = power.samples
    energy = sum((b[0] - a[0]) * (a[1] or 0) * (a[2] or 0) / 1e6 for a, b in zip(s, s[1:]))
    lines = pts.read_text().splitlines() if pts.exists() else []
    ts = [float(x) for x in lines if x.strip() and not x.startswith("#")]  # "# timecode format v2"
    gaps = sum(1 for a, b in zip(ts, ts[1:]) if b - a > 50)
    return {
        "rc": r.returncode,
        "wall_s": round(wall, 2),
        "frames": len(ts),
        "gaps_gt50ms": gaps,
        "cpu_busy_pct": round(100 * (1 - (idle1 - idle0) / max(1, tot1 - tot0)), 1),
        "energy_J": round(energy, 1),
        "mean_W": round(energy / wall, 3),
        "size_B": out.stat().st_size if out.exists() else None,
        "stderr_tail": r.stderr.strip().splitlines()[-2:] if r.returncode else [],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--budgets", nargs="*", type=int, default=[200, 400, 800, 1600])
    a = ap.parse_args(argv)
    guard_idle()
    a.out.mkdir(parents=True, exist_ok=True)
    log = (a.out / "live.jsonl").open("a")
    for w, h in ((1920, 1080), (1280, 720)):
        res = ["--width", str(w), "--height", str(h)]
        out = a.out / f"master_{h}p.mjpeg"
        r = record([*res, "--codec", "mjpeg", "-q", "95"], out)
        rec = {"kind": "mjpeg_master", "height": h, **r}
        log.write(json.dumps(rec) + "\n")
        print(rec, flush=True)
        out.unlink()  # 100+ MB; the scored master already exists
        for kb in a.budgets:
            br = int(kb * 1000 * 8 / SECONDS)
            out = a.out / f"live_{h}p_{kb}k.h264"
            r = record(
                [
                    *res,
                    "--codec",
                    "h264",
                    "--bitrate",
                    str(br),
                    "--intra",
                    "600",
                    "--profile",
                    "high",
                ],
                out,
            )
            rec = {"kind": "live_h264", "height": h, "budget_kB": kb, "target_bps": br, **r}
            log.write(json.dumps(rec) + "\n")
            log.flush()
            print(rec, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
