"""'Smart' pipelines beyond a plain codec call: ROI bit allocation, background + residual,
N-best-frames JPEG XL summary. Builders return shell commands (like candidates.py); the
display functions rebuild what a viewer sees as a lossless 293-frame file for scoring.

Coordinates: card boxes come from common.CARD_BOX (native 1280×720 video coordinates) and are
scaled to the coded frame size; SVT-AV1 ROI maps are per 64×64 superblock, raster order,
qindex offsets (−255..255; negative = more bits), one line ``<picture> <offsets...>``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from .candidates import GOP, Ctx
from .common import DUR_S, FPS, NFR, WIN_MAIN, H, W, card_box, q, sh, split_clip, src_path

HERE = Path(__file__).resolve().parent
PACK = f"python3 {q(HERE / 'pack.py')}"


# ------------------------------------------------------------------ ROI bit allocation


def write_roi_map(path: Path, clip: str, wh: tuple[int, int], card_off: int, rest_off: int) -> int:
    """One map for the whole clip (camera is static). Returns the number of card superblocks."""
    sx, sy = wh[0] / W, wh[1] / H
    x, y, w, h = card_box(clip)
    x0, y0, x1, y1 = x * sx, y * sy, (x + w) * sx, (y + h) * sy
    cols, rows = -(-wh[0] // 64), -(-wh[1] // 64)
    vals, n_card = [], 0
    for r in range(rows):
        for c in range(cols):
            inside = c * 64 + 64 > x0 and c * 64 < x1 and r * 64 + 64 > y0 and r * 64 < y1
            n_card += inside
            vals.append(card_off if inside else rest_off)
    path.write_text("0 " + " ".join(map(str, vals)) + "\n")
    return n_card


def svt_roi(preset: int, card_off: int, rest_off: int, wh=(W, H), extra: str = ""):
    from .candidates import svt_app

    def b(ctx: Ctx, kbps: float) -> str:
        m = ctx.tmp / "roi.txt"
        write_roi_map(m, ctx.clip, wh, card_off, rest_off)
        return svt_app(preset, f"--roi-map-file {q(m)} {extra}")(ctx, kbps)

    return b


# ------------------------------------------------------------------ N best frames as JPEG XL


def still_frames(n: int) -> list[int]:
    """Middle frame of each of n equal segments (the 'summary' picks; no content selection)."""
    return [int((j + 0.5) * NFR / n) for j in range(n)]


def segments(n: int) -> list[int]:
    edges = [round(j * NFR / n) for j in range(n + 1)]
    return [b - a for a, b in zip(edges, edges[1:])]


def jxl_stills(n: int, effort: int = 7):
    def b(ctx: Ctx, dist: float) -> str:
        sel = "+".join(f"eq(n\\,{i})" for i in still_frames(n))
        pngs = [ctx.tmp / f"{j + 1:02d}.png" for j in range(n)]
        jxls = [p.with_suffix(".jxl") for p in pngs]
        enc = " && ".join(
            f"cjxl {q(p)} {q(j)} -d {dist:.4f} -e {effort} --quiet" for p, j in zip(pngs, jxls)
        )
        return (
            f"ffmpeg -hide_banner -v error -y -i {q(ctx.src)} -vf \"select='{sel}',format=rgb24\" "
            f"-fps_mode passthrough {q(ctx.tmp / '%02d.png')} && {enc} && "
            f"{PACK} pack {q(ctx.out)} {' '.join(q(j) for j in jxls)}"
        )

    return b


def _write_ffv1(frames_rgb, out: Path) -> None:
    """Raw RGB frames -> lossless FFV1 (same default RGB<->YUV matrix as the PNG export)."""
    p = subprocess.Popen(
        f"ffmpeg -hide_banner -v error -y -f rawvideo -pix_fmt rgb24 -s {W}x{H} -r {FPS} -i - "
        f"-pix_fmt yuv420p -c:v ffv1 {q(out)}",
        shell=True,
        stdin=subprocess.PIPE,
    )
    for fr in frames_rgb:
        p.stdin.write(fr.tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError(f"ffv1 write failed: {out}")


def stills_display(n: int):
    def d(f: Path, clip: str, tmp: Path) -> tuple[Path, dict]:
        from .pack import unpack

        tmp.mkdir(parents=True, exist_ok=True)
        imgs, sizes = [], []
        for j, part in enumerate(unpack(f)):
            jx, png = tmp / f"s{j}.jxl", tmp / f"s{j}.png"
            jx.write_bytes(part)
            sizes.append(len(part))
            sh(f"djxl {q(jx)} {q(png)} --quiet")
            imgs.append(np.asarray(Image.open(png).convert("RGB")))
        if len(imgs) != n:
            raise RuntimeError(f"{f}: {len(imgs)} stills, expected {n}")
        seq = [im for im, k in zip(imgs, segments(n)) for _ in range(k)]
        out = tmp / "display.mkv"
        _write_ffv1(seq, out)
        # still quality on its own (vs its own source frame), not diluted by the hold
        s2 = []
        for j, i in enumerate(still_frames(n)):
            ref = tmp / f"ref{j}.png"
            sh(
                f"ffmpeg -hide_banner -v error -y -i {q(src_path(clip))} "
                f"-vf \"select='eq(n\\,{i})',format=rgb24\" "
                f"-frames:v 1 -update 1 {q(ref)}"
            )
            s2.append(
                float(
                    subprocess.run(
                        ["ssimulacra2", str(ref), str(tmp / f"s{j}.png")],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout.split()[-1]
                )
            )
        return out, {
            "still_bytes": sizes,
            "still_ssimulacra2": round(float(np.mean(s2)), 2),
            "still_ssimulacra2_min": round(float(np.min(s2)), 2),
        }

    return d


# ------------------------------------------------------------------ background + residual


def bg_residual(
    bg_q: float,
    preset: int = 4,
    bg_src: str = "median",
    bg_codec: str = "av1",
    extra: str = "",
    k: int = 1,
    step: int = 3,
    py: str | None = None,
    bg_cpu: int = 4,
):
    """Static camera: one high-quality background image + a video of the residual
    (frame − background + 128, ffmpeg blend grainextract), SVT-AV1 2-pass.

    bg_src: "median" (per-pixel temporal median, bg_median.py) or "first" (frame 0: tells
    whether the gain comes from the median or just from a better key image).
    bg_codec: "av1" (libaom all-intra, bg_q = crf) or "jxl" (cjxl, bg_q = distance; RGB round trip).
    k: keep every k-th frame for the residual video (the display holds frames, like score.py).
    step: background median over every step-th frame (6 on the Pi: half the memory).
    Knob = TOTAL kbps; the residual gets what the background leaves."""

    def b(ctx: Ctx, kbps: float) -> str:
        t = ctx.tmp
        bg, bgd, res, reso = t / "bg.y4m", t / "bgd.y4m", t / "res.y4m", t / "res.ivf"
        if bg_src == "median":
            make_bg = (
                f"{q(py or sys.executable)} {q(HERE / 'bg_median.py')} {q(ctx.src)} {q(bg)} "
                f"--step {step}"
            )
        else:
            make_bg = (
                f"ffmpeg -hide_banner -v error -y -i {q(ctx.src)} -frames:v 1 -pix_fmt yuv420p "
                f"-f yuv4mpegpipe {q(bg)}"
            )
        if bg_codec == "av1":
            bgo = t / "bg.obu"
            code_bg = (
                f"ffmpeg -hide_banner -v error -y -i {q(bg)} -c:v libaom-av1 -usage allintra "
                f"-cpu-used {bg_cpu} "
                f"-crf {int(bg_q)} -b:v 0 -f obu {q(bgo)} && "
                f"ffmpeg -hide_banner -v error -y -f obu -i {q(bgo)} -pix_fmt yuv420p "
                f"-f yuv4mpegpipe {q(bgd)}"
            )
        else:
            bgo = t / "bg.jxl"
            code_bg = (
                f"ffmpeg -hide_banner -v error -y -i {q(bg)} -pix_fmt rgb24 {q(t / 'bg.png')} && "
                f"cjxl {q(t / 'bg.png')} {q(bgo)} -d {bg_q} -e 7 --quiet && "
                f"djxl {q(bgo)} {q(t / 'bgd.png')} --quiet && "
                f"ffmpeg -hide_banner -v error -y -i {q(t / 'bgd.png')} -pix_fmt yuv420p "
                f"-f yuv4mpegpipe {q(bgd)}"
            )
        return (
            f"{make_bg} && {code_bg} && "
            f"ffmpeg -hide_banner -v error -y -i {q(ctx.src)} -i {q(bgd)} -filter_complex "
            f"\"[0:v]select='not(mod(n\\,{k}))',setpts=N/({FPS}/{k}*TB)[s];"
            f"[1:v]loop=loop=-1:size=1,setpts=N/({FPS}/{k}*TB)[b];"
            f'[s][b]blend=all_mode=grainextract:shortest=1" '
            f"-fps_mode cfr -r {FPS}/{k} -pix_fmt yuv420p -f yuv4mpegpipe {q(res)} && "
            f"BGB=$(stat -f %z {q(bgo)} 2>/dev/null || stat -c %s {q(bgo)}) && "
            f'RK=$(python3 -c "print(max(1, round({kbps} - $BGB * 8 / {DUR_S} / 1000)))") && '
            f"SvtAv1EncApp --preset {preset} --rc 1 --tbr $RK --passes 2 --keyint {GOP} --scd 0 "
            f"--lp {ctx.threads} --progress 0 {extra} --stats {q(t / 'svt.stats')} "
            f"-i {q(res)} -b {q(reso)} && "
            f"ffmpeg -hide_banner -v error -y -i {q(reso)} -c copy -f obu {q(t / 'res.obu')} && "
            f"{PACK} pack {q(ctx.out)} {q(bgo)} {q(t / 'res.obu')}"
        )

    return b


# Background already at the server ("send it once a day"): the background comes from ANOTHER
# clip of the same camera (minutes apart in the pool), is coded once at high quality and is not
# counted in this clip's budget; the clip sends only the residual video.
BG_REF = {"round1": "round2", "round2": "round3", "round3": "round1"}


def bg_ref_clip(clip: str) -> str:
    """The other round of the same camera, same window (e.g. round1 -> round2)."""
    base, start = split_clip(clip)
    cam, rnd = base.split("_")
    ref = f"{cam}_{BG_REF[rnd]}"
    return ref if start == WIN_MAIN else f"{ref}@{start}"


def cached_bg(data: Path, clip: str, crf: int, py: str | None = None) -> tuple[str, Path, Path]:
    """Shell command (idempotent) that makes the cached background of clip's reference clip
    (median of all 50 window frames), plus its coded (.obu) and decoded (.y4m) paths."""
    ref = bg_ref_clip(clip)
    d = data / "cache" / "bg"
    obu, y4m = d / f"{ref}_crf{crf}.obu", d / f"{ref}_crf{crf}.y4m"
    raw = d / f"{ref}_median.y4m"
    cmd = (
        f"mkdir -p {q(d)} && ( test -s {q(y4m)} || ( "
        f"{q(py or sys.executable)} {q(HERE / 'bg_median.py')} {q(src_path(ref))} {q(raw)}.$$ --step 1 && "
        f"ffmpeg -hide_banner -v error -y -i {q(raw)}.$$ -c:v libaom-av1 -usage allintra "
        f"-cpu-used 4 -crf {crf} -b:v 0 -f obu {q(obu)}.$$ && "
        f"ffmpeg -hide_banner -v error -y -f obu -i {q(obu)}.$$ -pix_fmt yuv420p "
        f"-f yuv4mpegpipe {q(y4m)}.$$ && mv {q(obu)}.$$ {q(obu)} && mv {q(y4m)}.$$ {q(y4m)} && "
        f"rm -f {q(raw)}.$$ ) )"
    )
    return cmd, obu, y4m


def bg_cached_residual(
    bg_crf: int = 20,
    preset: int = 4,
    py: str | None = None,
    extra: str = "",
    k: int = 1,
    nfr: int = NFR,
):
    """Residual video only; the background is the cached one (see BG_REF). Knob = kbps.
    k: keep every k-th frame; nfr: first nfr frames only (shorter clip)."""

    def b(ctx: Ctx, kbps: float) -> str:
        t = ctx.tmp
        data = ctx.out.parents[3]  # <data>/enc/<cand>/attempts/<file>
        mk, _, bgd = cached_bg(data, ctx.clip, bg_crf, py)
        res, reso = t / "res.y4m", t / "res.ivf"
        return (
            f"{mk} && ffmpeg -hide_banner -v error -y -i {q(ctx.src)} -i {q(bgd)} -filter_complex "
            f"\"[0:v]trim=end_frame={nfr},select='not(mod(n\\,{k}))',setpts=N/({FPS}/{k}*TB)[s];"
            f"[1:v]loop=loop=-1:size=1,setpts=N/({FPS}/{k}*TB)[b];"
            f'[s][b]blend=all_mode=grainextract:shortest=1" '
            f"-fps_mode cfr -r {FPS}/{k} -pix_fmt yuv420p -f yuv4mpegpipe {q(res)} && "
            f"SvtAv1EncApp --preset {preset} --rc 1 --tbr {max(1, round(kbps))} --passes 2 "
            f"--keyint {GOP} --scd 0 --lp {ctx.threads} --progress 0 {extra} "
            f"--stats {q(t / 'svt.stats')} -i {q(res)} -b {q(reso)} && "
            f"ffmpeg -hide_banner -v error -y -i {q(reso)} -c copy -f obu {q(ctx.out)}"
        )

    return b


def bg_cached_display(bg_crf: int = 20, k: int = 1, nfr: int = NFR):
    from .score import display_chain

    def d(f: Path, clip: str, tmp: Path) -> tuple[Path, dict]:
        data = f.parents[2]  # <data>/enc/<cand>/<clip>.obu
        _, obu, bgd = cached_bg(data, clip, bg_crf)
        tmp.mkdir(parents=True, exist_ok=True)
        out = tmp / "display.mkv"
        sh(
            f"ffmpeg -hide_banner -v error -y -i {q(f)} -i {q(bgd)} -filter_complex "
            f'"[0:v]format=yuv420p,settb=1/{FPS},setpts=N[r];[1:v]format=yuv420p,'
            f"loop=loop=-1:size=1,settb=1/{FPS},setpts=N[b];"
            f"[b][r]blend=all_mode=grainmerge:shortest=1,"
            f'{display_chain(k, (W, H), "hold", nfr)}" -frames:v {nfr} -c:v ffv1 {q(out)}'
        )
        return out, {"bg_bytes_cached": obu.stat().st_size, "bg_ref_clip": bg_ref_clip(clip)}

    return d


def bg_residual_display(f: Path, clip: str, tmp: Path, k: int = 1) -> tuple[Path, dict]:
    from .pack import unpack
    from .score import display_chain

    tmp.mkdir(parents=True, exist_ok=True)
    bg, res = unpack(f)
    (tmp / "res.obu").write_bytes(res)
    if bg[:2] == b"\xff\x0a" or bg[4:8] == b"JXL ":  # JPEG XL background: decode, back to YUV
        (tmp / "bg.jxl").write_bytes(bg)
        sh(
            f"djxl {q(tmp / 'bg.jxl')} {q(tmp / 'bg.png')} --quiet && "
            f"ffmpeg -hide_banner -v error -y "
            f"-i {q(tmp / 'bg.png')} -pix_fmt yuv420p -f yuv4mpegpipe {q(tmp / 'bg.y4m')}"
        )
        bg_in = f"-i {q(tmp / 'bg.y4m')}"
    else:
        (tmp / "bg.obu").write_bytes(bg)
        bg_in = f"-f obu -i {q(tmp / 'bg.obu')}"
    out = tmp / "display.mkv"
    sh(
        f"ffmpeg -hide_banner -v error -y -i {q(tmp / 'res.obu')} {bg_in} -filter_complex "
        f'"[0:v]format=yuv420p,settb=1/{FPS},setpts=N[r];[1:v]format=yuv420p,loop=loop=-1:size=1,'
        f"settb=1/{FPS},setpts=N[b];[b][r]blend=all_mode=grainmerge:shortest=1,"
        f'{display_chain(k, (W, H), "hold")}" '
        f"-frames:v {NFR} -c:v ffv1 {q(out)}"
    )
    return out, {"bg_bytes": len(bg), "residual_bytes": len(res)}
