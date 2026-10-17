"""Shared paths and constants for the E7 video screen (see RULES.md; spec SPEC.md rev 3)."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from pathlib import Path

SSD = Path("/Volumes/Nick_R_SSD/NereusData/nereus-camera-test-rig")
# RULES.md §1: the 5 Oct 2022 + Oct 2025 Reef Check videos, C-locale order; 1st, 3rd and 5th are used.
REEFCHECK_ALL = {
    "Caspar-South-Restoration-25-10-16-1130-IanNorton-c1": SSD / "2026-10-17_urchin_prescreen",
    "Caspar-South-Restoration-25-10-16-1142-IanNorton-c2": SSD / "2026-10-17_urchin_prescreen",
    "Caspar-South-Restoration-25-10-16-1215-IanNorton-c3": SSD / "2026-10-15_reefcheck/video/October 2025",
    "FullersResto-2022-10-15-xxx-JanFreiwald-leadline": SSD / "2026-10-15_reefcheck/video/October 2022",
    "FullersResto-2022-10-15-xxx-JanFreiwald-leadline2": SSD / "2026-10-17_urchin_prescreen",
}
REEFCHECK = [sorted(REEFCHECK_ALL)[i] for i in (0, 2, 4)]
SHORT = {REEFCHECK[0]: "rc_c1", REEFCHECK[1]: "rc_c3", REEFCHECK[2]: "rc_lead2"}

# Pool (PREREG v2 cut, frames 20-69, 10 fps): rig #116's FFV1 references, reused read-only.
POOL_REF = Path.home() / "NereusData/nereus-camera-test-rig/2026-10-15_video100k_5s/ref"
POOL = [f"bmcam00{u}_round{r}" for u in (3, 4) for r in (1, 2, 3)]
DIVER = ["bmcam003_round3", "bmcam004_round3"]

RUN = Path.home() / "NereusData/nereus-camera-test-rig/2026-10-17_video_screen"
REF = RUN / "ref"
W, H = 1280, 720
WIN = 150  # source frames per Reef Check window (5.005 s at 29.97 fps)
STEP = {30: 1, 15: 2, 10: 3}  # decimation per frame rate (no interpolation)
BOARD_FPS = {"ae3": 10, "n6": 15, "imx708": 30}
SIZES_KB = [500, 100, 1000]  # 500 KB first (first look), then 100 KB and 1 MB


def q(p) -> str:
    return shlex.quote(str(p))


def sh(cmd: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True, **kw)


def src_file(name: str) -> Path:
    d = REEFCHECK_ALL[name]
    for ext in (".MP4", ".mp4"):
        if (d / f"{name}{ext}").exists():
            return d / f"{name}{ext}"
    raise FileNotFoundError(f"{name} not found in {d}")


def ref_path(clip: str, fps: int) -> Path:
    """FFV1 reference of a clip at a frame rate. Pool clips exist only at 10 fps."""
    if clip.startswith("bmcam"):
        if fps != 10:
            raise ValueError(f"{clip}: pool clips are 10 fps at the source; {fps} fps is not covered")
        return POOL_REF / f"{clip}_f020.mkv"
    return REF / f"{clip}_{fps}fps.mkv"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load_json(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default
