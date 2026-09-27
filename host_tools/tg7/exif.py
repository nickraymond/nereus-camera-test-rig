"""TG-7 EXIF via exiftool — SPEC §20 "TG-7 facts".

One exiftool call handles many files (``-j -n``: JSON, numeric values). Each record is
parsed into plain fields and cross-checked, failing loudly on inconsistency:

- UTC time = DateTimeOriginal + OffsetTimeOriginal (standard EXIF), which must agree with
  the Olympus maker-note DateTimeUTC. The camera clock was UTC−8 (PST).
- flash fired = bit 0 of EXIF Flash (the Olympus InternalFlash field is useless).
- WaterDepth is the standard EXIF tag, in metres.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

TAGS = [
    "WaterDepth", "DateTimeOriginal", "OffsetTimeOriginal", "DateTimeUTC", "ExposureTime",
    "ISO", "FNumber", "FocalLength", "Flash", "BlackLevel2", "ColorMatrix", "WB_RBLevels",
    "Make", "Model",
]
_FMT = "%Y:%m:%d %H:%M:%S"


class ExifError(RuntimeError):
    """exiftool is missing, failed, or returned inconsistent metadata."""


def run_exiftool(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    """Raw exiftool JSON records for ``paths`` (one subprocess call)."""
    args = ["exiftool", "-j", "-n", *(f"-{t}" for t in TAGS), *(str(p) for p in paths)]
    try:
        out = subprocess.run(args, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise ExifError("exiftool not found — `brew install exiftool` "
                        "(docs/hardware_setup.md)") from exc
    except subprocess.CalledProcessError as exc:
        raise ExifError(f"exiftool failed ({exc.returncode}): {exc.stderr.strip()}") from exc
    return json.loads(out.stdout)


def _ints(value: Any) -> list[int]:
    return [int(v) for v in str(value).split()]


def parse(record: dict[str, Any]) -> dict[str, Any]:
    """One exiftool record → plain fields. Raises ``ExifError`` on inconsistent clocks."""
    src = record.get("SourceFile", "?")
    local = datetime.strptime(record["DateTimeOriginal"], _FMT)
    sign = -1 if record["OffsetTimeOriginal"].startswith("-") else 1
    hh, mm = (int(v) for v in record["OffsetTimeOriginal"].lstrip("+-").split(":"))
    offset = timezone(sign * timedelta(hours=hh, minutes=mm))
    utc = local.replace(tzinfo=offset).astimezone(timezone.utc)
    if "DateTimeUTC" in record:
        maker = datetime.strptime(record["DateTimeUTC"], _FMT).replace(tzinfo=timezone.utc)
        if abs((maker - utc).total_seconds()) > 1:
            raise ExifError(f"{src}: DateTimeOriginal+Offset = {utc.isoformat()} but "
                            f"Olympus DateTimeUTC = {maker.isoformat()}")

    black = _ints(record["BlackLevel2"]) if "BlackLevel2" in record else None
    matrix = _ints(record["ColorMatrix"]) if "ColorMatrix" in record else None
    wb = _ints(record["WB_RBLevels"]) if "WB_RBLevels" in record else None
    return {
        "path": src,
        "make": record.get("Make"),
        "model": record.get("Model"),
        "time_utc": utc.isoformat(),
        "camera_utc_offset": record["OffsetTimeOriginal"],
        "depth_m": record.get("WaterDepth"),
        "exposure_s": record.get("ExposureTime"),
        "iso": record.get("ISO"),
        "fnumber": record.get("FNumber"),
        "focal_length_mm": record.get("FocalLength"),
        "flash_fired": bool(int(record.get("Flash", 0)) & 1),
        # Olympus BlackLevel2 order is R, G1, G2, B (verified against LibRaw 0.22.1).
        "black_level2": black,
        # Rows sum to 256 (neutral maps to neutral); target space unverified (S2a).
        "color_matrix": [matrix[i:i + 3] for i in (0, 3, 6)] if matrix else None,
        # As-shot WB multipliers R, B relative to G (level / 256).
        "wb_rb": (wb[0] / 256, wb[1] / 256) if wb else None,
    }


def read_exif(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    return [parse(r) for r in run_exiftool(paths)]
