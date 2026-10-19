"""Score one encode against its own-frame-rate reference (RULES.md §4), on the Mac.

VMAF vmaf_v0.6.1 frame N vs frame N on one time base (mean, 5th percentile, worst frame + index);
SSIMULACRA2 on 10 evenly spread frames (RGB PNG, same conversion both sides). A decode with the
wrong frame count is an error, never padded.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .common import q, sh

VMAF_THREADS = 3


def _input(dist: Path, fps: int) -> str:
    """ffmpeg input args for a raw stream as sent."""
    fmt = {".mjpeg": "mjpeg", ".h264": "h264", ".obu": "obu"}[dist.suffix]
    return f"-f {fmt} -framerate {fps} -i {q(dist)}" if fmt != "obu" else f"-f obu -i {q(dist)}"


# One rendering for both sides: Y'CbCr labelled limited-range BT.709 before any RGB conversion
# (the Reef Check references are tagged BT.709, the AV1 OBU and the pool references carry no tag).
TAG = "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"


def _chain(fps: int) -> str:
    # JPEG decodes full-range: convert to the reference's limited-range yuv420p (no matrix change).
    return f"scale=out_range=tv,format=yuv420p,{TAG},settb=1/{fps},setpts=N"


def ref_chain(fps: int) -> str:
    return f"format=yuv420p,{TAG},settb=1/{fps},setpts=N"


def count(dist: Path, fps: int) -> int:
    out = sh(f"ffprobe -v error {_input(dist, fps).replace('-i ', '-i ', 1)} -count_frames -select_streams v:0 "
             f"-show_entries stream=nb_read_frames -of csv=p=0").stdout.strip()
    return int(out.split(",")[0])


def vmaf(dist: Path, ref: Path, fps: int, nfr: int, log: Path) -> dict:
    lavfi = (f"[0:v]{_chain(fps)}[d];[1:v]{ref_chain(fps)}[r];"
             f"[d][r]libvmaf=model=version=vmaf_v0.6.1:log_fmt=json:log_path={log}:n_threads={VMAF_THREADS}")
    sh(f"ffmpeg -v error {_input(dist, fps)} -i {q(ref)} -lavfi {q(lavfi)} -f null -")
    fr = json.loads(log.read_text())["frames"]
    if len(fr) != nfr:
        raise RuntimeError(f"{dist.name}: VMAF saw {len(fr)} frames, expected {nfr}")
    v = np.array([f["metrics"]["vmaf"] for f in fr])
    return {"vmaf_mean": round(float(v.mean()), 2), "vmaf_p5": round(float(np.percentile(v, 5)), 2),
            "vmaf_min": round(float(v.min()), 2), "worst_frame": int(v.argmin())}


def s2_frames(nfr: int) -> list[int]:
    return [round(k * (nfr - 1) / 9) for k in range(10)]


def _pngs(src_args: str, chain: str, frames: list[int], out: Path, prefix: str) -> list[Path]:
    sel = "+".join(f"eq(n\\,{i})" for i in frames)
    sh(f"ffmpeg -v error -y {src_args} -vf {q(chain + f',select={sel},format=rgb24')} -vsync 0 "
       f"{q(out / (prefix + '%02d.png'))}")
    got = sorted(out.glob(prefix + "*.png"))
    if len(got) != len(frames):
        raise RuntimeError(f"{prefix}: extracted {len(got)} PNGs, expected {len(frames)}")
    return got


def ssimulacra2(dist: Path, ref: Path, fps: int, nfr: int) -> dict:
    frames = s2_frames(nfr)
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        rp = _pngs(f"-i {q(ref)}", ref_chain(fps), frames, t, "r")
        dp = _pngs(_input(dist, fps), _chain(fps), frames, t, "d")
        s = []
        for a, b in zip(rp, dp):
            out = subprocess.run(["ssimulacra2", str(a), str(b)], check=True, capture_output=True, text=True).stdout
            s.append(float(out.strip().split()[-1]))
    return {"s2_mean": round(float(np.mean(s)), 2), "s2_min": round(float(np.min(s)), 2)}


def score(dist: Path, ref: Path, fps: int, nfr: int) -> dict:
    n = count(dist, fps)
    if n != nfr:
        raise RuntimeError(f"{dist.name}: decodes to {n} frames, expected {nfr}")
    log = dist.with_suffix(".vmaf.json")
    return {**vmaf(dist, ref, fps, nfr, log), **ssimulacra2(dist, ref, fps, nfr)}
