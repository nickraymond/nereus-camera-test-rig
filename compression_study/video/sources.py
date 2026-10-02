"""Build the canonical video sources for the codec bench (Mac).

    python -m compression_study.video.sources --data <primary>/data/video_20261002

Every encoder (Pi and Mac) reads the same lossless FFV1 file, and every score compares
against it, so codecs are compared on identical pixels. Sources, all yuv420p limited range:

- ``imx_static_1080``  the IMX708 1080p30 MJPEG q95 master (rpicam-vid), range-converted only
- ``imx_static_720``   the same, Lanczos-downscaled to 1280x720 (a device would record 720p
                       natively or scale before encoding; the scaler is not the variable here)
- ``imx_bob_1080/720`` simulated buoy motion: per-frame roll + sway + heave + 1.18x zoom of
                       the static master (cubic warp on the YUV planes). The real scene is
                       static, so this is the motion stress case; real water adds particles,
                       caustics and fish that a pure camera motion does not.
- ``n6_static``        the N6 stream master (HD q90, 18.4 fps over USB), 1280x800
- ``n6_bob``           the same motion applied to the N6 master
- low-budget variants (``--variants``): the motion clips at 10 fps (IMX 720p and 360p, N6
  1280x800 and 640x400 at half its 18.4 fps); ``*_1080_10`` are reference-only (scoring).

Each source records ``display_ref``: the source a viewer's full-screen picture is scored
against (same frame times, 1080p for the IMX, 1280x800 for the N6); ``encode: false`` marks
reference-only sources.

The masters stay untouched in ``<data>/<camera>/`` (raw evidence); outputs go to
``<data>/sources/`` with ``sources.json`` (frames, fps, dims, per-source decode md5).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path

import cv2
import numpy as np

FF = "ffmpeg"
RANGE = "scale=out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p"
FFV1 = ["-c:v", "ffv1", "-level", "3", "-g", "1", "-slices", "4", "-slicecrc", "1"]

# Buoy-like motion (deliberately not commensurate periods so the path never repeats)
ROLL_DEG, ROLL_T = 2.5, 4.1
SWAY, SWAY_T = 0.030, 5.3  # fraction of width
HEAVE, HEAVE_T = 0.040, 3.7  # fraction of height
ZOOM = 1.18


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def ffv1_from(src_args: list[str], vf: str, fps: str, out: Path) -> None:
    run([FF, "-v", "error", "-y", *src_args, "-vf", vf, "-r", fps, *FFV1, str(out)])


def read_frames(path: Path, w: int, h: int):
    p = subprocess.Popen(
        [FF, "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        stdout=subprocess.PIPE,
    )
    n = w * h * 3 // 2
    while True:
        buf = p.stdout.read(n)  # type: ignore[union-attr]
        if len(buf) < n:
            break
        yield np.frombuffer(buf, np.uint8)
    p.wait()


def bob(src: Path, out: Path, w: int, h: int, fps: str) -> None:
    """Warp every frame of ``src`` with the buoy motion; borders never enter the frame."""
    rate = eval(fps)  # "30" or "276/14.94" style rational
    enc = subprocess.Popen(
        [
            FF,
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-s",
            f"{w}x{h}",
            "-r",
            fps,
            "-i",
            "-",
            *FFV1,
            str(out),
        ],
        stdin=subprocess.PIPE,
    )
    for i, f in enumerate(read_frames(src, w, h)):
        t = i / rate
        ang = ROLL_DEG * math.sin(2 * math.pi * t / ROLL_T)
        dx = SWAY * w * math.sin(2 * math.pi * t / SWAY_T + 1.0)
        dy = HEAVE * h * math.sin(2 * math.pi * t / HEAVE_T + 2.0)
        m = cv2.getRotationMatrix2D((w / 2, h / 2), ang, ZOOM)
        m[:, 2] += (dx, dy)
        _check_inside(m, w, h)
        y = f[: w * h].reshape(h, w)
        u = f[w * h : w * h * 5 // 4].reshape(h // 2, w // 2)
        v = f[w * h * 5 // 4 :].reshape(h // 2, w // 2)
        # Same motion on the half-resolution chroma grid (centre-sited: x_luma = 2 x_c + 0.5)
        mc = m.copy()
        mc[:, 2] = (m[:, :2] @ np.array([0.5, 0.5]) + m[:, 2] - 0.5) / 2
        flags = cv2.INTER_CUBIC | cv2.WARP_FILL_OUTLIERS
        yo = cv2.warpAffine(y, m, (w, h), flags=flags, borderMode=cv2.BORDER_REFLECT)
        uo = cv2.warpAffine(u, mc, (w // 2, h // 2), flags=flags, borderMode=cv2.BORDER_REFLECT)
        vo = cv2.warpAffine(v, mc, (w // 2, h // 2), flags=flags, borderMode=cv2.BORDER_REFLECT)
        enc.stdin.write(yo.tobytes() + uo.tobytes() + vo.tobytes())  # type: ignore[union-attr]
    enc.stdin.close()  # type: ignore[union-attr]
    if enc.wait():
        raise RuntimeError(f"ffv1 encode failed for {out}")


def _check_inside(m: np.ndarray, w: int, h: int) -> None:
    """Every output corner must map back inside the source frame (no reflected border)."""
    inv = cv2.invertAffineTransform(m)
    for x, y in ((0, 0), (w, 0), (0, h), (w, h)):
        sx, sy = inv @ np.array([x, y, 1.0])
        if not (0 <= sx <= w and 0 <= sy <= h):
            raise ValueError(f"motion too large: corner ({x},{y}) maps to ({sx:.0f},{sy:.0f})")


def probe(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,nb_read_frames",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    s = json.loads(out.stdout)["streams"][0]
    md5 = hashlib.md5()
    for f in read_frames(path, s["width"], s["height"]):
        md5.update(f.tobytes())
    n = int(s["nb_read_frames"])
    num, den = (int(x) for x in s["r_frame_rate"].split("/"))
    return {
        "file": path.name,
        "width": s["width"],
        "height": s["height"],
        "frames": n,
        "fps": num / den,
        "duration_s": n * den / num,
        "decoded_md5": md5.hexdigest(),
    }


def decimate(src: Path, out: Path, keep_every: int, size: str | None) -> None:
    """Keep every Nth frame (exact frames, no blending), optionally Lanczos-resized."""
    rate = probe(src)["fps"] / keep_every
    vf = "select=not(mod(n\\,%d)),setpts=N/(%.6f*TB)" % (keep_every, rate)
    if size:
        vf += f",scale={size}:flags=lanczos"
    run([FF, "-v", "error", "-y", "-i", str(src), "-vf", vf, "-r", f"{rate:.6f}", *FFV1, str(out)])


# stem: (parent, keep_every, size or None, display_ref, encode)
VARIANTS = {
    "imx_bob_1080_10": ("imx_bob_1080", 3, None, "imx_bob_1080_10", False),
    "imx_static_1080_10": ("imx_static_1080", 3, None, "imx_static_1080_10", False),
    "imx_bob_720_10": ("imx_bob_1080", 3, "1280:720", "imx_bob_1080_10", True),
    "imx_bob_360_10": ("imx_bob_1080", 3, "640:360", "imx_bob_1080_10", True),
    "imx_static_720_10": ("imx_static_1080", 3, "1280:720", "imx_static_1080_10", True),
    "n6_bob_9": ("n6_bob", 2, None, "n6_bob_9", True),
    "n6_bob_400_9": ("n6_bob", 2, "640:400", "n6_bob_9", True),
}
DISPLAY_REF = {"imx_static_720": "imx_static_1080", "imx_bob_720": "imx_bob_1080"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument(
        "--variants",
        action="store_true",
        help="only (re)build the low-budget variants and refresh sources.json",
    )
    a = ap.parse_args(argv)
    src = a.data / "sources"
    src.mkdir(exist_ok=True)
    if a.variants:
        for stem, (parent, n, size, _ref, _enc) in VARIANTS.items():
            decimate(src / f"{parent}.mkv", src / f"{stem}.mkv", n, size)
        return write_meta(src)
    imx = ["-f", "mjpeg", "-framerate", "30", "-i", str(a.data / "imx708/master.mjpeg")]
    n6m = json.loads((a.data / "n6_q90/master.json").read_text())
    n6_fps = f"{n6m['frames'] - 1}/{n6m['duration_s']:.4f}"
    n6 = ["-f", "mjpeg", "-framerate", n6_fps, "-i", str(a.data / "n6_q90/master.mjpeg")]

    ffv1_from(imx, RANGE, "30", src / "imx_static_1080.mkv")
    ffv1_from(
        ["-i", str(src / "imx_static_1080.mkv")],
        "scale=1280:720:flags=lanczos",
        "30",
        src / "imx_static_720.mkv",
    )
    bob(src / "imx_static_1080.mkv", src / "imx_bob_1080.mkv", 1920, 1080, "30")
    ffv1_from(
        ["-i", str(src / "imx_bob_1080.mkv")],
        "scale=1280:720:flags=lanczos",
        "30",
        src / "imx_bob_720.mkv",
    )
    ffv1_from(n6, RANGE, n6_fps, src / "n6_static.mkv")
    bob(src / "n6_static.mkv", src / "n6_bob.mkv", 1280, 800, n6_fps)
    return write_meta(src)


def write_meta(src: Path) -> int:
    meta = {p.stem: probe(p) for p in sorted(src.glob("*.mkv"))}
    for stem, m in meta.items():
        v = VARIANTS.get(stem)
        m["display_ref"] = v[3] if v else DISPLAY_REF.get(stem, stem)
        m["encode"] = v[4] if v else True
    meta["_motion"] = {
        "roll_deg": ROLL_DEG,
        "roll_period_s": ROLL_T,
        "sway_frac": SWAY,
        "sway_period_s": SWAY_T,
        "heave_frac": HEAVE,
        "heave_period_s": HEAVE_T,
        "zoom": ZOOM,
    }
    (src / "sources.json").write_text(json.dumps(meta, indent=1))
    for k, v in meta.items():
        if not k.startswith("_"):
            print(
                k,
                v["width"],
                v["height"],
                v["frames"],
                round(v["fps"], 3),
                round(v["duration_s"], 2),
                (src / v["file"]).stat().st_size // 1_000_000,
                "MB",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
