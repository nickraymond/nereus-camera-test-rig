"""Every frame of one synced clip (scripts/sync_video.py output), per camera, with its capture time.

Reuses scripts/matched_frames.py for the clock (board_frames / imx_frames: each frame's CAPTURE
time on the Pi's monotonic clock, spec rev 5) and scripts/video_clip_table.py for the chroma each
board really sent (jpeg_sof on every frame: passthrough condition 2, F18). Reads only the raw
files; writes nothing.

  - boards: <cam>.mjpeg is concatenated camera JPEGs, split by <cam>_frames.csv byte counts;
  - IMX708: imx708.h264 is decoded by ffmpeg (on PATH) to raw frames in stream order. Frame i of
    the decode is metadata record i of imx708.meta.json (matched_frames.py extracts by the same
    index). A clip whose decode count differs from the metadata count fails loudly.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BOARDS = ("openmv_n6", "openmv_ae3")
CAMERAS = ("imx708",) + BOARDS


@lru_cache(maxsize=None)
def _script(name: str):
    spec = importlib.util.spec_from_file_location(f"pool2_{name}", ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_sync(clip: Path) -> dict:
    return json.loads((clip / "video_sync.json").read_text())


def fire_time(sync: dict) -> float | None:
    """The clip's synchronized start on the Pi clock (the earliest camera fire)."""
    fires = [r.get("fire_host_mono") for r in (sync.get("cameras") or {}).values()]
    fires = [f for f in fires if f is not None]
    return min(fires) if fires else None


def camera_frames(clip: Path, cam: str, sync: dict | None = None) -> list[dict]:
    """[{"index", "t" (capture, Pi monotonic s), ...}] for one camera; [] if it has no frames."""
    sync = sync or load_sync(clip)
    mf = _script("matched_frames")
    if cam not in mf.usable_cameras(clip, sync):
        return []
    if cam == "imx708":
        return mf.imx_frames(clip)
    return [{**f, "index": f["seq"]} for f in mf.board_frames(clip, cam, sync)]


def imx_scaler_crop(clip: Path, sync: dict | None = None) -> str | None:
    """The IMX708 video's ScalerCrop as recorded ('(x, y)/WxH'); None if not recorded."""
    sync = sync or load_sync(clip)
    rec = (sync.get("cameras") or {}).get("imx708") or {}
    if rec.get("scaler_crop"):
        return rec["scaler_crop"]
    meta = clip / "imx708.meta.json"
    if meta.exists():
        for f in json.loads(meta.read_text()):
            if f.get("ScalerCrop"):
                return f["ScalerCrop"]
    return None


def board_chroma(clip: Path, cam: str) -> dict:
    """What a board really sent, from every frame's JPEG header (video_clip_table.mjpeg_frames):
    {"resolution_measured", "subsampling_measured" ("420", "444", or "a;b" if it changed),
    "bad_frames"}."""
    vct = _script("video_clip_table")
    import csv
    path = clip / f"{cam}_frames.csv"
    if not path.exists():
        return {"resolution_measured": None, "subsampling_measured": None, "bad_frames": None}
    sizes = [int(r["bytes"]) for r in csv.DictReader(path.open())]
    return vct.mjpeg_frames(clip, cam, sizes)


def iter_board_images(clip: Path, cam: str, frames: list[dict],
                      flags: int = cv2.IMREAD_COLOR) -> Iterator[tuple[dict, np.ndarray | None]]:
    """(frame, image) per frame, decoded from the camera's own JPEG bytes (None if undecodable)."""
    with (clip / f"{cam}.mjpeg").open("rb") as raw:
        for f in frames:
            raw.seek(f["offset"])
            buf = np.frombuffer(raw.read(f["bytes"]), np.uint8)
            yield f, cv2.imdecode(buf, flags)


def _h264_size(clip: Path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-f", "h264", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "csv=p=0",
                          str(clip / "imx708.h264")], check=True, capture_output=True, text=True)
    w, h = (int(v) for v in out.stdout.strip().split(",")[:2])
    return w, h


def iter_imx_images(clip: Path, frames: list[dict], gray: bool = False
                    ) -> Iterator[tuple[dict, np.ndarray]]:
    """(frame, image) for the IMX708 frames listed, decoding the whole stream once in order."""
    w, h = _h264_size(clip)
    ch = 1 if gray else 3
    want = {f["index"]: f for f in frames}
    proc = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-f", "h264", "-i",
                             str(clip / "imx708.h264"), "-f", "rawvideo", "-pix_fmt",
                             "gray" if gray else "bgr24", "-"], stdout=subprocess.PIPE)
    n, size = 0, w * h * ch
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            if n in want:
                img = np.frombuffer(buf, np.uint8).reshape(h, w, ch) if ch == 3 else \
                    np.frombuffer(buf, np.uint8).reshape(h, w)
                yield want[n], img
            n += 1
    finally:
        proc.stdout.close()
        proc.wait()
    total = len(json.loads((clip / "imx708.meta.json").read_text()))
    if n != total:
        raise RuntimeError(f"{clip.name}: imx708.h264 decoded {n} frames, meta.json has {total};"
                           " the index -> capture time map would be wrong")


def iter_images(clip: Path, cam: str, frames: list[dict], gray: bool = False
                ) -> Iterator[tuple[dict, np.ndarray | None]]:
    if cam == "imx708":
        yield from iter_imx_images(clip, frames, gray)
    else:
        yield from iter_board_images(clip, cam, frames,
                                     cv2.IMREAD_GRAYSCALE if gray else cv2.IMREAD_COLOR)
