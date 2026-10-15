"""Candidate pipelines for the ≤ 100 KB study. One entry = one encode recipe.

Each candidate builds a shell command that turns a source clip into ONE bitstream file (the
bytes that would be sent). The rate knob is searched per clip (``bench.py``) so every final
file lands in 90–100 KB where the knob allows:

- ``kbps``: target bitrate (two-pass VBR where the encoder has it), proportional correction;
- ``q``: a quality knob (CRF / QP / JPEG XL distance), bisection; size falls as q rises.

Frame-rate / resolution changes happen BEFORE the encoder (ffmpeg ``select`` keeps every k-th
source frame; ``scale`` lanczos) and are undone for scoring (hold / bicubic) — see ``score.py``.

Encoder versions: Mac = Homebrew (x264 0.165, x265 4.1, SVT-AV1 3.1.2, libaom 3.13.1, rav1e
0.8.1, vvenc 1.14.0, libjxl 0.11.1). ``docker=True`` candidates run in the Pi-matched container
(Debian trixie arm64: SVT-AV1 2.3.0, libaom 3.12.1, x264 0.164, libjxl 0.11.2, ffmpeg 7.1.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .common import CAP_B, DUR_S, FPS, NFR, H, W, q

GOP = 300  # > 293 frames: exactly one key frame per clip (static camera)


@dataclass
class Ctx:
    clip: str
    src: Path  # source mp4
    out: Path  # bitstream to write
    tmp: Path  # scratch dir for this attempt
    vf: str  # source filter chain (pre-filter, frame select, scale)
    fr: str  # output frame rate (e.g. "10", "5", "1")
    y4m: Path | None = None  # cached y4m of the filtered source (two-pass apps)
    threads: int = 4


@dataclass
class Cand:
    name: str
    family: str  # h264 | hevc | av1 | vvc | jxl | still
    label: str  # human description for tables
    build: Callable[[Ctx, float], str]
    knob: str = "kbps"
    q_range: tuple = ()  # (lo, hi, integer)
    k: int = 1  # keep every k-th frame -> fps = 10/k
    wh: tuple = (W, H)
    pre: str = ""  # pre-filter (denoise etc.), applied before select/scale
    ext: str = "obu"
    dec: str = ""  # decoder options for scoring
    display: str = "hold"
    needs_y4m: bool = False
    docker: bool = False
    tags: list = field(default_factory=list)
    notes: str = ""
    # optional: custom scorer input (e.g. stills summary): fn(ctx_dir) -> lossless display file
    display_file: Callable | None = None
    cap: int = CAP_B  # bytes; a bundle (still + video) splits the 100 KB
    threads: int | None = None  # fixed thread count (Pi memory configs); None = the runner's
    nfr: int = NFR  # < 293: a shorter clip (first nfr frames) -- exploratory duration knob


def src_ff(ctx: Ctx) -> str:
    """ffmpeg input + filter for the source (decode, pre-filter, select, scale)."""
    vf = f'-vf "{ctx.vf}"' if ctx.vf else ""
    return f"ffmpeg -hide_banner -v error -y -i {q(ctx.src)} {vf} -fps_mode cfr -r {ctx.fr}"


def source_vf(c: Cand) -> tuple[str, str]:
    parts = [f"trim=end_frame={c.nfr}"] if c.nfr < NFR else []
    if c.pre:
        parts.append(c.pre)
    if c.k > 1:
        parts.append(f"select='not(mod(n\\,{c.k}))',setpts=N/({FPS}/{c.k}*TB)")
    if tuple(c.wh) != (W, H):
        parts.append(f"scale={c.wh[0]}:{c.wh[1]}:flags=lanczos")
    fr = f"{FPS}/{c.k}" if c.k > 1 else str(FPS)
    return ",".join(parts), fr


def n_frames(c: Cand) -> int:
    return -(-c.nfr // c.k)


# ---------------------------------------------------------------- encoders (Mac / Pi container)


def x264(preset="veryslow", extra=""):
    def b(ctx: Ctx, kbps: float) -> str:
        p = (
            f"keyint={GOP}:min-keyint={GOP}:scenecut=0:rc-lookahead=250:bframes=16:"
            f"threads={ctx.threads}{':' + extra if extra else ''}"
        )
        log = ctx.tmp / "x264"
        common = (
            f"-c:v libx264 -preset {preset} -b:v {kbps:.3f}k -x264-params {p} -passlogfile {q(log)}"
        )
        return (
            f"{src_ff(ctx)} {common} -pass 1 -f null - && "
            f"{src_ff(ctx)} {common} -pass 2 -bsf:v filter_units=remove_types=6 "
            f"-f h264 {q(ctx.out)}"
        )

    return b


def x265(preset="veryslow", extra=""):
    def b(ctx: Ctx, kbps: float) -> str:
        st = ctx.tmp / "x265.stats"
        base = (
            f"keyint={GOP}:min-keyint={GOP}:scenecut=0:rc-lookahead=250:bframes=16:info=0:"
            f"pools={ctx.threads}:stats={st}{':' + extra if extra else ''}"
        )
        common = f"-c:v libx265 -preset {preset} -b:v {kbps:.3f}k"
        return (
            f"{src_ff(ctx)} {common} -x265-params {base}:pass=1 -f null - && "
            f"{src_ff(ctx)} {common} -x265-params {base}:pass=2 -f hevc {q(ctx.out)}"
        )

    return b


def svt_app(preset: int, extra: str = "", passes: int = 2):
    """SvtAv1EncApp, VBR, 2-pass from a y4m file (the app cannot two-pass a pipe)."""

    def b(ctx: Ctx, kbps: float) -> str:
        ivf = ctx.tmp / "o.ivf"
        st = ctx.tmp / "svt.stats"
        cmd = (
            f"SvtAv1EncApp --preset {preset} --rc 1 --tbr {max(1, round(kbps))} --passes {passes} "
            f"--keyint {GOP} --scd 0 --lp {ctx.threads} --progress 0 {extra} "
            f"{'--stats ' + q(st) if passes == 2 else ''} -i {q(ctx.y4m)} -b {q(ivf)}"
        )
        return f"{cmd} && ffmpeg -hide_banner -v error -y -i {q(ivf)} -c copy -f obu {q(ctx.out)}"

    return b


def svt_crf(preset: int, extra: str = ""):
    """SvtAv1EncApp CRF (q knob): infinite GOP, one pass (what a Pi could run with a retry)."""

    def b(ctx: Ctx, crf: float) -> str:
        ivf = ctx.tmp / "o.ivf"
        cmd = (
            f"SvtAv1EncApp --preset {preset} --rc 0 --crf {int(crf)} --keyint -1 --scd 0 "
            f"--lp {ctx.threads} --progress 0 {extra} -i {q(ctx.y4m)} -b {q(ivf)}"
        )
        return f"{cmd} && ffmpeg -hide_banner -v error -y -i {q(ivf)} -c copy -f obu {q(ctx.out)}"

    return b


def aom(cpu: int, extra: str = "", usage: str = "good"):
    def b(ctx: Ctx, kbps: float) -> str:
        log = ctx.tmp / "aom"
        p = f"enable-tpl-model=1:auto-alt-ref=1{':' + extra if extra else ''}"
        common = (
            f"-c:v libaom-av1 -usage {usage} -cpu-used {cpu} -b:v {kbps:.3f}k "
            f"-threads {ctx.threads} -row-mt 1 -g {GOP} -keyint_min {GOP} -lag-in-frames 48 "
            f"-aom-params {p} -passlogfile {q(log)}"
        )
        return (
            f"{src_ff(ctx)} {common} -pass 1 -f null - && "
            f"{src_ff(ctx)} {common} -pass 2 -f obu {q(ctx.out)}"
        )

    return b


def aom_cq(cpu: int, extra: str = ""):
    """libaom constant quality (q knob = cq-level / crf), 2-pass for alt-refs."""

    def b(ctx: Ctx, crf: float) -> str:
        log = ctx.tmp / "aom"
        p = f"enable-tpl-model=1:auto-alt-ref=1{':' + extra if extra else ''}"
        common = (
            f"-c:v libaom-av1 -cpu-used {cpu} -crf {int(crf)} -b:v 0 -threads {ctx.threads} "
            f"-row-mt 1 -g {GOP} -keyint_min {GOP} -lag-in-frames 48 -aom-params {p} "
            f"-passlogfile {q(log)}"
        )
        return (
            f"{src_ff(ctx)} {common} -pass 1 -f null - && "
            f"{src_ff(ctx)} {common} -pass 2 -f obu {q(ctx.out)}"
        )

    return b


def rav1e(speed: int):
    def b(ctx: Ctx, kbps: float) -> str:
        log = ctx.tmp / "rav1e"
        common = (
            f"-c:v librav1e -speed {speed} -b:v {kbps:.3f}k -rav1e-params keyint={GOP}:"
            f"min_keyint={GOP}:threads={ctx.threads} -passlogfile {q(log)}"
        )
        return (
            f"{src_ff(ctx)} {common} -pass 1 -f null - && "
            f"{src_ff(ctx)} {common} -pass 2 -f obu {q(ctx.out)}"
        )

    return b


def vvenc(preset: str):
    def b(ctx: Ctx, kbps: float) -> str:
        return (
            f"vvencapp --preset {preset} -i {q(ctx.y4m)} --y4m -b {round(kbps * 1000)} --passes 2 "
            f"--intraperiod 320 -t {ctx.threads} --stats 0 -o {q(ctx.out)}"
        )

    return b


def jxl_anim(effort: int = 7):
    """JPEG XL animation (cjxl from APNG): every frame intra, no motion prediction."""

    def b(ctx: Ctx, dist: float) -> str:
        apng = ctx.tmp / "s.apng"
        return (
            f"{src_ff(ctx)} -f apng -pix_fmt rgb24 -plays 0 {q(apng)} && "
            f"cjxl {q(apng)} {q(ctx.out)} -d {dist:.4f} -e {effort} --quiet"
        )

    return b


def av1_intra(cpu: int = 4):
    """AVIF-style all-intra AV1 sequence (every frame a key frame, q knob = crf)."""

    def b(ctx: Ctx, crf: float) -> str:
        return (
            f"{src_ff(ctx)} -c:v libaom-av1 -cpu-used {cpu} -crf {int(crf)} -b:v 0 -g 1 "
            f"-threads {ctx.threads} -row-mt 1 -f obu {q(ctx.out)}"
        )

    return b


# ---------------------------------------------------------------- registry

KBPS0 = 100_000 * 8 / DUR_S / 1000  # 27.3 kbps for the whole clip


def registry() -> dict[str, Cand]:
    C = {}

    def add(c: Cand):
        C[c.name] = c

    # 1. codec baselines: 1280x720, 10 fps, one key frame, best-effort settings on the Mac
    add(Cand("x264_vs", "h264", "H.264 x264 veryslow 2-pass", x264(), ext="h264", tags=["codec"]))
    add(Cand("x265_vs", "hevc", "H.265 x265 veryslow 2-pass", x265(), ext="hevc", tags=["codec"]))
    add(
        Cand(
            "svt3_p2",
            "av1",
            "AV1 SVT-AV1 3.1 p2 2-pass",
            svt_app(2),
            needs_y4m=True,
            tags=["codec"],
        )
    )
    add(
        Cand(
            "svt3_p4",
            "av1",
            "AV1 SVT-AV1 3.1 p4 2-pass",
            svt_app(4),
            needs_y4m=True,
            tags=["codec"],
        )
    )
    add(
        Cand(
            "svt3_p8",
            "av1",
            "AV1 SVT-AV1 3.1 p8 2-pass",
            svt_app(8),
            needs_y4m=True,
            tags=["codec"],
        )
    )
    add(Cand("aom_c3", "av1", "AV1 libaom cpu-used 3 2-pass", aom(3), tags=["codec"]))
    add(Cand("aom_c6", "av1", "AV1 libaom cpu-used 6 2-pass", aom(6), tags=["codec"]))
    add(Cand("rav1e_s4", "av1", "AV1 rav1e speed 4 2-pass", rav1e(4), tags=["codec"]))
    add(
        Cand(
            "vvc_slower",
            "vvc",
            "H.266 vvenc slower 2-pass",
            vvenc("slower"),
            ext="266",
            needs_y4m=True,
            tags=["codec"],
        )
    )
    add(
        Cand(
            "vvc_medium",
            "vvc",
            "H.266 vvenc medium 2-pass",
            vvenc("medium"),
            ext="266",
            needs_y4m=True,
            tags=["codec"],
        )
    )
    # 4. image sequences (intra only) at 10 fps: expected to miss the budget; measured anyway
    add(
        Cand(
            "jxl_anim_10",
            "jxl",
            "JPEG XL animation 10 fps",
            jxl_anim(),
            knob="q",
            q_range=(1.0, 25.0, False),
            ext="jxl",
            tags=["sequence"],
        )
    )
    add(
        Cand(
            "av1_intra_10",
            "av1",
            "AVIF-style all-intra AV1 10 fps",
            av1_intra(),
            knob="q",
            q_range=(10, 63, True),
            tags=["sequence"],
        )
    )
    smart_registry(add)
    pi_registry(add)
    return C


def pi_registry(add) -> None:
    """Recipes for the Pi container (pi_emulate.py measure): Pi package versions, 4 cores.
    Same builders as on the Mac; only the encoder versions and thread counts differ."""
    from . import smart

    for p in (6, 8, 10):
        add(
            Cand(
                f"pi_svt_p{p}",
                "av1",
                f"AV1 SVT-AV1 2.3 p{p} 2-pass (Pi versions)",
                svt_app(p),
                needs_y4m=True,
                tags=["pi"],
            )
        )
    add(
        Cand(
            "pi_svt_p8_lowmem",
            "av1",
            "AV1 SVT-AV1 2.3 p8, 1 thread, no look-ahead / temporal filter",
            svt_app(8, "--lookahead 0 --enable-tf 0"),
            needs_y4m=True,
            threads=1,
            tags=["pi"],
        )
    )
    for p in (6, 8):
        add(
            Cand(
                f"pi_bgres_p{p}",
                "av1",
                f"background + residual, SVT-AV1 2.3 p{p} (Pi versions)",
                smart.bg_residual(30, preset=p, step=6, py="python3", bg_cpu=6),
                ext="pack",
                display_file=smart.bg_residual_display,
                tags=["pi"],
            )
        )
    for p in (8, 10):
        add(
            Cand(
                f"pi_bgcache_p{p}",
                "av1",
                f"residual vs cached background, SVT-AV1 2.3 p{p} (Pi versions)",
                smart.bg_cached_residual(20, preset=p, py="python3"),
                display_file=smart.bg_cached_display(20),
                tags=["pi"],
            )
        )
    add(
        Cand(
            "pi_bgcache_p8_lowmem",
            "av1",
            "residual vs cached background, SVT-AV1 2.3 p8, 1 thread, no look-ahead / TF",
            smart.bg_cached_residual(
                20, preset=8, py="python3", extra="--lookahead 0 --enable-tf 0"
            ),
            display_file=smart.bg_cached_display(20),
            threads=1,
            tags=["pi"],
        )
    )
    add(
        Cand(
            "pi_aom_c6",
            "av1",
            "AV1 libaom 3.12 cpu-used 6 2-pass (Pi versions)",
            aom(6),
            tags=["pi"],
        )
    )
    add(
        Cand(
            "pi_jxl_stills_2",
            "still",
            "2 JPEG XL stills (Pi versions)",
            smart.jxl_stills(2),
            knob="q",
            q_range=(0.3, 25.0, False),
            ext="pack",
            display_file=smart.stills_display(2),
            tags=["pi"],
        )
    )


def smart_registry(add) -> None:
    """Stage 2: one variable at a time on SVT-AV1 3.1 preset 4 (2-pass VBR, 720p, 10 fps)."""
    from . import smart

    base = "AV1 SVT p4"
    # frame rate (server shows each frame until the next; '_mi' = server motion interpolation)
    for k, fps in ((2, 5), (5, 2), (10, 1)):
        add(
            Cand(
                f"s4_fps{fps}",
                "av1",
                f"{base}, {fps} fps",
                svt_app(4),
                k=k,
                needs_y4m=True,
                tags=["fps"],
            )
        )
        add(
            Cand(
                f"s4_fps{fps}_mi",
                "av1",
                f"{base}, {fps} fps + server interpolation",
                svt_app(4),
                k=k,
                needs_y4m=True,
                display="minterp",
                tags=["fps"],
            )
        )
    # resolution (upscaled bicubic for scoring)
    for wh in ((960, 540), (640, 360)):
        add(
            Cand(
                f"s4_{wh[1]}p",
                "av1",
                f"{base}, {wh[0]}x{wh[1]}",
                svt_app(4),
                wh=wh,
                needs_y4m=True,
                tags=["res"],
            )
        )
    add(
        Cand(
            "s4_540p_fps5",
            "av1",
            f"{base}, 960x540, 5 fps",
            svt_app(4),
            wh=(960, 540),
            k=2,
            needs_y4m=True,
            tags=["res", "fps"],
        )
    )
    # superres: SVT-AV1 refuses it with 2-pass, so it is paired with a 1-pass baseline
    add(
        Cand(
            "s4_1pass",
            "av1",
            f"{base}, 1-pass VBR (baseline for superres)",
            svt_app(4, passes=1),
            needs_y4m=True,
            tags=["res"],
        )
    )
    add(
        Cand(
            "s4_superres",
            "av1",
            f"{base}, 1-pass, superres 1/2 width (decoder upscales)",
            svt_app(4, "--superres-mode 1 --superres-denom 16 --superres-kf-denom 16", passes=1),
            needs_y4m=True,
            tags=["res"],
        )
    )
    add(
        Cand(
            "s4_superres_auto",
            "av1",
            f"{base}, 1-pass, superres auto",
            svt_app(4, "--superres-mode 4", passes=1),
            needs_y4m=True,
            tags=["res"],
        )
    )
    # denoise before the encoder
    for nm, f in (
        ("hqdn3d", "hqdn3d=4:3:8:6"),
        ("hqdn3d_strong", "hqdn3d=8:6:16:12"),
        ("tmedian", "tmedian=radius=2"),
        ("nlmeans", "nlmeans=s=3:p=7:r=15"),
    ):
        add(
            Cand(
                f"s4_dn_{nm}",
                "av1",
                f"{base}, denoise {f}",
                svt_app(4),
                pre=f,
                needs_y4m=True,
                tags=["denoise"],
            )
        )
    # film-grain synthesis: SVT denoises internally, sends grain parameters, decoder re-adds grain
    for lvl in (8, 20):
        fg = f"--film-grain {lvl} --film-grain-denoise 1"
        add(
            Cand(
                f"s4_fg{lvl}",
                "av1",
                f"{base}, film grain {lvl} (grain shown)",
                svt_app(4, fg),
                needs_y4m=True,
                tags=["grain"],
            )
        )
        add(
            Cand(
                f"s4_fg{lvl}_nograin",
                "av1",
                f"{base}, film grain {lvl} (grain not applied)",
                svt_app(4, fg),
                needs_y4m=True,
                dec="-c:v libdav1d -export_side_data film_grain",
                tags=["grain"],
            )
        )
    # encoder tuning
    # clip length (exploratory, not in PREREG): the same 100 KB for the first 10 s / 15 s
    for sec in (10, 15):
        add(
            Cand(
                f"s4_{sec}s",
                "av1",
                f"{base}, first {sec} s only",
                svt_app(4),
                nfr=sec * FPS,
                needs_y4m=True,
                tags=["duration"],
            )
        )
    add(
        Cand(
            "s4_tune_vq",
            "av1",
            f"{base}, tune VQ",
            svt_app(4, "--tune 0"),
            needs_y4m=True,
            tags=["tune"],
        )
    )
    add(
        Cand(
            "s4_vboost",
            "av1",
            f"{base}, variance boost",
            svt_app(4, "--enable-variance-boost 1"),
            needs_y4m=True,
            tags=["tune"],
        )
    )
    add(
        Cand(
            "s4_overlays",
            "av1",
            f"{base}, overlay frames (static bg)",
            svt_app(4, "--enable-overlays 1"),
            needs_y4m=True,
            tags=["tune"],
        )
    )
    # ROI: more bits on the card, fewer elsewhere (qindex offsets per 64x64 superblock)
    add(
        Cand(
            "s4_roi_card",
            "av1",
            f"{base}, ROI card -60 qindex",
            smart.svt_roi(4, -60, 0),
            needs_y4m=True,
            tags=["roi"],
        )
    )
    add(
        Cand(
            "s4_roi_card_rest",
            "av1",
            f"{base}, ROI card -60 / rest +40",
            smart.svt_roi(4, -60, 40),
            needs_y4m=True,
            tags=["roi"],
        )
    )
    # background + residual (static camera)
    for crf in (20, 30, 38, 45):
        add(
            Cand(
                f"bgres_crf{crf}",
                "av1",
                f"background (median, AV1 intra crf {crf}) + residual video SVT p4",
                smart.bg_residual(crf),
                ext="pack",
                display_file=smart.bg_residual_display,
                tags=["bgdelta"],
            )
        )
    add(
        Cand(
            "bgres_first_crf30",
            "av1",
            "background = frame 0 (AV1 intra crf 30) + residual video SVT p4",
            smart.bg_residual(30, bg_src="first"),
            ext="pack",
            display_file=smart.bg_residual_display,
            tags=["bgdelta"],
        )
    )
    add(
        Cand(
            "bgres_crf30_fps5",
            "av1",
            "background (median, AV1 intra crf 30) + residual video SVT p4, 5 fps",
            smart.bg_residual(30, k=2),
            ext="pack",
            display_file=lambda f, c, t: smart.bg_residual_display(f, c, t, k=2),
            tags=["bgdelta", "fps"],
        )
    )
    add(
        Cand(
            "bgres_jxl_d1",
            "av1",
            "background (median, JPEG XL d1.0) + residual video SVT p4",
            smart.bg_residual(1.0, bg_codec="jxl"),
            ext="pack",
            display_file=smart.bg_residual_display,
            tags=["bgdelta"],
        )
    )
    # background sent once (e.g. daily) and cached at the server: the clip sends only the residual
    add(
        Cand(
            "bgcache_crf20",
            "av1",
            "residual video only; background sent earlier, cached at the server",
            smart.bg_cached_residual(20),
            display_file=smart.bg_cached_display(20),
            tags=["bgdelta"],
            notes="background = median of another clip of the same camera (AV1 intra crf 20)",
        )
    )
    for k, fps in ((2, 5),):
        add(
            Cand(
                f"bgcache_crf20_fps{fps}",
                "av1",
                f"residual video only, {fps} fps; background cached at the server",
                smart.bg_cached_residual(20, k=k),
                display_file=smart.bg_cached_display(20, k=k),
                k=k,
                tags=["bgdelta", "fps"],
            )
        )
    for sec in (10, 15):
        add(
            Cand(
                f"bgcache_crf20_{sec}s",
                "av1",
                f"residual video only, first {sec} s; background cached at the server",
                smart.bg_cached_residual(20, nfr=sec * FPS),
                display_file=smart.bg_cached_display(20, nfr=sec * FPS),
                nfr=sec * FPS,
                tags=["bgdelta", "duration"],
            )
        )
    # video summary: N stills as JPEG XL, held on screen
    for n in (1, 2, 4, 8):
        add(
            Cand(
                f"jxl_stills_{n}",
                "still",
                f"{n} JPEG XL still(s), held",
                smart.jxl_stills(n),
                knob="q",
                q_range=(0.3, 25.0, False),
                ext="pack",
                display_file=smart.stills_display(n),
                tags=["summary"],
            )
        )
    # low-fps image sequences
    for k, fps in ((5, 2), (10, 1)):
        add(
            Cand(
                f"jxl_anim_{fps}fps",
                "jxl",
                f"JPEG XL animation {fps} fps",
                jxl_anim(),
                knob="q",
                q_range=(0.5, 25.0, False),
                k=k,
                ext="jxl",
                tags=["sequence"],
            )
        )
        add(
            Cand(
                f"av1_intra_{fps}fps",
                "av1",
                f"AVIF-style all-intra AV1 {fps} fps",
                av1_intra(),
                knob="q",
                q_range=(10, 63, True),
                k=k,
                tags=["sequence"],
            )
        )
