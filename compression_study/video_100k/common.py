"""Shared constants and helpers for the ≤ 100 KB video study (see PREREG.md, v2: 5 s clips).

Clips: a clip name is ``<camera>_<round>`` for the main 5 s window (source frames 20–69) or
``<camera>_<round>@<start>`` for another fixed window (the sensitivity window starts at 150).
``src_path`` is the window cut once by ``prepare.py`` as lossless FFV1: it is both the encoder
input and the scoring reference.

Coordinates: every box here is in NATIVE VIDEO coordinates of the 1280×720 pool clips
(rpicam-vid preset wide_720p = sensor mode 2304×1296 full field, downscaled; not ScalerCrop).
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path

POOL = Path.home() / "NereusPool/20261014_POOL_REVIEW/1_VIDEOS"
# Run data (never in the repo); prepare.py writes the window references + MANIFEST.md here.
RUN_ROOT = Path.home() / "NereusData/nereus-camera-test-rig/2026-10-15_video100k_5s"
REF_DIR = RUN_ROOT / "ref"

BASE_CLIPS = [f"bmcam00{u}_round{r}" for u in (3, 4) for r in (1, 2, 3)]
WIN_MAIN, WIN_SENS = 20, 150  # first source frame of the main / sensitivity window (PREREG v2)
CLIPS = BASE_CLIPS  # main window
SENS_CLIPS = [f"{c}@{WIN_SENS}" for c in BASE_CLIPS]
EXPLORE = ["bmcam003_round1", "bmcam004_round3"]  # calm / diver: exploratory pair (PREREG)

W, H, FPS, NFR = 1280, 720, 10, 50  # NFR = frames per 5 s clip
DUR_S = NFR / FPS  # 5.0 s
CAP_B = 100_000  # hard budget per clip, bytes as sent

# Card box per camera (x, y, w, h), drawn by hand on frame 150 of every round
# (check sheet: data/<run>/card_box_check.png). The cameras did not move between rounds.
CARD_BOX = {"bmcam003": (512, 306, 224, 164), "bmcam004": (540, 298, 226, 162)}

# SSIMULACRA2 frames: 10 per clip, spread over the clip (PREREG: window frames 2 + 5k)
S2_FRAMES = [2 + 5 * k for k in range(10)]


def split_clip(clip: str) -> tuple[str, int]:
    """'bmcam003_round1' -> ('bmcam003_round1', 20); 'bmcam003_round1@150' -> (..., 150)."""
    base, _, start = clip.partition("@")
    return base, int(start) if start else WIN_MAIN


def pool_path(base: str) -> Path:
    """The original 29.3 s pool clip (H.264), read only by prepare.py."""
    return POOL / f"{base}_card2m_29s.mp4"


def src_path(clip: str) -> Path:
    base, start = split_clip(clip)
    return REF_DIR / f"{base}_f{start:03d}.mkv"


def clip_set(name: str) -> list[str]:
    """--clips value: explore | all | sens | comma list."""
    sets = {"explore": EXPLORE, "all": CLIPS, "sens": SENS_CLIPS}
    return sets[name] if name in sets else name.split(",")


def card_box(clip: str) -> tuple[int, int, int, int]:
    return CARD_BOX[clip.split("_")[0]]


def sh(cmd: str, log: Path | None = None, timeout: float | None = None) -> float:
    """Run a shell pipeline (bash, pipefail); raise loudly with the log tail on failure.

    Returns wall seconds."""
    t0 = time.time()
    p = subprocess.run(
        ["bash", "-o", "pipefail", "-c", cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    wall = time.time() - t0
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as f:
            f.write(f"$ {cmd}\n[rc={p.returncode} wall={wall:.1f}s]\n{p.stderr[-4000:]}\n")
    if p.returncode != 0:
        raise RuntimeError(f"command failed rc={p.returncode}: {cmd}\n{p.stderr[-2000:]}")
    return wall


def q(p) -> str:
    return shlex.quote(str(p))


def append_jsonl(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
