#!/usr/bin/env python3
"""imx_tags.py — live AprilTag detection on the IMX708's H.264 while rpicam-vid records it.

Purpose: pool 3 edge test (docs/specs/pool3_edge_tags.md rev 1+): the IMX708 detects tags live on
  the Pi, on a frame subset, in a separate process at native scale, never in the recorder's path.
  sync_video.py starts it beside rpicam-vid (--imx-tags-every); it only reads the growing file.

How:
  - follows <clip>/imx708.h264 as rpicam-vid writes it and splits it into frames (Annex B NAL
    units; a slice with first_mb_in_slice = 0 starts a frame). Frame k here is record k of
    imx708.meta.json, which gives its SensorTimestamp.
  - only IDR frames are decoded (they stand alone: the SPS + PPS rpicam-vid puts before each IDR
    with --inline, then the IDR). Measured on pool 2's clip008 (2026-10-21): one slice per frame,
    an IDR every 60 frames at rpicam-vid's default intra period. The newest complete IDR is taken
    each time the previous search ends, so a slow search skips IDRs instead of queueing them.
  - decode: ``ffmpeg`` (one short process per IDR, gray PGM out); detect: the repo's
    detect_tags (cv2.aruco, tag25h9) at scale 1 only (upscaled passes OOM on the Pi Zero 2 W,
    apriltag_detector.py).
  - runs at nice 19. F7 guard: before each search, MemAvailable below --min-mem-mb ends the
    process (reason in the summary); the recorder is never touched.
Outputs: --out CSV (frame, idr, host_mono, decode_ms, tag_ms, tag_ids, tag_rej, tag_corners: one
  row per searched frame) and <out>.json (searches, IDRs seen / skipped, exit reason).
Usage (Pi; sync_video.py does this):
  python scripts/imx_tags.py <clip>/imx708.h264 --out <clip>/imx708_tags.csv --ids 0,1,2,3
Stops on SIGTERM/SIGINT (the parent sends it once rpicam-vid has exited).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

NAL_SLICE, NAL_IDR, NAL_SPS, NAL_PPS = 1, 5, 7, 8
CSV_FIELDS = ("frame", "idr", "host_mono", "decode_ms", "tag_ms", "tag_ids", "tag_rej",
              "tag_corners")


def _first_mb_is_zero(nal: bytes) -> bool:
    """first_mb_in_slice is the slice header's first ue(v); 0 is coded as a single 1 bit."""
    return len(nal) > 1 and bool(nal[1] & 0x80)


class H264Frames:
    """Incremental Annex B splitter: ``feed`` bytes as the file grows, get back each complete IDR
    frame as (frame index, SPS + PPS + IDR slices). A frame is complete when the next one starts."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.frame = -1          # index of the frame being collected
        self.sps = self.pps = b""
        self.cur: list[bytes] | None = None  # slices of the current frame if it is an IDR
        self.idrs_seen = 0

    def _nals(self) -> list[bytes]:
        """Complete NAL units in the buffer (each with its start code); the last, possibly
        partial one stays buffered."""
        out, starts, i = [], [], 0
        while (j := self.buf.find(b"\x00\x00\x01", i)) != -1:
            starts.append(j - 1 if j > 0 and self.buf[j - 1] == 0 else j)
            i = j + 3
        for a, b in zip(starts, starts[1:]):
            out.append(bytes(self.buf[a:b]))
        if starts:
            del self.buf[:starts[-1]]
        return out

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self.buf += data
        done = []
        for nal in self._nals():
            hdr = nal.index(b"\x00\x00\x01") + 3
            kind, body = nal[hdr] & 0x1F, nal[hdr:]
            if kind == NAL_SPS:
                self.sps = nal
            elif kind == NAL_PPS:
                self.pps = nal
            elif kind in (NAL_SLICE, NAL_IDR):
                if _first_mb_is_zero(body):  # a new frame starts: the previous one is complete
                    if self.cur is not None:
                        done.append((self.frame, b"".join(self.cur)))
                    self.frame += 1
                    self.cur = None
                    if kind == NAL_IDR:
                        self.idrs_seen += 1
                        self.cur = [self.sps, self.pps, nal]
                elif self.cur is not None:
                    self.cur.append(nal)
        return done


def decode_ffmpeg(au: bytes):
    """One IDR access unit -> gray numpy image (ffmpeg, PGM through a pipe)."""
    import cv2
    import numpy as np

    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "h264", "-i", "pipe:0",
                        "-frames:v", "1", "-f", "image2pipe", "-c:v", "pgm", "pipe:1"],
                       input=au, capture_output=True, timeout=10)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"ffmpeg rc={r.returncode}: {r.stderr.decode(errors='replace')[-200:]}")
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_GRAYSCALE)


def mem_available_mb() -> int | None:
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) // 1024
    except OSError:
        return None
    return None


def search(gray, ids: list[int]) -> dict:
    """tag25h9 at native scale; ids outside the card copy's set are rejected (raw false ids)."""
    from nereus_camera_test_rig.analysis.apriltag_detector import detect_tags

    t0 = time.monotonic()
    out = detect_tags(gray, family="DICT_APRILTAG_25h9", scales=(1,))
    ms = round((time.monotonic() - t0) * 1000, 1)
    keep = [i for i in out.tag_ids if i in ids]
    return {"tag_ms": ms, "tag_ids": json.dumps(keep),
            "tag_rej": json.dumps([i for i in out.tag_ids if i not in ids]),
            "tag_corners": json.dumps([[[round(float(x), 1), round(float(y), 1)]
                                        for x, y in out.tags[i].corners] for i in keep])}


def run(h264: Path, out: Path, ids: list[int], min_mem_mb: int, poll_s: float = 0.05,
        decode=decode_ffmpeg, stop=lambda: False) -> dict:
    split = H264Frames()
    summary = {"searches": 0, "idrs_seen": 0, "idrs_skipped": 0, "errors": 0, "exit": "stopped"}
    pos = 0
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        while not stop():
            data = b""
            if h264.exists():
                with h264.open("rb") as src:
                    src.seek(pos)
                    data = src.read()
                pos += len(data)
            idrs = split.feed(data) if data else []
            if not idrs:
                time.sleep(poll_s)
                continue
            summary["idrs_skipped"] += len(idrs) - 1
            frame, au = idrs[-1]  # newest only
            mem = mem_available_mb()
            if mem is not None and mem < min_mem_mb:  # F7: give the memory back to the recorder
                summary["exit"] = f"MemAvailable {mem} MB < {min_mem_mb}"
                break
            t0 = time.monotonic()
            try:
                gray = decode(au)
                decode_ms = round((time.monotonic() - t0) * 1000, 1)
                row = {"frame": frame, "idr": split.idrs_seen, "host_mono": round(t0, 4),
                       "decode_ms": decode_ms, **search(gray, ids)}
            except Exception as exc:  # noqa: BLE001 - counted, logged, never fatal to the clip
                summary["errors"] += 1
                summary["last_error"] = repr(exc)[:200]
                continue
            w.writerow(row)
            f.flush()
            summary["searches"] += 1
    summary["idrs_seen"] = split.idrs_seen
    Path(str(out) + ".json").write_text(json.dumps(summary, indent=1))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("h264", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ids", required=True, help="the card copy's tag ids, e.g. 0,1,2,3")
    ap.add_argument("--min-mem-mb", type=int, default=80,
                    help="F7: stop when MemAvailable falls below this (MB)")
    a = ap.parse_args()
    os.nice(19)
    stopping = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopping.append(1))
    s = run(a.h264, a.out, [int(i) for i in a.ids.split(",")], a.min_mem_mb,
            stop=lambda: bool(stopping))
    print(f"imx_tags: {s}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
