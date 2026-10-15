"""Score one encoded clip against its source (PREREG metrics), on the Mac.

What a viewer would see is rebuilt first: the candidate is decoded, frames are held (or
interpolated, labelled) back to 10 fps, and lower resolutions are upscaled bicubic to 1280×720.
Then, frame N vs frame N on one time base (the PR #88 pairing fix):

- VMAF (vmaf_v0.6.1) mean / 5th percentile / min, PSNR-Y, SSIM over all 293 frames;
- card-ROI SSIM-Y and PSNR-Y inside the per-camera card box (native video coordinates);
- SSIMULACRA2 on 10 fixed frames (RGB PNG, same conversion for both sides).

A candidate that decodes to the wrong number of frames is an error, never padded silently.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .common import FPS, NFR, S2_FRAMES, H, W, card_box, q, sh, src_path

VMAF_THREADS = 4


def count_frames(path: Path, dec: str = "") -> int:
    out = subprocess.run(
        f"ffprobe -v error {dec} -count_frames -select_streams v:0 "
        f"-show_entries stream=nb_read_frames -of csv=p=0 {q(path)}",
        shell=True,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return int(out.split(",")[0])


def display_chain(k: int, wh: tuple[int, int], display: str, nfr: int = NFR) -> str:
    """Filter chain turning the decoded candidate into nfr (293) frames of 1280×720 at 10 fps."""
    parts = ["format=yuv420p", f"settb=1/{FPS}", f"setpts=N*{k}"]
    if k > 1:
        if display == "minterp":  # server-side motion-compensated interpolation (labelled)
            parts.append(f"minterpolate=fps={FPS}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
        else:  # frame hold: what a plain player shows
            parts.append(f"fps={FPS}:round=down")
        parts.append(f"tpad=stop_mode=clone:stop={k}")
    parts.append(f"trim=end_frame={nfr}")
    if tuple(wh) != (W, H):
        parts.append(f"scale={W}:{H}:flags=bicubic")
    parts += ["format=yuv420p", f"settb=1/{FPS}", "setpts=N"]
    return ",".join(parts)


def ref_pngs(clip: str, cache: Path) -> list[Path]:
    """The 10 SSIMULACRA2 reference frames of a clip (cached)."""
    d = cache / "ref_png" / clip
    files = [d / f"f{n:03d}.png" for n in S2_FRAMES]
    if all(f.exists() for f in files):
        return files
    d.mkdir(parents=True, exist_ok=True)
    sel = "+".join(f"eq(n\\,{n})" for n in S2_FRAMES)
    with tempfile.TemporaryDirectory() as td:
        sh(
            f"ffmpeg -hide_banner -v error -y -i {q(src_path(clip))} "
            f"-vf \"select='{sel}',format=rgb24\" -fps_mode passthrough {q(Path(td) / '%02d.png')}"
        )
        for i, f in enumerate(files):
            shutil.move(Path(td) / f"{i + 1:02d}.png", f)
    return files


def _mean_stat(path: Path, key: str) -> float:
    vals = []
    for ln in path.read_text().splitlines():
        m = re.search(rf"{key}:(\S+)", ln)
        if m:
            v = m.group(1)
            vals.append(100.0 if v == "inf" else float(v))
    return float(np.mean(vals)) if vals else float("nan")


def score(
    clip: str,
    cand_file: Path,
    *,
    k: int = 1,
    wh: tuple[int, int] = (W, H),
    display: str = "hold",
    dec: str = "",
    expect_frames: int | None = None,
    cache: Path,
    s2: bool = True,
    nfr: int = NFR,
) -> dict:
    """nfr < 293 = a shorter clip (the first nfr source frames; exploratory duration knob)."""
    if expect_frames is not None:
        n = count_frames(cand_file)
        if n != expect_frames:
            raise RuntimeError(f"{cand_file}: decodes to {n} frames, expected {expect_frames}")
    x, y, w, h = card_box(clip)
    sel = "+".join(f"eq(n\\,{n})" for n in S2_FRAMES)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        vlog, slog, plog = td / "v.json", td / "ssim.txt", td / "psnr.txt"
        fc = (
            f"[0:v]{display_chain(k, wh, display, nfr)}[d];"
            f"[1:v]trim=end_frame={nfr},format=yuv420p,settb=1/{FPS},setpts=N[r];"
            "[d]split=3[d1][d2][d3];[r]split=3[r1][r2][r3];"
            f"[d1][r1]libvmaf=log_fmt=json:log_path={vlog}:n_threads={VMAF_THREADS}:"
            "feature=name=psnr|name=float_ssim[vm];"
            f"[d2]crop={w}:{h}:{x}:{y}[dc];[r2]crop={w}:{h}:{x}:{y},split[rc1][rc2];"
            f"[dc][rc1]ssim=stats_file={slog}[ds];[ds][rc2]psnr=stats_file={plog}[cp];"
            f"[d3]select='{sel}',format=rgb24[dp];[r3]nullsink"
        )
        sh(
            f"ffmpeg -hide_banner -v error -y {dec} -i {q(cand_file)} -i {q(src_path(clip))} "
            f'-filter_complex "{fc}" -map "[vm]" -f null - -map "[cp]" -f null - '
            f'-map "[dp]" -fps_mode passthrough {q(td / "d%02d.png")}'
        )
        frames = json.loads(vlog.read_text())["frames"]
        if len(frames) != nfr:
            raise RuntimeError(f"{cand_file}: scored {len(frames)} frames, expected {nfr}")
        v = np.array([f["metrics"]["vmaf"] for f in frames])
        rec = {
            "vmaf": round(float(v.mean()), 2),
            "vmaf_p5": round(float(np.percentile(v, 5)), 2),
            "vmaf_min": round(float(v.min()), 2),
            "psnr_y": round(float(np.mean([f["metrics"]["psnr_y"] for f in frames])), 2),
            "ssim": round(float(np.mean([f["metrics"]["float_ssim"] for f in frames])), 4),
            "card_ssim_y": round(_mean_stat(slog, "Y"), 4),
            "card_psnr_y": round(_mean_stat(plog, "psnr_y"), 2),
            "vmaf_per_frame": [round(float(a), 1) for a in v],
        }
        if s2 and nfr == NFR:
            refs = ref_pngs(clip, cache)
            s2v = []
            for i, ref in enumerate(refs):
                out = subprocess.run(
                    ["ssimulacra2", str(ref), str(td / f"d{i + 1:02d}.png")],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                s2v.append(float(out.strip().split()[-1]))
            rec["ssimulacra2"] = round(float(np.mean(s2v)), 2)
            rec["ssimulacra2_min"] = round(float(np.min(s2v)), 2)
    return rec
