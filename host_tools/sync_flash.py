"""sync_flash.py — ground-truth start skew of a synchronized video run from a light step.

Purpose: ``scripts/sync_video.py`` measures skew from timestamps (IMX708 SensorTimestamp, board
  snapshot ticks through the fire handshake). This checks it against the scene: a light switched
  on/off (room light, torch, strobe) in view of all three cameras is one physical event; each
  camera's frame series shows a brightness step at that moment. The step's time on the common
  Pi clock, per camera, gives the real inter-camera offset.

Inputs: a sync_video run folder (imx708.h264 + imx708.meta.json, <board>.mjpeg +
  <board>_frames.csv, video_sync.json). Needs ffmpeg on PATH (IMX708 H.264 decode).
Output: per camera the step time (Pi monotonic s), the frame period, and the pairwise offsets;
  JSON on stdout and ``sync_flash.json`` in the run folder.

Usage:
  python -m host_tools.sync_flash ~/NereusPool/<date>/nereus002/video/<run>
  python -m host_tools.sync_flash <run> --window 2.0   # search only +-2 s around the largest step

Method: mean grey level per frame (64x36 thumbnail); the step frame is the largest
  frame-to-frame jump of the same sign in every camera; its time is the midpoint between that
  frame and the one before (resolution about one frame period: IMX708 ~33-37 ms, boards
  ~100 ms at 10 fps). Limits: rolling shutter and exposure smear a step over a frame; a scene
  with other big brightness changes (moving diver) can fool it — use a clear on/off event.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
from pathlib import Path


def imx_series(run: Path) -> list[tuple[float, float]]:
    """(Pi monotonic s, mean grey) for every IMX708 frame: decoded order == metadata order."""
    meta = json.loads((run / "imx708.meta.json").read_text())
    ts = [m["SensorTimestamp"] / 1e9 for m in meta]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(run / "imx708.h264"), "-vf",
                          "scale=64:36,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    n = len(raw) // (64 * 36)
    means = [sum(raw[i * 2304:(i + 1) * 2304]) / 2304 for i in range(n)]
    if n != len(ts):
        print(f"warning: imx708 decoded {n} frames, metadata {len(ts)}; using the common "
              f"prefix", file=sys.stderr)
    return list(zip(ts, means))


def board_series(run: Path, name: str, summary: dict) -> list[tuple[float, float]]:
    """(Pi monotonic s, mean grey) per board frame: frames split by the CSV byte counts."""
    from PIL import Image

    cam = summary["cameras"][name]
    fire_host, fire_rx = cam["fire_host_mono"], cam["board_sync"]["fire_rx_ms"]
    rows = list(csv.DictReader((run / f"{name}_frames.csv").open()))
    data = (run / f"{name}.mjpeg").read_bytes()
    out, off = [], 0
    for r in rows:
        n = int(r["bytes"])
        img = Image.open(io.BytesIO(data[off:off + n])).convert("L").resize((64, 36))
        off += n
        out.append((fire_host + (int(r["ticks_ms"]) - fire_rx) / 1000, sum(img.tobytes()) / 2304))
    return out


def step_time(series: list[tuple[float, float]], sign: int, around: float | None,
              window: float) -> tuple[float, float, float]:
    """(time, jump, frame period) of the largest same-sign jump; time = midpoint of the pair."""
    best = None
    for (t0, m0), (t1, m1) in zip(series, series[1:]):
        if around is not None and abs(t1 - around) > window:
            continue
        d = (m1 - m0) * sign
        if best is None or d > best[1]:
            best = ((t0 + t1) / 2, d, t1 - t0)
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run")
    ap.add_argument("--window", type=float, default=2.0,
                    help="search +-s around the IMX708's largest step")
    a = ap.parse_args()
    run = Path(a.run).expanduser()
    summary = json.loads((run / "video_sync.json").read_text())
    series = {"imx708": imx_series(run)}
    for name in ("openmv_n6", "openmv_ae3"):
        if (run / f"{name}_frames.csv").exists():
            series[name] = board_series(run, name, summary)
    # The reference event: the IMX708's largest absolute jump; its sign (on/off) for all.
    ref = max(((t1, m1 - m0) for (t0, m0), (t1, m1) in zip(series["imx708"],
                                                            series["imx708"][1:])),
              key=lambda x: abs(x[1]))
    sign = 1 if ref[1] > 0 else -1
    steps = {n: step_time(s, sign, ref[0], a.window) for n, s in series.items()}
    t0 = min(v[0] for v in steps.values())
    res = {"run": run.name, "event": "light on" if sign > 0 else "light off",
           "step_host_mono": {n: round(v[0], 4) for n, v in steps.items()},
           "jump_grey": {n: round(v[1], 1) for n, v in steps.items()},
           "frame_period_ms": {n: round(v[2] * 1000, 1) for n, v in steps.items()},
           "offset_ms": {n: round((v[0] - t0) * 1000, 1) for n, v in steps.items()},
           "timestamp_start_skew_ms": summary.get("start_skew_ms")}
    res["measured_skew_ms"] = max(res["offset_ms"].values())
    (run / "sync_flash.json").write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
