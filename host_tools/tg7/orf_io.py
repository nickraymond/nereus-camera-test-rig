"""TG-7 ORF → ``RawFrame`` (rawpy / LibRaw + exiftool) — SPEC §4 Phase 8 S0.

Pixels, CFA, black/white level and the valid area come from LibRaw; capture metadata
(time, depth, exposure, flash, maker-note matrix) from exiftool. The two are cross-checked
and disagreement fails loudly:

- LibRaw's per-channel black (R, G, B, G2) must equal Olympus BlackLevel2 (R, G1, G2, B).
- LibRaw's as-shot WB must equal WB_RBLevels / 256.

Verified on the TG-7 Channel Islands set (2026-09-26, rawpy 0.27.1 / LibRaw 0.22.1): the
raw readout is 4040×3016, LibRaw's visible area is the left 4014 columns (the right 26 are
not optical black — their means are 260–490 counts), and the camera JPEG is the 4000×3000
crop at (8, 8), recorded as ``source["jpeg_crop"]``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from nereus_camera_test_rig.color.raw_io import RawFrame

from .exif import ExifError, read_exif


def black_per_position(black_per_channel, raw_pattern) -> tuple[float, ...]:
    """LibRaw black (indexed by colour: R, G, B, G2) → per CFA position, row-major."""
    return tuple(float(black_per_channel[raw_pattern[i // 2][i % 2]]) for i in range(4))


def check_black(libraw_rgbg, blacklevel2, path) -> None:
    """Olympus BlackLevel2 is (R, G1, G2, B); LibRaw per-channel is (R, G, B, G2)."""
    if blacklevel2 is None:
        return
    r, g1, g2, b = blacklevel2
    if [int(v) for v in libraw_rgbg] != [r, g1, b, g2]:
        raise ExifError(f"{path}: LibRaw black {list(libraw_rgbg)} (R,G,B,G2) disagrees with "
                        f"BlackLevel2 {blacklevel2} (R,G1,G2,B)")


def read_orf(path: str | Path, exif: Optional[dict[str, Any]] = None) -> RawFrame:
    """Read one TG-7 ORF. Pass ``exif`` (from ``read_exif``) to avoid one exiftool call per
    file when reading many."""
    try:
        import rawpy
    except ImportError as exc:  # pragma: no cover
        raise ImportError("rawpy is not installed — `make install-tg7`") from exc

    path = Path(path)
    exif = exif if exif is not None else read_exif([path])[0]
    with rawpy.imread(str(path)) as raw:
        pattern = raw.raw_pattern.tolist()
        desc = raw.color_desc.decode()
        if desc != "RGBG" or sorted(sum(pattern, [])) != [0, 1, 2, 3]:
            raise ValueError(f"{path}: unexpected LibRaw colour layout {desc} {pattern}")
        cfa = "".join("RGBG"[pattern[i // 2][i % 2]] for i in range(4))
        check_black(raw.black_level_per_channel, exif["black_level2"], path)
        wb = raw.camera_whitebalance
        as_shot = (wb[0] / wb[1], 1.0, wb[2] / wb[1])
        if exif["wb_rb"] and not np.allclose(as_shot[::2], exif["wb_rb"], atol=1e-3):
            raise ExifError(f"{path}: LibRaw WB {as_shot} disagrees with WB_RBLevels "
                            f"{exif['wb_rb']}")
        s = raw.sizes
        return RawFrame(
            mosaic=raw.raw_image.copy(),
            cfa=cfa,
            black_level=black_per_position(raw.black_level_per_channel, pattern),
            white_level=float(raw.white_level),
            valid_crop=(s.left_margin, s.top_margin, s.width, s.height),
            exposure_s=exif["exposure_s"],
            iso=exif["iso"],
            fnumber=exif["fnumber"],
            as_shot_wb=as_shot,
            color_matrix=(np.asarray(exif["color_matrix"], dtype=np.float64) / 256.0
                          if exif["color_matrix"] else None),
            source={
                "reader": f"tg7/rawpy {rawpy.__version__} LibRaw "
                          f"{'.'.join(map(str, rawpy.libraw_version))}",
                "path": str(path),
                "color_matrix": "Olympus maker-note ColorMatrix / 256 (rows sum to 1; "
                                "target space unverified, S2a)",
                "libraw_rgb_xyz_matrix": raw.rgb_xyz_matrix[:3].tolist(),
                "jpeg_crop": (s.crop_left_margin, s.crop_top_margin, s.crop_width,
                              s.crop_height),
                "exif": exif,
            },
        )
