"""Score every encoded clip against its lossless source (Mac).

    python -m compression_study.video.score --data <data>   # reads enc_pi/ and enc_mac/

Per clip: VMAF (vmaf_v0.6.1, the 1080p-TV model) mean / 5th percentile / worst frame, PSNR-Y,
SSIM, real bitrate, frame and key-frame count. 720p IMX clips are also scored upscaled
(bicubic) against the 1080p source (``vmaf_1080``): what a viewer sees full-screen, so 720p
and 1080p can be compared at the same budget; generally every clip is also scored against
its source's ``display_ref`` (``vmaf_display``: 1080p for the IMX, 1280x800 for the N6, same
frame times), so lower resolutions and frame rates compare on one scale. Results go to
``<data>/scores_<i>of<n>.jsonl`` per worker (``--shard i/n``); ``load_scores`` reads them
all. A clip already scored by any worker is skipped.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import zlib
from pathlib import Path

import numpy as np

THREADS = 8


def load_scores(data: Path) -> list[dict]:
    """All scored clips: ``scores_<i>of<n>.jsonl`` from every worker (and a plain scores.jsonl)."""
    rows = []
    for p in sorted(data.glob("scores_*of*.jsonl")) + [data / "scores.jsonl"]:
        if p.exists():
            rows += [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
    return rows


def vmaf(
    dist: Path, ref: Path, scale_to: tuple[int, int] | None = None, fill_fps: float | None = None
) -> dict:
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "v.json"
        up = f"scale={scale_to[0]}:{scale_to[1]}:flags=bicubic," if scale_to else ""
        if fill_fps:  # encoder dropped frames: repeat the previous one, as a player shows it
            up = f"fps=fps={fill_fps:.6f}:round=near," + up
        # Pair frame N with frame N on one shared time base. Container time bases differ
        # (mkv 1/1000, mp4 1/10240 or 1/15360), so pairing by timestamp - even rescaled
        # frame indices - picked the previous reference frame (found 2026-10-02 on the N6
        # clips: 20-77 misaligned frames per clip).
        fc = (
            f"[0:v]{up}format=yuv420p,settb=1/30,setpts=N[d];"
            f"[1:v]format=yuv420p,settb=1/30,setpts=N[r];"
            f"[d][r]libvmaf=log_fmt=json:log_path={log}:n_threads={THREADS}:"
            f"feature=name=psnr|name=float_ssim"
        )
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-v",
                "error",
                "-i",
                str(dist),
                "-i",
                str(ref),
                "-filter_complex",
                fc,
                "-fps_mode",
                "passthrough",
                "-f",
                "null",
                "-",
            ],
            check=True,
        )
        frames = json.loads(log.read_text())["frames"]
    v = np.array([f["metrics"]["vmaf"] for f in frames])
    py = np.array([f["metrics"]["psnr_y"] for f in frames])
    ss = np.array([f["metrics"]["float_ssim"] for f in frames])
    return {
        "vmaf": round(float(v.mean()), 2),
        "vmaf_p5": round(float(np.percentile(v, 5)), 2),
        "vmaf_min": round(float(v.min()), 2),
        "psnr_y": round(float(py.mean()), 2),
        "ssim": round(float(ss.mean()), 4),
        "n_frames": len(frames),
        "vmaf_per_frame": [round(float(x), 2) for x in v],
    }


def stream_info(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,profile,width,height,nb_frames,duration,bit_rate:packet=flags",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    j = json.loads(out.stdout)
    s = j["streams"][0]
    return {
        "codec": s["codec_name"],
        "profile": s.get("profile"),
        "width": s["width"],
        "height": s["height"],
        "keyframes": sum("K" in p["flags"] for p in j["packets"]),
        "packets": len(j["packets"]),
        "stream_bps": int(s.get("bit_rate") or 0),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--shard", default="0/1", help="i/n: score every n-th clip (parallel workers)")
    ap.add_argument("--threads", type=int, default=8)
    a = ap.parse_args(argv)
    global THREADS
    THREADS = a.threads
    shard, nshard = (int(x) for x in a.shard.split("/"))
    meta = json.loads((a.data / "sources" / "sources.json").read_text())
    out_path = a.data / f"scores_{shard}of{nshard}.jsonl"
    done = {r["path"] for r in load_scores(a.data)}
    with out_path.open("a") as out:
        for enc_dir in ("enc_pi", "enc_mac"):
            jobs = a.data / enc_dir / "jobs.jsonl"
            if not jobs.exists():
                continue
            for line in jobs.read_text().splitlines():
                job = json.loads(line)
                if job.get("kind") != "encode" or job.get("status") != "ok":
                    continue
                path = f"{enc_dir}/{job['file']}"
                if path in done:
                    continue
                if zlib.crc32(path.encode()) % nshard != shard:  # stable across workers
                    continue
                f = a.data / path
                src = meta[job["source"]]
                ref = a.data / "sources" / src["file"]
                n_src = src["frames"]
                info = stream_info(f)
                fill = src["fps"] if info["packets"] < n_src else None
                rec = {
                    "path": path,
                    "host": enc_dir,
                    **{
                        k: job[k]
                        for k in (
                            "source",
                            "encoder",
                            "budget_kB",
                            "target_bps",
                            "size_B",
                            "duration_s",
                        )
                    },
                    "real_bps": round(job["size_B"] * 8 / src["duration_s"]),
                    **info,
                    "dropped_frames": n_src - info["packets"],
                    **vmaf(f, ref, fill_fps=fill),
                }
                if rec["n_frames"] != n_src:
                    raise SystemExit(
                        f"{path}: {rec['packets']} packets / {rec['n_frames']} "
                        f"scored frames, source has {n_src}"
                    )
                disp = meta[src.get("display_ref", job["source"])]
                if disp is not src:  # lower resolution or frame rate: also score full-screen
                    v = vmaf(
                        f,
                        a.data / "sources" / disp["file"],
                        (disp["width"], disp["height"]),
                        fill_fps=fill,
                    )
                    rec["vmaf_display"], rec["vmaf_display_p5"] = v["vmaf"], v["vmaf_p5"]
                else:
                    rec["vmaf_display"], rec["vmaf_display_p5"] = rec["vmaf"], rec["vmaf_p5"]
                out.write(json.dumps(rec) + "\n")
                out.flush()
                done.add(path)
                print(path, rec["size_B"], rec["vmaf"], rec["vmaf_p5"], rec["psnr_y"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
