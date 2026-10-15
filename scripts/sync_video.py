#!/usr/bin/env python3
"""sync_video.py — synchronized video on all three cameras: one start, one clock, measured skew.

Purpose: Friday dive (2026-10-16) video on the IMX708, N6 and AE3 started by ONE command at the
  same moment, every frame timestamped on the Pi's monotonic clock, and the start skew between
  cameras measured. Replaces Nick's ci-record for nereus002's dive video: ci-record (IMX H.264 +
  N6 + AE3) hard-reset the Pi twice on 2026-10-13 (CmaFree 16 kB), so this path samples CPU,
  memory and CMA every second and is gated (5 clean starts/stops) before the dive.

How:
  - boards (N6, AE3): ``reset_board`` (the AE3 allows one camera session per boot, OQ-19), then
    ``start_stream`` with ``trigger``: the board warms up, says "armed", starts on the fire byte;
    each frame header carries the board ticks of its snapshot. Frames -> <cam>.mjpeg
    (concatenated JPEGs) + <cam>_frames.csv. The board's fire receipt ties its ticks to the Pi
    clock: frame_time = fire_host + (ticks - fire_rx_ticks).
  - IMX708: ``rpicam-vid -t 0`` (hardware H.264) records from the arm (~3 s pre-roll) with
    ``--metadata``: one record per frame with SensorTimestamp (CLOCK_MONOTONIC, the Pi clock).
    Its synchronized start is the first frame at/after the fire (<= 1 frame late). Not
    pause/resume: rpicam-vid resumes only at the next keyframe (measured: 251 of ~300 frames,
    up to the intra period late), which would make the IMX708 start late by up to ~2 s.
  - all three are armed in parallel and fired back to back (sub-ms on the host).

Usage (Pi, repo root):
  python scripts/sync_video.py --seconds 10                      # all three, defaults
  python scripts/sync_video.py --seconds 10 --board-framesize VGA --board-fps 10 --out DIR
Outputs (DIR, default results/video/<ts>/): imx708.h264 (+ .pts, .meta.json, .log),
  openmv_n6.mjpeg + openmv_n6_frames.csv, openmv_ae3.mjpeg + openmv_ae3_frames.csv,
  host_stats.csv, video_sync.json (start skew, fps, frames, gaps, files + sha256).
Exit 0 = every camera recorded and the start skew was measured; 1 otherwise.

Limits: the AE3's software JPEG caps it at ~14 fps VGA / ~2.3 fps HD (Nick's S3 bench); the
  board stream is MJPEG over USB (no on-board H.264). The visible sync marker (LED flash / clap)
  is the ground truth for the measured skew; this script measures timestamps only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from openmv.common import command_protocol as cp  # noqa: E402

from nereus_camera_test_rig.cameras.openmv_usb import OpenMvError, OpenMvUsbCamera  # noqa: E402

BOARDS = {"openmv_n6": "10003500025043364d343000", "openmv_ae3": "0829c14000000000"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class BoardVideo:
    """One OpenMV board's synchronized MJPEG stream (arm -> fire -> stop -> finish)."""

    def __init__(self, name: str, serial: str, out: Path, settings: dict):
        self.name, self.serial, self.out, self.settings = name, serial, out, settings
        self.cam = OpenMvUsbCamera(serial)
        self.rec: dict = {"camera": name, "ok": False}
        self.thread: threading.Thread | None = None

    def arm(self) -> None:
        r = self.cam.reset_board()
        self.rec["reset_s"] = round(r["duration_seconds"], 2)
        self.io = self.cam._ensure_io()
        self.command_id = uuid.uuid4().hex[:12]
        self.io.write_message(cp.make_request("start_stream", self.command_id,
                                              {**self.settings, "trigger": True}))
        msg = self.io.read_message(timeout=30)
        if msg.get("status") != "armed":
            raise OpenMvError("bad_arm", f"{self.name}: expected armed, got {msg}")
        self.rec["armed_ticks"] = msg["armed"]["ticks_ms"]

    def fire(self) -> float:
        t = time.monotonic()
        self.io._t.write(b"\n")
        self.rec["fire_host_mono"] = t
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        return t

    def _next_message(self) -> dict:
        """The next protocol line. Resyncs after lost bytes: the N6 drops USB TX bytes when the
        Pi reads too slowly (2026-10-14: 63 fps VGA + 1080p H.264 desynced after 124 frames), so
        a JPEG body can land where a header was expected. Skip lines until one decodes."""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            line = self.io.read_line(timeout=max(0.1, deadline - time.monotonic()))
            if not line.strip().startswith(b"{"):
                continue
            try:
                return cp.decode_message(line)
            except (ValueError, UnicodeDecodeError, cp.ProtocolError):
                self.rec["resyncs"] = self.rec.get("resyncs", 0) + 1
        raise OpenMvError("timeout", f"{self.name}: no frame header for 10 s")

    def _read(self) -> None:
        """USB reader: never touches the SD card. Frames go to a writer thread through a queue,
        so an SD write stall (100s of ms) cannot stop the reads and make the board's USB send
        time out and drop bytes (the N6 desync of 2026-10-14, soak #1)."""
        import queue

        mjpeg = self.out / f"{self.name}.mjpeg"
        rows = []
        q: queue.Queue = queue.Queue()

        def writer():
            with mjpeg.open("wb") as f:
                while (data := q.get()) is not None:
                    f.write(data)

        wt = threading.Thread(target=writer, daemon=True)
        wt.start()
        try:
            while True:
                msg = self._next_message()
                if msg.get("status") == "frame":
                    fr = msg["frame"]
                    data = self.io.read_exact(fr["size_bytes"], timeout=10)
                    rows.append({"seq": fr["seq"], "ticks_ms": fr.get("ticks_ms"),
                                 "host_rx_mono": round(time.monotonic(), 4),
                                 "bytes": len(data), "width": fr["width"],
                                 "height": fr["height"]})
                    q.put(data)
                elif msg.get("status") == "completed":
                    self.rec["board_done"] = msg.get("output") or {}
                    break
                else:
                    self.rec["resyncs"] = self.rec.get("resyncs", 0) + 1
        except Exception as exc:  # noqa: BLE001 - recorded, never silent
            self.rec["error"] = repr(exc)
        q.put(None)
        wt.join()
        self.rows = rows
        with (self.out / f"{self.name}_frames.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["seq", "ticks_ms", "host_rx_mono", "bytes",
                                              "width", "height"])
            w.writeheader()
            w.writerows(rows)

    def stop(self) -> None:
        try:
            # Any byte ends the stream; a bare newline because a board whose stream already
            # ended (trigger timeout, max_seconds) is back in its line loop, where "\n" is
            # ignored but any other byte would corrupt the next command (QE #115).
            self.io._t.write(b"\n")
        except OSError as exc:
            self.rec["stop_error"] = str(exc)

    def abort(self) -> None:
        """Idempotent cleanup for any exit path: stop the stream, release the port."""
        try:
            if getattr(self, "io", None) is not None:
                self.io._t.write(b"\n")  # see stop(): never leave a byte in the line buffer
        except Exception:  # noqa: BLE001 - best effort; the board times out by itself
            pass
        self.cam.close()

    def finish(self) -> dict:
        if self.thread:
            self.thread.join(timeout=30)
        self.cam.close()
        rows = getattr(self, "rows", [])
        sync = (self.rec.get("board_done") or {}).get("sync") or {}
        fire_rx = sync.get("fire_rx_ms")
        fire_host = self.rec.get("fire_host_mono")
        times = [fire_host + (r["ticks_ms"] - fire_rx) / 1000 for r in rows
                 if r["ticks_ms"] is not None and fire_rx is not None and fire_host]
        mjpeg = self.out / f"{self.name}.mjpeg"
        sent = (self.rec.get("board_done") or {}).get("frames")
        self.rec["frames_lost"] = (sent - len(rows)) if isinstance(sent, int) else None
        self.rec.update(frames=len(rows), first_frame_host_mono=times[0] if times else None,
                        last_frame_host_mono=times[-1] if times else None,
                        fps=round((len(times) - 1) / (times[-1] - times[0]), 2)
                        if len(times) > 1 and times[-1] > times[0] else None,
                        max_gap_ms=round(max((b - a) * 1000 for a, b in zip(times, times[1:])), 1)
                        if len(times) > 1 else None,
                        dims=f"{rows[0]['width']}x{rows[0]['height']}" if rows else None,
                        file=mjpeg.name, bytes=mjpeg.stat().st_size if mjpeg.exists() else 0,
                        sha256=sha256(mjpeg) if mjpeg.exists() else None,
                        board_sync=sync)
        self.rec["ok"] = bool(rows) and "error" not in self.rec and bool(times)
        return self.rec


class ImxVideo:
    """IMX708 hardware H.264 via rpicam-vid: records from the arm with per-frame metadata; the
    fire only marks t=0 (frames are trimmed to the fire by their SensorTimestamp)."""

    name = "imx708"

    def __init__(self, out: Path, width: int, height: int, fps: int, lens: float | None,
                 warmup_s: float, buffers: int):
        self.out, self.warmup_s = out, warmup_s
        self.cmd = ["rpicam-vid", "-n", "-t", "0", "--codec", "h264", "--inline",
                    "--width", str(width), "--height", str(height), "--framerate", str(fps),
                    "--metadata", "imx708.meta.json", "--metadata-format", "json",
                    "--save-pts", "imx708.pts", "-o", "imx708.h264"]
        if buffers:
            # CMA (nereus002 2026-10-14, 6 s 1080p H.264, n=1 each): default buffers took free
            # CMA to 0 MB, --buffer-count 4 kept 16 MB (3 kept 21 MB but dropped 25 % of frames).
            # ci-record hard-reset this Pi with CmaFree at 16 kB, so never run at the edge.
            self.cmd[1:1] = ["--buffer-count", str(buffers)]
        if lens is not None:
            self.cmd[1:1] = ["--autofocus-mode", "manual", "--lens-position", str(lens)]
        self.rec: dict = {"camera": "imx708", "ok": False, "cmd": " ".join(self.cmd)}

    def arm(self) -> None:
        self.log = (self.out / "imx708.log").open("wb")
        self.proc = subprocess.Popen(self.cmd, cwd=self.out, stdout=self.log,
                                     stderr=subprocess.STDOUT)
        t0 = time.monotonic()
        while time.monotonic() - t0 < self.warmup_s:
            if self.proc.poll() is not None:
                raise RuntimeError(f"rpicam-vid exited rc={self.proc.returncode} (imx708.log)")
            time.sleep(0.05)

    def fire(self) -> float:
        t = time.monotonic()  # t=0 mark: already recording, frames are timestamped
        self.rec["fire_host_mono"] = t
        return t

    def stop(self) -> None:
        self.rec["stop_host_mono"] = time.monotonic()
        if self.proc.poll() is None:
            os.kill(self.proc.pid, signal.SIGINT)  # clean exit: file + metadata closed

    def abort(self) -> None:
        """Idempotent cleanup for any exit path: rpicam-vid must never outlive this script
        (it would hold the camera and keep filling the SD card)."""
        proc = getattr(self, "proc", None)
        if proc is not None and proc.poll() is None:
            os.kill(proc.pid, signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        log = getattr(self, "log", None)
        if log is not None and not log.closed:
            log.close()

    def finish(self) -> dict:
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.log.close()
        self.rec["rc"] = self.proc.returncode
        h264, meta = self.out / "imx708.h264", self.out / "imx708.meta.json"
        try:
            frames = json.loads(meta.read_text())
        except (OSError, ValueError):
            frames = []
        allts = [f["SensorTimestamp"] / 1e9 for f in frames if f.get("SensorTimestamp")]
        fire, stop_t = self.rec["fire_host_mono"], self.rec.get("stop_host_mono", 1e18)
        times = [t for t in allts if fire <= t <= stop_t]  # the synchronized window
        self.rec.update(file=h264.name, bytes=h264.stat().st_size if h264.exists() else 0,
                        sha256=sha256(h264) if h264.exists() else None,
                        frames_total=len(allts), preroll_frames=sum(t < fire for t in allts),
                        scaler_crop=frames[0].get("ScalerCrop") if frames else None,
                        frames=len(times),
                        first_frame_host_mono=times[0] if times else None,
                        fps=round((len(times) - 1) / (times[-1] - times[0]), 2)
                        if len(times) > 1 and times[-1] > times[0] else None,
                        max_gap_ms=round(max((b - a) * 1000 for a, b in zip(times, times[1:])),
                                         1) if len(times) > 1 else None)
        self.rec["ok"] = self.rec["rc"] == 0 and self.rec["bytes"] > 0 and bool(times)
        return self.rec


def host_sampler(path: Path, stop: threading.Event) -> None:
    """1 Hz: CPU temp, load, MemAvailable, CmaFree, throttled — the ci-record crash signals."""
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["mono_s", "cpu_c", "load1", "mem_avail_mb", "cma_free_mb", "throttled"])
        while not stop.is_set():
            mem = dict(ln.split(":", 1) for ln in Path("/proc/meminfo").read_text().splitlines())
            try:
                temp = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
            except (OSError, ValueError):
                temp = None
            thr = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                                 text=True).stdout.strip().split("=")[-1]
            w.writerow([round(time.monotonic(), 2), temp, os.getloadavg()[0],
                        int(mem["MemAvailable"].split()[0]) // 1024,
                        int(mem.get("CmaFree", "0 kB").split()[0]) // 1024, thr])
            f.flush()
            stop.wait(1.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--cameras", default="imx708,openmv_n6,openmv_ae3")
    ap.add_argument("--imx-size", default="1920x1080")
    ap.add_argument("--imx-fps", type=int, default=30)
    ap.add_argument("--lens-position", type=float, default=0.8)
    ap.add_argument("--imx-buffers", type=int, default=4,
                    help="rpicam-vid --buffer-count (CMA headroom; 0 = rpicam default)")
    ap.add_argument("--board-framesize", default="VGA", help="QVGA | VGA | HD")
    ap.add_argument("--board-quality", type=int, default=70)
    ap.add_argument("--board-fps", type=float, default=10,
                    help="board frame cap, both boards (0 = as fast as each can). 10: the AE3's "
                         "VGA ceiling, and the N6 (full-speed USB) dropped bytes at 30-63 fps "
                         "over minutes (soak 2026-10-14)")
    a = ap.parse_args()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(a.out) if a.out else ROOT / "results" / "video" / ts
    out.mkdir(parents=True, exist_ok=True)
    cams = a.cameras.split(",")
    w, h = (int(v) for v in a.imx_size.split("x"))
    board_settings = {"framesize": a.board_framesize, "jpeg_quality": a.board_quality,
                      "fps": a.board_fps, "max_seconds": int(a.seconds + 60),
                      "warmup_ms": 2000}
    devs = []
    if "imx708" in cams:
        devs.append(ImxVideo(out, w, h, a.imx_fps, a.lens_position, warmup_s=3.0,
                             buffers=a.imx_buffers))
    devs += [BoardVideo(n, s, out, board_settings) for n, s in BOARDS.items() if n in cams]
    print(f"sync_video: {ts} -> {out} cameras {cams} {a.seconds}s", flush=True)

    stop = threading.Event()
    sampler = threading.Thread(target=host_sampler, args=(out / "host_stats.csv", stop),
                               daemon=True)
    sampler.start()
    errors: dict = {}

    def arm(d):
        try:
            d.arm()
        except Exception as exc:  # noqa: BLE001
            errors[d.name] = f"arm: {exc!r}"

    results: dict = {}
    fire: dict = {}
    try:
        threads = [threading.Thread(target=arm, args=(d,)) for d in devs]
        [t.start() for t in threads]
        [t.join(timeout=60) for t in threads]
        armed = [d for d in devs if d.name not in errors]
        print(f"sync_video: armed {[d.name for d in armed]} errors {errors}", flush=True)
        for d in list(armed):  # back to back, sub-ms apart; a board not reading fails alone
            try:
                fire[d.name] = d.fire()
            except Exception as exc:  # noqa: BLE001
                errors[d.name] = f"fire: {exc!r}"
                armed.remove(d)
        time.sleep(a.seconds)
        for d in armed:
            try:
                d.stop()
            except Exception as exc:  # noqa: BLE001
                errors[d.name] = f"stop: {exc!r}"
        for d in armed:
            try:
                results[d.name] = d.finish()
            except Exception as exc:  # noqa: BLE001
                errors[d.name] = f"finish: {exc!r}"
    finally:
        for d in devs:  # whatever happened above: no rpicam-vid / open port survives
            d.abort()
        stop.set()
        sampler.join(timeout=3)
    for name, err in errors.items():
        results[name] = {"camera": name, "ok": False, "error": err}

    # Start skew: each camera's first frame at/after the fire, all on the Pi monotonic clock
    # (IMX708: SensorTimestamp; boards: snapshot ticks mapped through the fire handshake).
    firsts = {n: r.get("first_frame_host_mono") for n, r in results.items()}
    firsts = {n: t for n, t in firsts.items() if t}
    t0 = min(firsts.values()) if firsts else None
    summary = {
        "utc": ts, "seconds": a.seconds, "args": vars(a),
        "fire_host_mono": fire, "start_offsets_ms": {n: round((t - t0) * 1000, 1)
                                                    for n, t in firsts.items()} if t0 else {},
        "start_skew_ms": round((max(firsts.values()) - t0) * 1000, 1) if len(firsts) > 1
        else None,
        "cameras": results,
        "ok": all(r.get("ok") for r in results.values()) and len(results) == len(cams),
    }
    stats = list(csv.DictReader((out / "host_stats.csv").open()))
    if stats:
        summary["host"] = {
            "min_cma_free_mb": min(int(r["cma_free_mb"]) for r in stats),
            "min_mem_avail_mb": min(int(r["mem_avail_mb"]) for r in stats),
            "max_cpu_c": max(float(r["cpu_c"] or 0) for r in stats),
            "throttled": sorted({r["throttled"] for r in stats})}
    (out / "video_sync.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    for n, r in results.items():
        print(f"  {n}: {'ok' if r.get('ok') else 'FAIL'} frames {r.get('frames')} fps "
              f"{r.get('fps')} max_gap {r.get('max_gap_ms')} ms bytes {r.get('bytes')} "
              f"lost {r.get('frames_lost')} resyncs {r.get('resyncs', 0)} "
              f"{str(r.get('error', ''))[:120]}", flush=True)
    print(f"sync_video: start skew {summary['start_skew_ms']} ms offsets "
          f"{summary['start_offsets_ms']} host {summary.get('host')} -> "
          f"{'OK' if summary['ok'] else 'FAIL'}", flush=True)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
