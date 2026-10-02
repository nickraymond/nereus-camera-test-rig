"""Record a ~15 s clip from an OpenMV board over the rig's allowlisted ``start_stream``.

    python -m compression_study.video.n6_record --serial <usb serial> --out DIR \
        [--seconds 15] [--framesize HD] [--quality 95]

Runs on the Pi. The board streams framed JPEGs (snapshot → hardware JPEG → USB, one after
the other, no pipelining — ``capture_service.stream_frames``); this script keeps every
frame and its host arrival time. Output:

- ``master.mjpeg``  the frames concatenated (a valid MJPEG stream for ffmpeg ``-f mjpeg``)
- ``master.json``   frame count, host timestamps, measured fps, sizes, settings

No change to the board service: ``start_stream`` is already on the allowlist (§8).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

from host_tools.discover_openmv import find_port  # noqa: E402
from openmv.common import command_protocol as cp  # noqa: E402

from nereus_camera_test_rig.cameras.openmv_usb import WRITE_TIMEOUT, _SerialIO  # noqa: E402


def record(serial_number: str, out: Path, seconds: float, framesize: str, quality: int) -> dict:
    import serial

    dev = find_port(serial_number)
    if not dev:
        raise SystemExit(f"no OpenMV board with serial {serial_number}")
    out.mkdir(parents=True, exist_ok=True)
    ser = serial.Serial(dev, 115200, timeout=0.2, write_timeout=WRITE_TIMEOUT)
    io = _SerialIO(ser, default_timeout=10.0)
    settings = {"framesize": framesize, "jpeg_quality": quality, "max_seconds": int(seconds) + 10}
    times, sizes, dims = [], [], set()
    t_req = time.monotonic()
    try:
        io.write_message(cp.make_request("start_stream", "video-study", settings))
        with (out / "master.mjpeg").open("wb") as fh:
            while True:
                header = io.read_message(timeout=10.0)
                status = header.get("status")
                if status != "frame":
                    raise SystemExit(f"stream ended early: {header}")
                meta = header["frame"]
                data = io.read_exact(int(meta["size_bytes"]), timeout=10.0)
                now = time.monotonic()
                if not times:
                    t_first = now
                if now - t_first > seconds:
                    break  # this frame is past the window: drop it
                fh.write(data)
                times.append(now - t_first)
                sizes.append(len(data))
                dims.add((int(meta["width"]), int(meta["height"])))
    finally:
        ser.write(b"\n")  # stop signal
        ser.flush()
        tail = b""
        t_stop = time.monotonic()
        while time.monotonic() - t_stop < 5:  # drain the in-flight frame + completion line
            chunk = ser.read(65536)
            tail += chunk
            if b'"completed"' in tail:
                break
        ser.close()
    dt = [b - a for a, b in zip(times, times[1:])]
    meta = {
        "serial": serial_number,
        "framesize": framesize,
        "jpeg_quality": quality,
        "frames": len(times),
        "duration_s": times[-1] if times else 0.0,
        "fps_mean": (len(times) - 1) / times[-1] if len(times) > 1 else 0.0,
        "dt_ms": {"min": 1e3 * min(dt), "median": 1e3 * statistics.median(dt), "max": 1e3 * max(dt)}
        if dt
        else {},
        "first_frame_after_request_s": round(t_first - t_req, 3) if times else None,
        "dims": sorted(dims),
        "bytes_total": sum(sizes),
        "bytes_per_frame_mean": statistics.mean(sizes) if sizes else 0,
        "link_MBps": sum(sizes) / times[-1] / 1e6 if len(times) > 1 else 0.0,
        "times_s": [round(t, 4) for t in times],
    }
    (out / "master.json").write_text(json.dumps(meta, indent=1))
    return meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--framesize", default="HD")
    ap.add_argument("--quality", type=int, default=95)
    a = ap.parse_args(argv)
    m = record(a.serial, a.out, a.seconds, a.framesize, a.quality)
    print(json.dumps({k: v for k, v in m.items() if k != "times_s"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
