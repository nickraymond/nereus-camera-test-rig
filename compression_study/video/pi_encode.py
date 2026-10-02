"""On-device video encode bench (Pi Zero 2 W) — H.264 hardware vs AV1 software.

    python -m compression_study.video.pi_encode --sources ~/video_study/sources \
        --out ~/video_study/enc --budgets 50 100 200 400 800 1600

For every source × encoder × budget (kB per clip): one ffmpeg encode from the lossless FFV1
source, timed (``-benchmark``: wall, user+sys CPU, max RSS) with the UPS power meter sampled
every ~0.25 s for energy. One JSON line per job in ``<out>/jobs.jsonl``; the encoded files
go to ``<out>/<source>/<encoder>_<budget>k.mp4`` and are scored on the Mac (``score.py``).

Encoders (``ENCODERS``): ``h264_hw`` = the BCM2837 hardware encoder (V4L2 M2M, High profile,
no B-frames, VBR); ``av1_svt_p<N>`` = SVT-AV1 (BSD) at preset N, VBR. One key frame per clip
(``-g 600`` > frames) for both. Rate target = budget × 8 / clip duration.

Memory safety (the Zero has 415 MB and Nick's services run beside the bench): each ffmpeg
gets ``oom_score_adj = 1000`` (the kernel kills it first), and a watchdog kills it if
MemAvailable drops below ``MIN_AVAIL_MB`` — a killed job is recorded as ``oom``, never retried.
The decode cost of the FFV1 source is measured once per source (``decode_only`` jobs) and is
part of every total; it is not the device's cost (a device decodes its own master instead).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

from compression_study.phase2.pi_run_probe import PowerSampler, guard_idle  # noqa: E402

MIN_AVAIL_MB = 45
GOP = 600

ENCODERS = {
    "h264_hw": lambda br, extra: ["-c:v", "h264_v4l2m2m", "-b:v", str(br), "-g", str(GOP)],
    "av1_svt_p12": lambda br, extra: _svt(12, br, extra),
    "av1_svt_p10": lambda br, extra: _svt(10, br, extra),
    "av1_svt_p8": lambda br, extra: _svt(8, br, extra),
}


def _svt(preset: int, br: int, extra: str) -> list[str]:
    args = ["-c:v", "libsvtav1", "-preset", str(preset), "-b:v", str(br), "-g", str(GOP)]
    return args + (["-svtav1-params", extra] if extra else [])


def mem_available_mb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024
    return 1e9


def run_ffmpeg(args: list[str], min_avail_mb: float = MIN_AVAIL_MB) -> dict:
    """Run one ffmpeg with -benchmark under the memory watchdog and the power sampler."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-benchmark", "-y", *args]
    env = dict(os.environ, SVT_LOG="1")

    def preexec():
        Path("/proc/self/oom_score_adj").write_text("1000")

    power = PowerSampler()
    power.start()
    t0 = time.time()
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        preexec_fn=preexec,
    )
    killed, min_avail = [], [1e9]

    def watchdog():
        while p.poll() is None:
            a = mem_available_mb()
            min_avail[0] = min(min_avail[0], a)
            if a < min_avail_mb:
                killed.append(a)
                p.send_signal(signal.SIGKILL)
                return
            time.sleep(0.2)

    wd = threading.Thread(target=watchdog, daemon=True)
    wd.start()
    _, err = p.communicate()
    wall = time.time() - t0
    power.stop_flag = True
    power.join(timeout=5)
    # "bench: utime=0.785s stime=0.079s rtime=0.654s" / "bench: maxrss=63112KiB"
    bench = dict(kv.split("=", 1) for kv in re.findall(r"\b(?:[us]time|rtime|maxrss)=\S+", err))
    s = power.samples
    energy = sum(
        (b[0] - a[0]) * (a[1] or 0) * (a[2] or 0) / 1e6 for a, b in zip(s, s[1:])
    )  # mV × mA → W·s
    return {
        "rc": p.returncode,
        "status": "oom" if killed else ("ok" if p.returncode == 0 else "fail"),
        "wall_s": round(wall, 2),
        "cpu_s": round(
            float(bench.get("utime", "0s")[:-1]) + float(bench.get("stime", "0s")[:-1]), 2
        )
        if bench
        else None,
        "maxrss_MB": round(int(bench["maxrss"][:-3]) / 1024, 1) if "maxrss" in bench else None,
        "min_avail_MB": round(min_avail[0], 1),
        "energy_J": round(energy, 1),
        "mean_W": round(energy / wall, 3) if wall else None,
        "stderr_tail": err.strip().splitlines()[-3:] if p.returncode else [],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", nargs="*", default=[], help="source stems (default: all)")
    ap.add_argument("--encoders", nargs="*", default=["h264_hw", "av1_svt_p12"])
    ap.add_argument(
        "--budgets", nargs="*", type=int, default=[50, 100, 200, 400, 800, 1600], help="kB per clip"
    )
    ap.add_argument("--svt-params", default="", help="extra -svtav1-params for AV1 jobs")
    ap.add_argument("--frames", type=int, default=0, help="limit frames (tuning runs)")
    ap.add_argument("--tag", default="")
    ap.add_argument(
        "--raw-input",
        type=Path,
        default=None,
        help="decode each source once (untimed) to raw YUV in this folder and feed "
        "the encoders from it, so only the encoder's own memory and time count",
    )
    ap.add_argument(
        "--min-avail",
        type=float,
        default=MIN_AVAIL_MB,
        help="kill an encode when MemAvailable falls below this (MB)",
    )
    a = ap.parse_args(argv)
    guard_idle()
    meta = json.loads((a.sources / "sources.json").read_text())
    stems = a.only or [k for k in meta if not k.startswith("_") and meta[k].get("encode", True)]
    a.out.mkdir(parents=True, exist_ok=True)
    log = (a.out / "jobs.jsonl").open("a")
    idle = PowerSampler()
    idle.start()
    time.sleep(10)
    idle.stop_flag = True
    idle.join()
    s = idle.samples
    idle_w = sum(
        (b[0] - a_[0]) * (a_[1] or 0) * (a_[2] or 0) / 1e6 for a_, b in zip(s, s[1:])
    ) / max(1e-9, s[-1][0] - s[0][0])
    log.write(json.dumps({"kind": "idle", "mean_W": round(idle_w, 3), "utc": time.time()}) + "\n")
    lim = ["-frames:v", str(a.frames)] if a.frames else []
    for stem in stems:
        m = meta[stem]
        src = a.sources / m["file"]
        dur = (a.frames / m["fps"]) if a.frames else m["duration_s"]
        r = run_ffmpeg(["-i", str(src), *lim, "-f", "null", "-"])
        log.write(json.dumps({"kind": "decode_only", "source": stem, **r}) + "\n")
        log.flush()
        print(stem, "decode_only", r["wall_s"], "s", flush=True)
        inp = ["-i", str(src), *lim]
        if a.raw_input:
            a.raw_input.mkdir(parents=True, exist_ok=True)
            raw = a.raw_input / f"{stem}.yuv"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(src),
                    *lim,
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "yuv420p",
                    str(raw),
                ],
                check=True,
            )
            inp = [
                "-f",
                "rawvideo",
                "-pix_fmt",
                "yuv420p",
                "-s",
                f"{m['width']}x{m['height']}",
                "-r",
                f"{m['fps']:.6f}",
                "-i",
                str(raw),
            ]
        (a.out / stem).mkdir(exist_ok=True)
        for enc in a.encoders:
            for kb in a.budgets:
                br = int(kb * 1000 * 8 / dur)
                out = a.out / stem / f"{enc}{a.tag}_{kb}k.mp4"
                r = run_ffmpeg(
                    [*inp, *ENCODERS[enc](br, a.svt_params), "-an", str(out)], a.min_avail
                )
                size = out.stat().st_size if out.exists() and r["status"] == "ok" else None
                rec = {
                    "kind": "encode",
                    "source": stem,
                    "encoder": enc + a.tag,
                    "input": "raw" if a.raw_input else "ffv1",
                    "svt_params": a.svt_params if enc.startswith("av1") else "",
                    "budget_kB": kb,
                    "target_bps": br,
                    "frames": a.frames or m["frames"],
                    "duration_s": round(dur, 3),
                    "file": str(out.relative_to(a.out)),
                    "size_B": size,
                    "fps": round((a.frames or m["frames"]) / r["wall_s"], 2),
                    **r,
                }
                log.write(json.dumps(rec) + "\n")
                log.flush()
                print(
                    stem,
                    enc + a.tag,
                    kb,
                    "kB ->",
                    size,
                    r["status"],
                    r["wall_s"],
                    "s",
                    r["maxrss_MB"],
                    "MB",
                    flush=True,
                )
        if a.raw_input:
            (a.raw_input / f"{stem}.yuv").unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
