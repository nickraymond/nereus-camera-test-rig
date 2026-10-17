#!/usr/bin/env python3
"""matched_frames.py — the same moment from IMX708, N6 and AE3 in one synced clip.

Purpose: pool test 2 prep (docs/pool2_20261021/SPEC.md, must-do 2 and 12): N6 vs AE3 vs IMX708
  stills matched in time (all three within a 50 ms span), to compare field of view, sharpness, colour, noise
  and motion blur. Reads only the raw files scripts/sync_video.py wrote.

Matching clock: each frame's CAPTURE time on the Pi's monotonic clock — the IMX708's
  SensorTimestamp (imx708.meta.json, CLOCK_MONOTONIC) and, for a board, its snapshot ticks tied
  to the Pi clock by the fire receipt (fire_host_mono + (ticks_ms - fire_rx_ms) / 1000, as
  video_sync.json's first_frame_host_mono). A board's host_rx_mono is when the Pi finished
  reading the frame, i.e. capture + JPEG + USB time, so it is reported but not matched on.

Usage (Mac, needs ffmpeg on PATH):
  python scripts/matched_frames.py <clip_dir> <t_s> [<t_s> ...] --out DIR [--window-ms 50]
  t_s = seconds after the synchronized start (the fire). Writes, per moment:
    <clip>_t<t>_{imx708,openmv_n6,openmv_ae3}.jpg (full resolution; the board JPEGs are the
    camera's own bytes, unchanged), <clip>_t<t>_strip.jpg (side by side at 400 px high), and one
    row per camera in matched_frames.csv. A moment whose three frames span more than the window
    is dropped (no images) and counted (spec rev 5); exit 1 if any moment was dropped.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

BOARDS = ("openmv_n6", "openmv_ae3")


def board_frames(clip: Path, cam: str, sync: dict) -> list[dict]:
    rec = sync["cameras"][cam]
    bs = rec.get("board_sync") or (rec.get("board_done") or {}).get("sync") or {}
    fire_rx, fire_host = bs.get("fire_rx_ms"), rec.get("fire_host_mono")
    rows, off = [], 0
    for r in csv.DictReader((clip / f"{cam}_frames.csv").open()):
        n = int(r["bytes"])
        rows.append({"seq": int(r["seq"]), "offset": off, "bytes": n,
                     "t": fire_host + (int(r["ticks_ms"]) - fire_rx) / 1000,
                     "host_rx": float(r["host_rx_mono"])})
        off += n
    return rows


def imx_frames(clip: Path) -> list[dict]:
    meta = json.loads((clip / "imx708.meta.json").read_text())
    return [{"index": i, "t": f["SensorTimestamp"] / 1e9} for i, f in enumerate(meta)
            if f.get("SensorTimestamp")]


def nearest(frames: list[dict], t: float) -> dict:
    return min(frames, key=lambda f: abs(f["t"] - t))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("clip", type=Path)
    ap.add_argument("times", nargs="+", type=float)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--window-ms", type=float, default=50.0)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    sync = json.loads((a.clip / "video_sync.json").read_text())
    fire = min(r["fire_host_mono"] for r in sync["cameras"].values())
    cams = {c: board_frames(a.clip, c, sync) for c in BOARDS if c in sync["cameras"]}
    imx = imx_frames(a.clip) if "imx708" in sync["cameras"] else []
    log = a.out / "matched_frames.csv"
    new = not log.exists()
    dropped = 0
    with log.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["clip", "t_s", "camera", "frame", "capture_t_s",
                                          "dt_ms_vs_ref", "dt_hostrx_ms_vs_ref", "span_ms",
                                          "kept", "file"])
        if new:
            w.writeheader()
        for t in a.times:
            # Reference = the slowest stream's frame (AE3, else N6) within 0.5 s of the target
            # that keeps the other cameras closest to it, so every camera can land in the window.
            target = fire + t
            slow = cams.get("openmv_ae3") or cams.get("openmv_n6")
            others = [fr for c, fr in cams.items() if fr is not slow] + ([imx] if imx else [])
            cands = [f for f in slow if abs(f["t"] - target) <= 0.5] if slow else []
            ref = (min(cands, key=lambda c: max([abs(nearest(o, c["t"])["t"] - c["t"])
                                                 for o in others] or [0]))["t"]
                   if cands else target)
            picks = {"imx708": nearest(imx, ref)} if imx else {}
            picks.update({cam: nearest(frames, ref) for cam, frames in cams.items()})
            ts = [p["t"] for p in picks.values()]
            span = (max(ts) - min(ts)) * 1000
            kept = span <= a.window_ms  # a triplet spanning more than the window is dropped
            dropped += not kept
            stem = f"{a.clip.name}_t{t:05.1f}"
            parts = []
            for cam, fr in picks.items():
                out = a.out / f"{stem}_{cam}.jpg"
                if kept and cam == "imx708":
                    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "h264", "-i",
                                    str(a.clip / "imx708.h264"), "-vf",
                                    f"select=eq(n\\,{fr['index']})", "-frames:v", "1",
                                    "-q:v", "2", str(out)], check=True)
                elif kept:
                    with (a.clip / f"{cam}.mjpeg").open("rb") as raw:
                        raw.seek(fr["offset"])
                        out.write_bytes(raw.read(fr["bytes"]))
                if kept:
                    parts.append(out)
                w.writerow({"clip": a.clip.name, "t_s": t, "camera": cam,
                            "frame": fr.get("index", fr.get("seq")),
                            "capture_t_s": round(fr["t"] - fire, 4),
                            "dt_ms_vs_ref": round((fr["t"] - ref) * 1000, 1),
                            "dt_hostrx_ms_vs_ref": round((fr["host_rx"] - ref) * 1000, 1)
                            if "host_rx" in fr else "",
                            "span_ms": round(span, 1), "kept": kept,
                            "file": out.name if kept else ""})
            if not kept:
                print(f"{stem}: DROPPED, span {span:.1f} ms > {a.window_ms:g} ms")
                continue
            inputs = sum((["-i", str(p)] for p in parts), [])
            scale = ";".join(f"[{i}:v]scale=-2:400[v{i}]" for i in range(len(parts)))
            stack = "".join(f"[v{i}]" for i in range(len(parts)))
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *inputs, "-filter_complex",
                            f"{scale};{stack}hstack=inputs={len(parts)}", "-q:v", "2",
                            str(a.out / f"{stem}_strip.jpg")], check=True)
            print(f"{stem}: span {span:.1f} ms · " + ", ".join(p.name for p in parts))
    print(f"{len(a.times) - dropped} kept, {dropped} dropped (span > {a.window_ms:g} ms)")
    return 0 if not dropped else 1


if __name__ == "__main__":
    sys.exit(main())
