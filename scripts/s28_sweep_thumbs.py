"""Sunrise sweep cut-sheet tiles, made ON THE PI: the camera JPEG of each chosen pair, cropped to
the B3a field of view (1600x900 at native x=1504, y=846 of the 4608x2592 frame) and resized
to 800x450 (INTER_AREA, 0.5x). No colour processing: these are the camera's own JPEGs.

    python scripts/s28_sweep_thumbs.py <run_dir> 0600 0730 0810 1020
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2

CROP = (1504, 846, 1600, 900)


def main() -> int:
    run = Path(sys.argv[1])
    out = run / "tiles"
    out.mkdir(exist_ok=True)
    x, y, w, h = CROP
    for slot in sys.argv[2:]:
        for prof in ("lowgain", "stock"):
            img = cv2.imread(str(run / f"s{slot}_{prof}.jpg"))
            if img is None:
                print("missing", slot, prof)
                continue
            crop = img[y : y + h, x : x + w]
            tile = cv2.resize(crop, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(out / f"s{slot}_{prof}.jpg"), tile, [cv2.IMWRITE_JPEG_QUALITY, 88])
            print("tile", slot, prof)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
