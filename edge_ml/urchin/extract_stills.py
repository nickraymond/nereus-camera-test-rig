"""Video → stills for the Reef Check urchin study (PREREG rules E0–E5).

    python -m edge_ml.urchin.extract_stills inventory <video_dir> <out_dir>
    python -m edge_ml.urchin.extract_stills extract   <video_dir> <out_dir> [--decoder software]

`inventory` writes `inventory.csv` (ffprobe facts + SHA-256 per file). `extract` writes
`stills/<video>/<video>_tNNNNNN.jpg` and `stills.csv` (one row per 0.5 s candidate, with the
reason it was kept or dropped). The videos are only read.

Two decode passes per video, both through the same `fps=2` filter so frame n is the same frame:
pass 1 at 960 px (sharpness + dHash → selection), pass 2 at full resolution (save the kept ones).
Needs ffmpeg/ffprobe on PATH, numpy and OpenCV.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("urchin.extract")

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".lrv", ".360"}

# PREREG rules — change only through the PREREG amendment log.
CANDIDATE_STEP_S = 0.5  # E1
WINDOW_S = 2.0  # E2
SHARP_LONG_SIDE = 960  # E2
BLUR_FRACTION = 0.5  # E3
DUP_HAMMING = 6  # E4
JPEG_QUALITY = 95  # E5
# The decoder is part of the method: for 8-bit sources VideoToolbox and software decoding give
# different pixels, which can flip near-tie picks. PREREG: VideoToolbox, one decoder per out_dir.
DECODERS = ("videotoolbox", "software")
DEFAULT_DECODER = "videotoolbox"
PER_WINDOW = round(WINDOW_S / CANDIDATE_STEP_S)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class VideoInfo:
    path: Path
    width: int  # display orientation (after rotation)
    height: int
    duration_s: float
    fps: float
    codec: str
    creation_time: str
    rotation: int


def probe(path: Path) -> VideoInfo:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    meta = json.loads(out.stdout)
    if not meta.get("streams"):
        raise ValueError(f"{path}: no video stream")
    s, fmt = meta["streams"][0], meta.get("format", {})
    rotation = 0
    for sd in s.get("side_data_list", []):
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    rotation = rotation or int(s.get("tags", {}).get("rotate", 0))
    w, h = int(s["width"]), int(s["height"])
    if abs(rotation) % 180 == 90:
        w, h = h, w
    num, den = (s.get("avg_frame_rate") or "0/1").split("/")
    fps = float(num) / float(den) if float(den) else 0.0
    duration = float(s.get("duration") or fmt.get("duration") or 0.0)
    created = s.get("tags", {}).get("creation_time") or fmt.get("tags", {}).get("creation_time", "")
    return VideoInfo(path, w, h, duration, fps, s.get("codec_name", ""), created, rotation)


def _is_hidden(p: Path, root: Path) -> bool:
    return any(part.startswith(".") for part in p.relative_to(root).parts)


def find_videos(video_dir: Path) -> list[Path]:
    """Video files under video_dir, skipping hidden ones (exFAT/macOS `._` AppleDouble files)."""
    return sorted(
        p
        for p in video_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS and not _is_hidden(p, video_dir)
    )


def count_hidden_videos(video_dir: Path) -> int:
    return sum(
        1
        for p in video_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS and _is_hidden(p, video_dir)
    )


def frames(path: Path, width: int, height: int, scale: bool, decoder: str = DEFAULT_DECODER):
    """Yield BGR frames of the fps=2 stream, optionally scaled to width×height.

    "videotoolbox" keeps decoded frames on the GPU and downloads them explicitly, so if ffmpeg
    cannot use VideoToolbox it fails instead of silently decoding in software."""
    if decoder not in DECODERS:
        raise ValueError(f"decoder {decoder!r} not in {DECODERS}")
    vf = f"fps={1 / CANDIDATE_STEP_S:g}"
    hw = []
    if decoder == "videotoolbox":
        hw = ["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld"]
        vf += ",hwdownload,format=nv12|p010le"
    if scale:
        vf += f",scale={width}:{height}:flags=area"
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        *hw,
        "-i",
        str(path),
        "-vf",
        vf,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "pipe:1",
    ]
    size = width * height * 3
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(height, width, 3)
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.returncode not in (0, None):
        raise RuntimeError(f"ffmpeg failed on {path} (rc={proc.returncode}): {err.strip()[:500]}")


def sharpness(grey: np.ndarray) -> float:
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def dhash(grey: np.ndarray) -> int:
    small = cv2.resize(grey, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def select(sharp: list[float], hashes: list[int]) -> list[str]:
    """PREREG E2–E4 on per-candidate sharpness + dHash → one status per candidate.

    Status is "kept", "not_sharpest" (E2), "blur" (E3) or "duplicate" (E4).
    """
    n = len(sharp)
    status = ["not_sharpest"] * n
    best = []
    for start in range(0, n, PER_WINDOW):
        window = range(start, min(start + PER_WINDOW, n))
        best.append(max(window, key=lambda i: sharp[i]))
    if not best:
        return status
    floor = BLUR_FRACTION * float(np.median([sharp[i] for i in best]))
    prev_hash = None
    for i in best:
        if sharp[i] < floor:
            status[i] = "blur"
        elif prev_hash is not None and bin(hashes[i] ^ prev_hash).count("1") <= DUP_HAMMING:
            status[i] = "duplicate"
        else:
            status[i] = "kept"
            prev_hash = hashes[i]
    return status


def still_name(video: Path, index: int) -> str:
    tenths = round(index * CANDIDATE_STEP_S * 10)
    return f"{video.stem}_t{tenths:06d}.jpg"


def extract_video(info: VideoInfo, out_dir: Path, decoder: str = DEFAULT_DECODER) -> list[dict]:
    t0 = time.monotonic()
    long_side = max(info.width, info.height)
    k = SHARP_LONG_SIDE / long_side
    sw = max(2, round(info.width * k / 2) * 2)
    sh = max(2, round(info.height * k / 2) * 2)
    sharp, hashes = [], []
    for frame in frames(info.path, sw, sh, scale=True, decoder=decoder):
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sharp.append(sharpness(grey))
        hashes.append(dhash(grey))
    status = select(sharp, hashes)
    keep = {i for i, s in enumerate(status) if s == "kept"}

    still_dir = out_dir / "stills" / info.path.stem
    still_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for i, frame in enumerate(
        frames(info.path, info.width, info.height, scale=False, decoder=decoder)
    ):
        if i in keep:
            dest = still_dir / still_name(info.path, i)
            if not cv2.imwrite(str(dest), frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]):
                raise RuntimeError(f"could not write {dest}")
            saved += 1
    if saved != len(keep):
        raise RuntimeError(
            f"{info.path.name}: pass 2 saved {saved} of {len(keep)} kept stills "
            "(frame count differs between passes)"
        )
    log.info(
        "%s: %d candidates → %d stills (%.1f min video) in %.0f s",
        info.path.name,
        len(status),
        saved,
        info.duration_s / 60,
        time.monotonic() - t0,
    )
    return [
        {
            "video": info.path.name,
            "index": i,
            "t_s": round(i * CANDIDATE_STEP_S, 1),
            "sharpness": round(sharp[i], 2),
            "dhash": f"{hashes[i]:016x}",
            "status": status[i],
            "still": still_name(info.path, i) if status[i] == "kept" else "",
            "decoder": decoder,
        }
        for i in range(len(status))
    ]


def cmd_inventory(video_dir: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in find_videos(video_dir):
        row = {
            "file": str(path.relative_to(video_dir)),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        try:
            info = probe(path)
            row.update(
                width=info.width,
                height=info.height,
                duration_s=round(info.duration_s, 2),
                fps=round(info.fps, 3),
                codec=info.codec,
                rotation=info.rotation,
                creation_time=info.creation_time,
                error="",
            )
        except (subprocess.CalledProcessError, ValueError, KeyError) as exc:
            row["error"] = str(exc)[:200]
            log.error("unreadable video %s: %s", path, exc)
        rows.append(row)
    fields = [
        "file",
        "size_bytes",
        "sha256",
        "width",
        "height",
        "duration_s",
        "fps",
        "codec",
        "rotation",
        "creation_time",
        "error",
    ]
    with (out_dir / "inventory.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    log.info("inventory: %d files → %s", len(rows), out_dir / "inventory.csv")
    return 0 if rows else 1


def ffmpeg_version() -> str:
    out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=False)
    return out.stdout.splitlines()[0] if out.stdout else "unknown"


def cmd_extract(video_dir: Path, out_dir: Path, decoder: str = DEFAULT_DECODER) -> int:
    prev = out_dir / "extract_run.json"
    if prev.exists() and (old := json.loads(prev.read_text()).get("decoder")) != decoder:
        raise ValueError(
            f"{out_dir} was extracted with decoder {old!r}; refusing {decoder!r} (pixels differ, "
            "so still names and picks could change). Use a new out_dir."
        )
    rows, failed = [], []
    started = datetime.now(UTC).isoformat(timespec="seconds")
    videos = find_videos(video_dir)
    hidden = count_hidden_videos(video_dir)
    if hidden:
        log.info("skipping %d hidden video-named files (e.g. exFAT ._ sidecars)", hidden)
    for path in videos:
        try:
            rows += extract_video(probe(path), out_dir, decoder)
        except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
            failed.append(path.name)
            log.error("extract failed for %s: %s", path, exc)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "stills.csv").open("w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "video",
                "index",
                "t_s",
                "sharpness",
                "dhash",
                "status",
                "still",
                "decoder",
            ],
        )
        w.writeheader()
        w.writerows(rows)
    kept = sum(r["status"] == "kept" for r in rows)
    run = {
        "video_dir": str(video_dir),
        "videos": len(videos),
        "hidden_skipped": hidden,
        "failed": failed,
        "candidates": len(rows),
        "kept": kept,
        "decoder": decoder,
        "ffmpeg": ffmpeg_version(),
        "rules": {
            "candidate_step_s": CANDIDATE_STEP_S,
            "window_s": WINDOW_S,
            "sharp_long_side": SHARP_LONG_SIDE,
            "blur_fraction": BLUR_FRACTION,
            "dup_hamming": DUP_HAMMING,
            "jpeg_quality": JPEG_QUALITY,
        },
        "started_utc": started,
        "finished_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (out_dir / "extract_run.json").write_text(json.dumps(run, indent=1) + "\n")
    log.info(
        "extract: %d candidates, %d stills kept, %d videos failed %s",
        len(rows),
        kept,
        len(failed),
        failed or "",
    )
    return 1 if failed or not kept else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("command", choices=["inventory", "extract"])
    ap.add_argument("video_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument(
        "--decoder",
        choices=DECODERS,
        default=DEFAULT_DECODER,
        help="PREREG: videotoolbox (Mac hardware, low heat). Pixels differ between decoders.",
    )
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.command == "inventory":
        return cmd_inventory(args.video_dir, args.out_dir)
    return cmd_extract(args.video_dir, args.out_dir, args.decoder)


if __name__ == "__main__":
    sys.exit(main())
