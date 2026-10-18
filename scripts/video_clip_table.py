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
  - fps_clip (the bars' input, QE 005 R2): frames captured in [fire, fire + clip seconds] / clip
    seconds, on the capture clock. A camera that stops early scores low.
  - fps: (frames - 1) / (last - first) on the capture clock, as video_sync.json: the rate while
    streaming, reported only.
  - last_frame_s: the last frame's capture time after the fire.
  - board period: board ticks_ms between consecutive frames (the board's own clock).
  - MB / 30 s: bytes of the recorded file x 30 / the file's own duration. Boards: the MJPEG holds
    exactly the synced frames, duration = frames / delivered fps. IMX708: the .h264 also holds
    the ~3 s pre-roll and the tail, so duration = all frames in imx708.meta.json / their rate.
  - IMX708 frames lost: nominal frames in the synced window (window x asked fps) minus frames
    recorded there; a gap > 1.5 frame periods counts as a drop.
  - host: min mem_avail / CMA free and max CPU over the clip, from host_stats.csv; every distinct
    `throttled` value seen. host_throttle_void: a current-state bit (0xF) in any sample, or a
    sticky "has occurred" bit (0xF0000) that was not set at the clip's first sample; sticky bits
    from before the clip don't void it. None when there is no reading.
  - boards: resolution_measured / subsampling_measured from each frame's JPEG SOF header (the
    asked `subsampling` is a separate column); bad_frames = frames that don't start FF D8, end
    FF D9 and carry one SOF size.
  - first_frame_delay_ms: the camera's own fire -> first frame (traces a start-skew miss).
  - boards with ``--timing``: medians of the per-frame snapshot / JPEG / previous-write ms.
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


def throttle_void(values: list[str]) -> bool | None:
    """vcgencmd get_throttled samples over one clip, in order -> void? Bits 0-3 are the current
    state, bits 16-19 'has occurred since boot' (sticky). A current bit in any sample, or a sticky
    bit that appears during the clip, voids it; sticky bits already set at its first sample do
    not (they would void every later clip of that boot). None = no readable sample. Pure."""
    bits = []
    for v in values:
        try:
            bits.append(int(str(v).strip(), 16))
        except ValueError:
            continue
    if not bits:
        return None
    sticky0 = (bits[0] >> 16) & 0xF
    return any(b & 0xF for b in bits) or any(((b >> 16) & 0xF) & ~sticky0 for b in bits)


def _host(clip: Path) -> dict:
    path = clip / "host_stats.csv"
    rows = list(csv.DictReader(path.open())) if path.exists() else []
    if not rows:
        return {"host_throttle_void": None}

    def num(key, fn):
        v = [fn(r[key]) for r in rows if r.get(key) not in (None, "")]
        return v or [None]
    return {
        "host_min_mem_avail_mb": min(num("mem_avail_mb", int), key=lambda x: x or 0),
        "host_min_cma_free_mb": min(num("cma_free_mb", int), key=lambda x: x or 0),
        "host_max_cpu_c": max(num("cpu_c", lambda x: round(float(x), 1)), key=lambda x: x or 0),
        "host_throttled": ";".join(sorted({r["throttled"] for r in rows})),
        "host_throttle_void": throttle_void([r["throttled"] for r in rows]),
    }


_SAMPLING = {(2, 2): "420", (2, 1): "422", (1, 1): "444"}


def jpeg_sof(data: bytes) -> dict | None:
    """Width, height and chroma subsampling from a JPEG's SOF header (baseline/progressive),
    or None if it isn't a JPEG with one. Subsampling from the luma sampling factors when both
    chroma components are 1x1 ("420" / "422" / "444"), "gray" for one component. Pure."""
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            body = data[i + 4:i + 2 + seg]
            if len(body) < 6:
                return None
            h, w, nc = int.from_bytes(body[1:3], "big"), int.from_bytes(body[3:5], "big"), body[5]
            comps = [body[6 + 3 * k + 1] for k in range(nc) if 6 + 3 * k + 1 < len(body)]
            if nc == 1:
                sub = "gray"
            elif len(comps) == 3 and comps[1] == comps[2] == 0x11:
                sub = _SAMPLING.get((comps[0] >> 4, comps[0] & 0xF), f"0x{comps[0]:02x}")
            else:
                sub = "other"
            return {"width": w, "height": h, "subsampling": sub}
        if marker == 0xDA:  # start of scan before any SOF
            return None
        i += 2 + seg
    return None


def mjpeg_frames(clip: Path, cam: str, sizes: list[int]) -> dict:
    """Every frame of <cam>.mjpeg, split by frames.csv's byte counts: the measured size and
    chroma format, and frames that aren't whole JPEGs (no FF D8 start, no FF D9 end, no SOF)."""
    path = clip / f"{cam}.mjpeg"
    if not path.exists() or not sizes:
        return {"resolution_measured": None, "subsampling_measured": None, "bad_frames": None}
    dims, subs, bad = set(), set(), 0
    with path.open("rb") as f:
        for n in sizes:
            data = f.read(n)
            sof = jpeg_sof(data[:4096])
            if len(data) != n or sof is None or data[-2:] != b"\xff\xd9":
                bad += 1
                continue
            dims.add(f"{sof['width']}x{sof['height']}")
            subs.add(sof["subsampling"])
        extra = len(f.read())  # bytes beyond the listed frames
    return {"resolution_measured": ";".join(sorted(dims)) or None,
            "subsampling_measured": ";".join(sorted(subs)) or None,
            "bad_frames": bad + (1 if extra else 0)}


def _rate(times: list[float], fire: float | None, seconds: float | None) -> dict:
    """fps_clip + last_frame_s, the same rule as sync_video.clip_rate (recomputed from the raw
    files, so clips recorded before it existed are scored the same way)."""
    if fire is None or not seconds:
        return {"fps_clip": None, "last_frame_s": None}
    win = [t for t in times if fire <= t <= fire + seconds]
    return {"fps_clip": round(len(win) / seconds, 2),
            "last_frame_s": round(max(times) - fire, 3) if times else None}


def clip_rows(clip: Path) -> list[dict]:
    sync = json.loads((clip / "video_sync.json").read_text())
    args = sync.get("args", {})
    host = _host(clip)
    seconds = sync.get("seconds")
    base = {"clip": clip.name, "utc": sync.get("utc"), "seconds": seconds,
            "start_skew_ms": sync.get("start_skew_ms"), **host}
    rows = []
    for cam, rec in sync.get("cameras", {}).items():
        row = dict(base, camera=cam, ok=rec.get("ok"),
                   start_offset_ms=(sync.get("start_offsets_ms") or {}).get(cam),
                   fps=rec.get("fps"), frames=rec.get("frames"),
                   max_gap_ms=rec.get("max_gap_ms"), bytes=rec.get("bytes"),
                   resyncs=rec.get("resyncs", 0), short_frames=rec.get("short_frames", 0),
                   error=rec.get("error") or rec.get("warning"),
                   first_frame_delay_ms=round((rec["first_frame_host_mono"]
                                               - rec["fire_host_mono"]) * 1000, 1)
                   if rec.get("first_frame_host_mono") and rec.get("fire_host_mono") else None)
        if cam == "imx708":
            times = _imx_times(clip)
            asked = float(args.get("imx_fps") or 30)
            fire, stop = rec.get("fire_host_mono"), rec.get("stop_host_mono")
            span = fire is not None and stop is not None
            win = [t for t in times if span and fire <= t <= stop]
            row.update(rc=rec.get("rc"), **_rate(win, fire, seconds))
            nominal = round((stop - fire) * asked) if span else None
            period = 1.0 / asked
            drops = sum(round((b - a) / period) - 1 for a, b in zip(win, win[1:])
                        if (b - a) > 1.5 * period)
            file_s = (len(times) / (len(times) - 1) * (times[-1] - times[0])
                      if len(times) > 1 else None)
            gaps = [(b - a) * 1000 for a, b in zip(win, win[1:])]
            row.update(codec="H.264 (hw)", resolution=args.get("imx_size"), fps_asked=asked,
                       frames_lost=drops, frames_nominal=nominal,
                       mb_per_30s=round(rec["bytes"] / file_s * 30 / 1e6, 2)
                       if file_s and rec.get("bytes") else None,
                       period_med_ms=round(statistics.median(gaps), 1) if gaps else None)
        else:
            frames_csv = clip / f"{cam}_frames.csv"
            fr = list(csv.DictReader(frames_csv.open())) if frames_csv.exists() else []
            ticks = [int(r["ticks_ms"]) for r in fr if r.get("ticks_ms")]
            per = [b - a for a, b in zip(ticks, ticks[1:])]
            fps = rec.get("fps")
            dur = (rec["frames"] / fps) if fps and rec.get("frames") else None
            settings = rec.get("settings") or {}
            fire_rx = (rec.get("board_sync") or {}).get("fire_rx_ms")
            fire = rec.get("fire_host_mono")
            times = [fire + (t - fire_rx) / 1000 for t in ticks] \
                if fire is not None and fire_rx is not None else []
            row.update(**_rate(times, fire, seconds),
                       **mjpeg_frames(clip, cam, [int(r["bytes"]) for r in fr]))

            def med(key):
                v = [float(r[key]) for r in fr if r.get(key) not in (None, "")]
                return round(statistics.median(v), 1) if v else None
            row.update(codec="MJPEG (board)", resolution=rec.get("dims"),
                       quality=settings.get("jpeg_quality", args.get("board_quality")),
                       fps_asked=settings.get("fps", args.get("board_fps")),
                       frames_lost=rec.get("frames_lost"), frames_nominal=None,
                       mb_per_30s=round(rec["bytes"] / dur * 30 / 1e6, 2)
                       if dur and rec.get("bytes") else None,
                       period_med_ms=statistics.median(per) if per else None,
                       period_max_ms=max(per) if per else None,
                       frame_kb_mean=round(statistics.mean(int(r["bytes"]) for r in fr) / 1e3, 1)
                       if fr else None,
                       reset_s=rec.get("reset_s"),
                       subsampling=settings.get("subsampling", "auto"),
                       snap_ms_med=med("snap_ms"), jpeg_ms_med=med("jpeg_ms"),
                       write_ms_med=med("prev_write_ms"))
        rows.append(row)
    return rows


COLS = ["clip", "camera", "codec", "resolution", "resolution_measured", "fps_asked", "fps_clip",
        "fps", "last_frame_s", "frames", "frames_lost", "bad_frames", "max_gap_ms",
        "period_med_ms", "mb_per_30s", "start_offset_ms", "first_frame_delay_ms",
        "start_skew_ms", "subsampling", "subsampling_measured", "snap_ms_med", "jpeg_ms_med",
        "write_ms_med", "resyncs", "short_frames", "host_min_mem_avail_mb",
        "host_min_cma_free_mb", "host_max_cpu_c", "host_throttled", "host_throttle_void", "ok",
        "rc", "error"]


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
        keys = sorted({k for r in rows for k in r},
                      key=lambda k: (k not in COLS, COLS.index(k) if k in COLS else 0, k))
        with a.csv.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
    if a.json:
        a.json.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
