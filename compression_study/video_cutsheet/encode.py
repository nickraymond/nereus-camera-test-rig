"""Desk-emulation encoders per board (RULES.md §3). Each returns a dict with the bitstream path and size.

- mjpeg(): libjpeg-turbo cjpeg per frame, one quality for the clip, highest Q whose total <= target.
  'ae3' = OpenMV software encoder rule (AUTO subsampling by Q), 'n6' = VC8000 path (4:2:0, Q steps of 10).
- h264(): x264 via ffmpeg, 1 pass with VBV, bitrate searched into 95-100 % of the target.
- av1_pi(): the rig #116 Pi recipe (SVT-AV1 p6 low-memory, 2-pass), only if time remains.
The emitted file is exactly the bytes that would be sent (concatenated JPEGs / Annex-B / OBU).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .common import q, sh

BAND = (0.95, 1.00)


def _frames_ppm(ref: Path, tmp: Path) -> list[Path]:
    # cjpeg converts RGB -> YCbCr with the JFIF (BT.601) matrix: decode with the same matrix so the
    # JPEG's Y'CbCr matches the reference's (otherwise a BT.709-tagged reference shifts luma).
    sh(f"ffmpeg -v error -y -i {q(ref)} -vf scale=in_color_matrix=bt601:in_range=tv,format=rgb24 "
       f"{q(tmp / 'f%04d.ppm')}")
    return sorted(tmp.glob("f*.ppm"))


def _subsample(board: str, qual: int) -> str:
    if board == "n6":
        return "2x2"
    return "2x2" if qual <= 35 else ("2x1" if qual < 60 else "1x1")


def _jpeg_total(frames: list[Path], board: str, qual: int, out: Path | None = None) -> int:
    total, chunks = 0, []
    for f in frames:
        b = subprocess.run(["cjpeg", "-quality", str(qual), "-dct", "int", "-sample", _subsample(board, qual),
                            "-baseline", str(f)], check=True, capture_output=True).stdout
        total += len(b)
        if out is not None:
            chunks.append(b)
    if out is not None:
        out.write_bytes(b"".join(chunks))
    return total


def mjpeg(ref: Path, out: Path, target: int, board: str) -> dict:
    t0 = time.time()
    with tempfile.TemporaryDirectory(dir=out.parent) as td:
        frames = _frames_ppm(ref, Path(td))
        qs = list(range(10, 101, 10)) if board == "n6" else list(range(1, 101))
        sizes: dict[int, int] = {}
        lo, hi, best = 0, len(qs) - 1, None  # size grows with Q: binary search the highest Q under target
        while lo <= hi:
            mid = (lo + hi) // 2
            sizes[qs[mid]] = _jpeg_total(frames, board, qs[mid])
            if sizes[qs[mid]] <= target:
                best, lo = qs[mid], mid + 1
            else:
                hi = mid - 1
        if best is None:
            return {"ok": False, "reason": f"smallest Q={qs[0]} gives {sizes[qs[0]]} B > target {target} B",
                    "min_bytes": sizes[qs[0]], "tries": sizes, "frames": len(frames)}
        n = _jpeg_total(frames, board, best, out)
    return {"ok": True, "bytes": n, "quality": best, "subsampling": _subsample(board, best), "tries": sizes,
            "frames": len(frames), "frac": round(n / target, 4), "encode_s": round(time.time() - t0, 1)}


def mjpeg_floor(ref: Path, out: Path, board: str) -> dict:
    """The smallest file the board's JPEG knob can make (AE3 Q=1, N6 Q=10): the MJPEG floor."""
    t0 = time.time()
    qual = 10 if board == "n6" else 1
    with tempfile.TemporaryDirectory(dir=out.parent) as td:
        frames = _frames_ppm(ref, Path(td))
        n = _jpeg_total(frames, board, qual, out)
    return {"ok": True, "bytes": n, "quality": qual, "subsampling": _subsample(board, qual), "frames": len(frames),
            "encode_s": round(time.time() - t0, 1)}


def _x264(ref: Path, out: Path, kbps: float, fps: int, nfr: int, profile: str) -> int:
    if profile == "baseline":  # N6 hardware proxy: one key frame per clip
        prof = f"-profile:v baseline -g {nfr} -keyint_min {nfr}"
    elif profile == "high":  # IMX708 Pi hardware proxy: rpicam-vid defaults on Friday (high, level 4.2, intra 60)
        prof = "-profile:v high -level:v 4.2 -g 60 -keyint_min 60"
    else:  # "high_nob" sensitivity: as above without B-frames, like the Pi's hardware encoder (PR #88)
        prof = "-profile:v high -level:v 4.2 -g 60 -keyint_min 60 -bf 0"
    r = f"{kbps:.1f}k"
    sh(f"ffmpeg -v error -y -i {q(ref)} -c:v libx264 -threads 4 -preset veryfast {prof} -sc_threshold 0 "
       f"-b:v {r} -maxrate {r} -bufsize {kbps:.1f}k -pix_fmt yuv420p -r {fps} -f h264 {q(out)}")
    return out.stat().st_size


def h264(ref: Path, out: Path, target: int, fps: int, nfr: int, profile: str, max_tries: int = 6) -> dict:
    t0 = time.time()
    dur = nfr / fps
    kbps = target * 8 / dur / 1000 * 0.97
    tries, best = [], None
    tmp = out.with_suffix(".try.h264")
    for _ in range(max_tries):
        n = _x264(ref, tmp, kbps, fps, nfr, profile)
        tries.append({"kbps": round(kbps, 1), "bytes": n})
        if n <= target and (best is None or n > best[1]):
            best = (kbps, n)
            shutil.copyfile(tmp, out)
        if BAND[0] * target <= n <= target:
            break
        kbps *= (0.975 * target) / n
    tmp.unlink(missing_ok=True)
    if best is None:
        return {"ok": False, "reason": "no try under target", "tries": tries}
    return {"ok": True, "bytes": best[1], "kbps": round(best[0], 1), "tries": tries, "frames": nfr,
            "frac": round(best[1] / target, 4), "encode_s": round(time.time() - t0, 1)}


def av1_pi(ref: Path, out: Path, target: int, fps: int, nfr: int, max_tries: int = 4) -> dict:
    """rig #116 recipe: SvtAv1EncApp p6 --lp 1 --lookahead 0 --enable-tf 0, 2-pass, one key frame."""
    t0 = time.time()
    dur = nfr / fps
    kbps = target * 8 / dur / 1000 * 0.97
    tries, best = [], None
    with tempfile.TemporaryDirectory(dir=out.parent) as td:
        y4m, ivf, st = Path(td) / "in.y4m", Path(td) / "o.ivf", Path(td) / "pass.stats"
        sh(f"ffmpeg -v error -y -i {q(ref)} -pix_fmt yuv420p -strict -1 {q(y4m)}")
        for _ in range(max_tries):
            sh(f"SvtAv1EncApp --preset 6 --rc 1 --tbr {int(kbps)} --passes 2 --keyint {nfr + 1} --scd 0 "
               f"--lp 1 --lookahead 0 --enable-tf 0 --stats {q(st)} -i {q(y4m)} -b {q(ivf)}")
            obu = Path(td) / "o.obu"
            sh(f"ffmpeg -v error -y -i {q(ivf)} -c copy -f obu {q(obu)}")
            n = obu.stat().st_size
            tries.append({"kbps": int(kbps), "bytes": n})
            if n <= target and (best is None or n > best[1]):
                best = (int(kbps), n)
                shutil.copyfile(obu, out)
            if BAND[0] * target <= n <= target:
                break
            kbps *= (0.975 * target) / n
    if best is None:
        return {"ok": False, "reason": "no try under target", "tries": tries}
    return {"ok": True, "bytes": best[1], "kbps": best[0], "tries": tries, "frames": nfr,
            "frac": round(best[1] / target, 4), "encode_s": round(time.time() - t0, 1)}
