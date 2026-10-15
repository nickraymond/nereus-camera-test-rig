"""Cut the pre-registered 5 s windows once, as lossless FFV1, and write the run MANIFEST (Mac).

    python -m compression_study.video_100k.prepare            # main window (frames 20-69)
    python -m compression_study.video_100k.prepare --sens     # + sensitivity window (150-199)

For each pool clip and window: decode source frames [start, start + 50) into
``REF_DIR/<clip>_f<start>.mkv`` (FFV1, yuv420p, 10 fps), then check it frame for frame
(framemd5 of the decoded pixels) against the same frames decoded from the original H.264.
A mismatch or a wrong frame count fails loudly. ``RUN_ROOT/MANIFEST.md`` records what the run
is, the SHA-256 of every source and reference file, the code commit and the host.
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .common import (
    BASE_CLIPS,
    FPS,
    NFR,
    REF_DIR,
    RUN_ROOT,
    WIN_MAIN,
    WIN_SENS,
    pool_path,
    q,
    sh,
    src_path,
)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def frame_md5s(cmd_input: str, vf: str) -> list[str]:
    out = subprocess.run(
        f"ffmpeg -hide_banner -v error {cmd_input} -vf {q(vf)} -pix_fmt yuv420p -f framemd5 -",
        shell=True,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ln.split(",")[-1].strip() for ln in out.splitlines() if ln and not ln.startswith("#")]


def cut(base: str, start: int) -> Path:
    clip = base if start == WIN_MAIN else f"{base}@{start}"
    out = src_path(clip)
    trim = f"trim=start_frame={start}:end_frame={start + NFR},setpts=N/({FPS}*TB)"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part.mkv")
        sh(
            f"ffmpeg -hide_banner -v error -y -i {q(pool_path(base))} -vf {q(trim)} "
            f"-fps_mode cfr -r {FPS} -pix_fmt yuv420p -c:v ffv1 -level 3 {q(tmp)} && "
            f"mv {q(tmp)} {q(out)}"
        )
    want = frame_md5s(f"-i {q(pool_path(base))}", trim)
    got = frame_md5s(f"-i {q(out)}", "null")
    if len(got) != NFR or got != want:
        raise RuntimeError(
            f"{out}: {len(got)} frames; pixel match with source frames {start}..{start + NFR - 1}: "
            f"{sum(a == b for a, b in zip(got, want))}/{len(want)} (expected {NFR}/{NFR})"
        )
    print(f"{out.name}: {NFR} frames, pixel-identical to source frames {start}-{start + NFR - 1}")
    return out


def git_sha() -> str:
    here = Path(__file__).resolve().parent
    sha = subprocess.run(
        ["git", "-C", str(here), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(here), "status", "--porcelain", "--", "."], capture_output=True, text=True
    ).stdout.strip()
    return sha + (" (+ uncommitted changes in compression_study/video_100k)" if dirty else "")


def manifest(starts: list[int]) -> None:
    lines = [
        "# MANIFEST — video ≤ 100 KB study, 5 s clips (PREREG v2)",
        "",
        "What: encodes, scores and logs for the question \"best 5 s underwater clip in "
        "≤ 100,000 B, encodable on a Pi Zero 2 W\". Pre-registration: "
        "`compression_study/video_100k/PREREG.md` (rig repo, branch `study/video-100k-budget`).",
        f"Written: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Host: {platform.node()} ({platform.platform()})",
        f"Code: {git_sha()}",
        "",
        "Layout: `ref/` = the 5 s window references (lossless FFV1; encoder input and scoring "
        "reference), `stage1/` Mac codec field, `pi/` Pi container runs (stages 2-4), each with "
        "`results.jsonl`, `enc/<candidate>/<clip>.<ext>` (the bytes sent) and `logs/`.",
        "",
        "## Sources (read only)",
        "",
        "| file | SHA-256 |",
        "|---|---|",
    ]
    for base in BASE_CLIPS:
        p = pool_path(base)
        lines.append(f"| `{p}` | `{sha256(p)}` |")
    lines += ["", "## Window references", "", "| file | source frames | SHA-256 |", "|---|---|---|"]
    for start in starts:
        for base in BASE_CLIPS:
            clip = base if start == WIN_MAIN else f"{base}@{start}"
            p = src_path(clip)
            lines.append(f"| `ref/{p.name}` | {start}–{start + NFR - 1} | `{sha256(p)}` |")
    (RUN_ROOT / "MANIFEST.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {RUN_ROOT / 'MANIFEST.md'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sens", action="store_true", help="also cut the sensitivity window")
    a = ap.parse_args(argv)
    # keep the sensitivity window in the manifest once it has been cut
    have_sens = REF_DIR.exists() and any(REF_DIR.glob(f"*_f{WIN_SENS:03d}.mkv"))
    starts = [WIN_MAIN] + ([WIN_SENS] if a.sens or have_sens else [])
    for start in starts:
        for base in BASE_CLIPS:
            cut(base, start)
    manifest(starts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
