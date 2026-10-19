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
  - the IMX708 video's field is its ScalerCrop (imx708.meta.json) on the full 4608x2592 sensor.

Rev 16 (PE 005, Sun 10/18 9:30 PM; the successor-3 dry run on Friday's clips): every decoded tag's
corners are undistorted with the same fit's camera matrix and dist_coeffs before the pose; range,
speed, W2, F10 and the board pose use them. The coefficients are in normalised coordinates, so only
K is mapped to the frame size. The image itself is never undistorted (measure.py projects board
points back into raw pixels with the same K + coefficients). Guard: a mode not in MAPPED_MODES (or
an IMX708 clip with no ScalerCrop) keeps raw corners and says why (Lens.why).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

# bm #184 pool049 fits, intrinsics_full, copied exactly; the files (sha256) the Wed card cites:
CALIB_FILES = {
    "imx708": ("~/NereusData/bm_cam_legacy/2026-10-17_pool049_calib/"
               "20261017_152713_pool049_imx708_dng/summary.json",
               "178dd3556110a15603e2ba22ef3c0139b4af26d9c38352ffcaf9cd11e7b5444e"),
    "openmv_n6": ("~/NereusData/bm_cam_legacy/2026-10-17_pool049_calib/"
                  "20261017_153029_pool049_openmv_raw/summary.json",
                  "681dda546912506fdc33be40368dbcedeb8aa84d0d62c2ae793204c63443a458"),
}
CALIB_FILES["openmv_ae3"] = CALIB_FILES["openmv_n6"]
# camera -> (calibration width, height, fx, fy, cx, cy)
CALIB = {
    "imx708": (4608, 2592, 2628.2827517934816, 2627.9894986908967, 2313.8554199266064,
               1294.2294582813581),
    "openmv_n6": (1280, 800, 1628.8561990775179, 1630.0355714404836, 637.4060935322506,
                  490.38807564783536),
    "openmv_ae3": (1280, 800, 1394.2211146171771, 1393.2557752342311, 677.3690664567961,
                   375.0514639699826),
}
# camera -> dist_coeffs (OpenCV order; N6 / AE3 "standard" k1 k2 p1 p2 k3, IMX708 "rational")
DIST = {
    "imx708": (11.338614489468014, -41.08504653749358, 0.004718703593224851,
               0.005119347265476883, 27.277140257091695, 11.081435854095098,
               -44.979689528772745, 36.96860838773947),
    "openmv_n6": (-0.4214072011739352, 0.25358372909391313, 0.002648833257357536,
                  0.0012107824320776424, -0.10986596216508071),
    "openmv_ae3": (0.39104288364647183, -0.15981930550952725, 0.004303504641045737,
                   -0.0023332439182556846, 1.2675838245698394),
}
# Board frame sizes that show the calibrated 1280 x 800 field (docstring above); others: guard.
MAPPED_MODES = {"openmv_n6": {(1280, 800), (960, 600), (640, 400)},
                "openmv_ae3": {(1280, 800), (640, 400)}}
TAG_DICT = cv2.aruco.DICT_APRILTAG_25h9  # the V3 card's corner tags (LESSONS §5)


@dataclass(frozen=True)
class Lens:
    """One camera mode's lens: K for this frame size, and dist_coeffs when the mode maps to the
    fit (``mapped``); otherwise dist is None and corners stay raw (rev 16 guard, ``why``)."""
    cam: str
    K: np.ndarray
    dist: np.ndarray | None
    mapped: bool
    why: str

    def record(self) -> dict:
        return {"camera": self.cam, "K": self.K.tolist(), "mapped": self.mapped, "why": self.why,
                "dist": None if self.dist is None else self.dist.tolist(),
                "calib_file": CALIB_FILES[self.cam][0], "calib_sha256": CALIB_FILES[self.cam][1]}


def lens_for(cam: str, width: int, height: int,
             scaler_crop: tuple[int, int, int, int] | None = None,
             force_raw: bool = False) -> Lens:
    """The rev 16 lens for a frame size; ``force_raw`` = the Wed card put this camera on raw
    corners."""
    if cam == "imx708":
        ok, why = scaler_crop is not None, (
            f"ScalerCrop {scaler_crop} -> K scaled" if scaler_crop else
            "no ScalerCrop recorded: the field is unknown, raw corners (rev 16 guard)")
        K = camera_matrix(cam, width, height, scaler_crop)
    else:
        ok = (width, height) in MAPPED_MODES[cam]
        why = (f"{width}x{height} shows the calibrated 1280x800 field -> K scaled" if ok else
               f"{width}x{height} is not a known full-field mode: raw corners (rev 16 guard)")
        K = camera_matrix(cam, width, height)
    if force_raw:
        ok, why = False, "raw corners by the Wed card (rev 16 guard)"
    return Lens(cam, K, np.array(DIST[cam], dtype=float) if ok else None, ok, why)


def undistort_corners(tags: dict[int, np.ndarray], lens: Lens) -> dict[int, np.ndarray]:
    """Rev 16: tag corners in ideal (undistorted) pixels of lens.K; unchanged on the guard path."""
    if lens.dist is None:
        return {i: c.astype(float) for i, c in tags.items()}
    return {i: cv2.undistortPoints(c.reshape(-1, 1, 2).astype(np.float64), lens.K, lens.dist,
                                   P=lens.K).reshape(-1, 2) for i, c in tags.items()}


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
