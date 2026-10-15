"""Shared constants and helpers for the ≤ 100 KB video study (see PREREG.md).

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
CLIPS = [f"bmcam00{u}_round{r}" for u in (3, 4) for r in (1, 2, 3)]
EXPLORE = ["bmcam003_round1", "bmcam004_round3"]  # calm / diver: exploratory pair (PREREG)

W, H, FPS, NFR = 1280, 720, 10, 293
DUR_S = NFR / FPS  # 29.3 s
CAP_B = 100_000  # hard budget per clip, bytes as sent

# Card box per camera (x, y, w, h), drawn by hand on frame 150 of every round
# (check sheet: data/<run>/card_box_check.png). The cameras did not move between rounds.
CARD_BOX = {"bmcam003": (512, 306, 224, 164), "bmcam004": (540, 298, 226, 162)}

# SSIMULACRA2 frames: 10 per clip, spread over the clip (PREREG)
S2_FRAMES = [14 + 29 * k for k in range(10)]


def src_path(clip: str) -> Path:
    return POOL / f"{clip}_card2m_29s.mp4"


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
