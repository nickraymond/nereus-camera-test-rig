"""The four 1600x900 IMX708 study frames (native-density crops, as nrjxl sends them)."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from compression_study import rois

STUDY = Path(__file__).resolve().parents[1]
W, H = 1600, 900


def _card_centred(roi_key: str, native=(4608, 2592)):
    roi = rois.load(STUDY / "config" / "card_rois.yaml")[roi_key]
    c = np.mean([np.mean(p["quad"], axis=0) for p in roi["patches"].values()], axis=0) * 2
    x0 = int(np.clip(c[0] - W / 2, 0, native[0] - W)) // 2 * 2
    y0 = int(np.clip(c[1] - H / 2, 0, native[1] - H)) // 2 * 2
    return (x0, y0, W, H)


def frames() -> list[tuple[str, Path, tuple, str]]:
    """(name, dng, crop, note). Data roots from NRJXL_DATA (primary checkout data/) and
    NRJXL_POOL (the run-7 pool sweep experiment folder)."""
    data = Path(os.environ["NRJXL_DATA"])
    pool = Path(os.environ["NRJXL_POOL"])
    rig = STUDY / "presets" / "runs" / "sweep_20261003T203525Z" / "cap" / "stop_+0_r0.dng"
    return [
        ("s4_cool", data / "s4_20260930/cool_imx708/stop_-1_r0.dng", _card_centred("imx708_cool"),
         "S4 bench, cool LED, card + chart at 0.5 m"),
        ("s4_warm", data / "s4_20260930/warm_imx708/stop_-1_r0.dng", _card_centred("imx708_warm"),
         "S4 bench, warm 3200 K LED"),
        ("rig_1003", rig, (1504, 846, W, H), "nereus002 2026-10-03 sweep (byte-target prior frame)"),
        ("pool_run7", pool / "captures/imx708/imx708_raw_sweep06_20261005T035837Z.dng",
         (1504, 846, W, H), "pool rig run 7, 1/60 s, 2x 5300 K LEDs"),
    ]
