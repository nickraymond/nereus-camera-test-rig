#!/usr/bin/env python3
"""video_clip_table.py — one row per camera per synced clip, from the raw files only.

Purpose: pool test 2 prep (docs/pool2_20261021/SPEC.md, must-do 1 and 9). Reads what
  scripts/sync_video.py wrote for each clip (video_sync.json, <board>_frames.csv,
  imx708.meta.json, host_stats.csv) and prints the facts the spec asks for: codec,
  resolution, fps asked vs delivered, frames lost, MB per 30 s, start skew, IMX708 max gap,
  host memory / CMA / throttled. Never reads the Mac MP4 viewing copies.

Usage (Mac or Pi, stdlib only):
  python scripts/video_clip_table.py <clip_dir> [<clip_dir> ...] [--csv out.csv] [--json out.json]

Definitions (all from the raw files):
  - delivered fps: (frames - 1) / (last - first) on the capture clock, as video_sync.json.
  - board period: board ticks_ms between consecutive frames (the board's own clock).
  - MB / 30 s: bytes of the recorded file x 30 / the file's own duration. Boards: the MJPEG holds
    exactly the synced frames, duration = frames / delivered fps. IMX708: the .h264 also holds
    the ~3 s pre-roll and the tail, so duration = all frames in imx708.meta.json / their rate.
  - IMX708 frames lost: nominal frames in the synced window (window x asked fps) minus frames
    recorded there; a gap > 1.5 frame periods counts as a drop.
  - host: min mem_avail / CMA free and max CPU over the clip, from host_stats.csv; every distinct
    `throttled` value seen.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

BOARDS = ("openmv_n6", "openmv_ae3")


def _imx_times(clip: Path) -> list[float]:
    meta = clip / "imx708.meta.json"
    if not meta.exists():
        return []
    try:
        frames = json.loads(meta.read_text())
    except json.JSONDecodeError:
        return []
    return [f["SensorTimestamp"] / 1e9 for f in frames if f.get("SensorTimestamp")]


def _host(clip: Path) -> dict:
    path = clip / "host_stats.csv"
    if not path.exists():
        return {}
    rows = list(csv.DictReader(path.open()))
    if not rows:
        return {}
    return {
        "host_min_mem_avail_mb": min(int(r["mem_avail_mb"]) for r in rows),
        "host_min_cma_free_mb": min(int(r["cma_free_mb"]) for r in rows),
        "host_max_cpu_c": round(max(float(r["cpu_c"]) for r in rows), 1),
        "host_throttled": ";".join(sorted({r["throttled"] for r in rows})),
    }


def clip_rows(clip: Path) -> list[dict]:
    sync = json.loads((clip / "video_sync.json").read_text())
    args = sync.get("args", {})
    host = _host(clip)
    base = {"clip": clip.name, "utc": sync.get("utc"), "seconds": sync.get("seconds"),
            "start_skew_ms": sync.get("start_skew_ms"), **host}
    rows = []
    for cam, rec in sync.get("cameras", {}).items():
        row = dict(base, camera=cam, ok=rec.get("ok"),
                   start_offset_ms=(sync.get("start_offsets_ms") or {}).get(cam),
                   fps=rec.get("fps"), frames=rec.get("frames"),
                   max_gap_ms=rec.get("max_gap_ms"), bytes=rec.get("bytes"),
                   resyncs=rec.get("resyncs", 0), error=rec.get("error") or rec.get("warning"))
        if cam == "imx708":
            times = _imx_times(clip)
            asked = float(args.get("imx_fps") or 30)
            fire, stop = rec.get("fire_host_mono"), rec.get("stop_host_mono")
            win = [t for t in times if fire is not None and stop is not None and fire <= t <= stop]
            nominal = round((stop - fire) * asked) if fire is not None and stop is not None else None
            period = 1.0 / asked
            drops = sum(round((b - a) / period) - 1 for a, b in zip(win, win[1:])
                        if (b - a) > 1.5 * period)
            file_s = (len(times) / (len(times) - 1) * (times[-1] - times[0])) if len(times) > 1 else None
            row.update(codec="H.264 (hw)", resolution=args.get("imx_size"), fps_asked=asked,
                       frames_lost=drops, frames_nominal=nominal,
                       mb_per_30s=round(rec["bytes"] / file_s * 30 / 1e6, 2) if file_s else None,
                       period_med_ms=round(statistics.median(
                           [(b - a) * 1000 for a, b in zip(win, win[1:])]), 1) if len(win) > 1 else None)
        else:
            frames_csv = clip / f"{cam}_frames.csv"
            fr = list(csv.DictReader(frames_csv.open())) if frames_csv.exists() else []
            ticks = [int(r["ticks_ms"]) for r in fr if r.get("ticks_ms")]
            per = [b - a for a, b in zip(ticks, ticks[1:])]
            fps = rec.get("fps")
            dur = (rec["frames"] / fps) if fps and rec.get("frames") else None
            row.update(codec="MJPEG (board)", resolution=rec.get("dims"),
                       quality=args.get("board_quality"), fps_asked=args.get("board_fps"),
                       frames_lost=rec.get("frames_lost"), frames_nominal=None,
                       mb_per_30s=round(rec["bytes"] / dur * 30 / 1e6, 2) if dur else None,
                       period_med_ms=statistics.median(per) if per else None,
                       period_max_ms=max(per) if per else None,
                       frame_kb_mean=round(statistics.mean(int(r["bytes"]) for r in fr) / 1e3, 1)
                       if fr else None,
                       reset_s=rec.get("reset_s"))
        rows.append(row)
    return rows


COLS = ["clip", "camera", "codec", "resolution", "fps_asked", "fps", "frames", "frames_lost",
        "max_gap_ms", "period_med_ms", "mb_per_30s", "start_offset_ms", "start_skew_ms",
        "resyncs", "host_min_mem_avail_mb", "host_min_cma_free_mb", "host_max_cpu_c",
        "host_throttled", "ok", "error"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("clips", nargs="+", type=Path)
    ap.add_argument("--csv", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    rows = []
    for clip in a.clips:
        if not (clip / "video_sync.json").exists():
            print(f"skip {clip}: no video_sync.json", file=sys.stderr)
            continue
        rows += clip_rows(clip)
    print("| " + " | ".join(COLS) + " |")
    print("|" + "---|" * len(COLS))
    for r in rows:
        print("| " + " | ".join("" if r.get(c) is None else str(r.get(c)) for c in COLS) + " |")
    if a.csv:
        keys = sorted({k for r in rows for k in r}, key=lambda k: (k not in COLS, COLS.index(k) if k in COLS else 0, k))
        with a.csv.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
    if a.json:
        a.json.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
