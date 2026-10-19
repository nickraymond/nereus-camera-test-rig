"""Board range from its AprilTags, per frame (pool test 2 rev 12: "every frame's range comes from
the board's tag pose, using bm #184's centre focal lengths ... and Nick's measured tag size").

The intrinsics are bm #184's pool049 ChArUco fit (NOT VALIDATED as a lens model, but centre-stable),
from ~/NereusData/bm_cam_legacy/2026-10-17_pool049_calib/2026101715*/summary.json, intrinsics_full.
Rev 13: they are scaled to a Wed mode only if that mode shows the calibrated (HD) field:
  - N6 960x600: the ISP scales the HD field (openmv/n6/board_config.py; OpenMV v5.0.1 source);
  - 640x400 (AE3, and the N6 at VGA): **binned, full field**. OpenMV v5.0.1
    drivers/sensors/pag7936.c: vga_regs set AVERAGE_MODE 1 with a 648x408 array (hd_regs:
    AVERAGE_MODE 0, 1296x816) and an ISP window of 640x400 at offset (4, 4), i.e. HD's 1280x800 at
    (8, 8) at half scale. So fx, fy, cx and cy all halve; there is no window shift;
  - the IMX708 video's field is its ScalerCrop (imx708.meta.json) on the full 4608x2592 sensor. No distortion model is applied (the
fit is not validated as one); the board is held near the image centre, where it matters least.
"""

from __future__ import annotations

import re

import cv2
import numpy as np

# camera -> (calibration width, height, fx, fy, cx, cy), bm #184 pool049, intrinsics_full
CALIB = {
    "imx708": (4608, 2592, 2628.283, 2627.989, 2313.855, 1294.229),
    "openmv_n6": (1280, 800, 1628.856, 1630.036, 637.406, 490.388),
    "openmv_ae3": (1280, 800, 1394.221, 1393.256, 677.369, 375.051),
}
TAG_DICT = cv2.aruco.DICT_APRILTAG_25h9  # the V3 card's corner tags (LESSONS §5)


def camera_matrix(cam: str, width: int, height: int,
                  scaler_crop: tuple[int, int, int, int] | None = None) -> np.ndarray:
    """K for a frame of width x height from ``cam``. ``scaler_crop`` (x, y, w, h) in calibration
    pixels is the sensor area the frame shows (IMX708 video); None = the whole calibrated field."""
    cw, ch, fx, fy, cx, cy = CALIB[cam]
    x0, y0, w, h = scaler_crop or (0, 0, cw, ch)
    sx, sy = width / w, height / h
    return np.array([[fx * sx, 0, (cx - x0) * sx], [0, fy * sy, (cy - y0) * sy], [0, 0, 1]],
                    dtype=float)


def parse_scaler_crop(text: str) -> tuple[int, int, int, int] | None:
    """libcamera's ScalerCrop as rpicam writes it, e.g. '(0, 0)/4608x2592' or [0, 0, 4608, 2592]."""
    if isinstance(text, (list, tuple)) and len(text) == 4:
        return tuple(int(v) for v in text)
    m = re.search(r"\(?\s*(\d+)\s*,\s*(\d+)\s*\)?\s*/\s*(\d+)\s*x\s*(\d+)", str(text or ""))
    return tuple(int(g) for g in m.groups()) if m else None


def detect_tags(gray: np.ndarray) -> dict[int, np.ndarray]:
    """tag id -> its 4 corners (4 x 2 px; OpenCV order TL, TR, BR, BL of the black square)."""
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(TAG_DICT),
                                  cv2.aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(gray)
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2).astype(float) for i, c in zip(ids.ravel(), corners)}


def tag_depth_m(corners: np.ndarray, edge_mm: float, K: np.ndarray) -> float | None:
    """The tag centre's depth along the optical axis (m), by IPPE on its square, no distortion."""
    h = edge_mm / 2.0
    obj = np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], dtype=float)
    ok, _rvec, tvec = cv2.solvePnP(obj, corners.astype(float), K, None,
                                   flags=cv2.SOLVEPNP_IPPE_SQUARE)
    return float(tvec[2, 0]) / 1000.0 if ok and tvec[2, 0] > 0 else None


def board_range(tags: dict[int, np.ndarray], card_ids: tuple[int, ...], edge_mm: float,
                K: np.ndarray) -> dict:
    """The board's range = the median depth of its decoded corner tags; whole = all its tags
    decoded (they sit in the card's corners); px_per_mm = fx / depth, at the board."""
    depths = [d for i in card_ids if i in tags
              for d in [tag_depth_m(tags[i], edge_mm, K)] if d is not None]
    rng = float(np.median(depths)) if depths else None
    return {"range_m": rng, "tags": len(depths), "board_whole": len(depths) == len(card_ids),
            "tag_depths_m": depths,
            "px_per_mm": float(K[0, 0]) / (rng * 1000.0) if rng else None}
