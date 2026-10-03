"""Card + checker regions for the 1.5 m sweep frames (2026-10-03), from a supplied tag-centre quad.

The plastic wrap's glare hides the left two tags from the decoder, so ``rois.find_rois`` (which
needs 3 decoded tags) fails. The right tags (1, 3) are decoded with ArUco on the camera JPEG
(same geometry as the DNG at full resolution); the left tags (0, 2) are placed at the centre of
their dark border square (Otsu threshold, largest dark component) — 32–34 px squares, the
same size as the decoded tags. Everything else is ``rois.find_rois`` unchanged.
"""

from __future__ import annotations

import cv2
import numpy as np

from compression_study import rois as R
from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.chart import find_chart, load_chart
from nereus_camera_test_rig.color.patches import homography, mosaic_to_binned, sample
from nereus_camera_test_rig.color.raw_io import bin2x2, normalize

DIC = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)


def tag_quad(jpeg_gray: np.ndarray, left_guess=((2157, 1207), (2156, 1301)),
             search=(1950, 1000, 2800, 1700)) -> np.ndarray:
    """Tag-centre quad (TL, TR, BR, BL) in mosaic px."""
    x0, y0, x1, y1 = search
    c = cv2.resize(jpeg_gray[y0:y1, x0:x1], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    co, ids, _ = cv2.aruco.ArucoDetector(DIC, cv2.aruco.DetectorParameters()).detectMarkers(c)
    cent = {int(k): q.reshape(4, 2).mean(0) / 2 + [x0 - 0.25, y0 - 0.25]
            for k, q in zip(ids.ravel(), co)}
    for t, (px, py) in zip((0, 2), left_guess):
        if t in cent:
            continue
        w = jpeg_gray[py - 30:py + 30, px - 30:px + 30]
        _, m = cv2.threshold(w, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        _, _, st, _ = cv2.connectedComponentsWithStats(m)
        x, y, ww, hh, _ = st[1 + np.argmax(st[1:, cv2.CC_STAT_AREA])]
        cent[t] = np.array([px - 30 + x + ww / 2 - 0.5, py - 30 + y + hh / 2 - 0.5])
    return np.array([cent[0], cent[1], cent[3], cent[2]], dtype=np.float64)


def find_rois_quad(frame, quad_raw: np.ndarray) -> dict:
    card, chart = load_card(R.CARD), load_chart(R.CHART)
    linear, saturated, cfa = normalize(frame)
    binned, _ = bin2x2(linear, cfa, saturated)
    H = mosaic_to_binned(None) @ homography(card, quad_raw)
    boxes = {p.id: (p.box, p.group) for p in card.patches}
    chart_info: dict = {}
    try:
        white = sample(binned, H, card.patch("gray_white").box)["mean"]
        found = find_chart(binned, H, white, chart, R.CHART_REGION)
        for pid, box in found.pop("boxes").items():
            boxes[f"chart_{pid}"] = (box, "chart")
        chart_info = {k: found[k] for k in ("n_found", "grid_rms_px", "patch_px")}
    except (ValueError, IndexError) as exc:
        chart_info = {"error": str(exc)}
    patches = {pid: {"group": g, "quad": R._map(H, R._inner(b))} for pid, (b, g) in boxes.items()}
    t0 = card.tags[card.corner_map["tl"]]
    tex = np.array([[t0.center[0] - t0.edge[0], t0.center[1] - t0.edge[1]],
                    [t0.center[0] + t0.edge[0], t0.center[1] + t0.edge[1]]])
    tq = cv2.perspectiveTransform(tex[None], H)[0]
    return {"patches": patches, "chart": chart_info, "quad_raw": quad_raw.round(2).tolist(),
            "texture_box": [int(tq[:, 0].min()), int(tq[:, 1].min()),
                            int(np.ceil(tq[:, 0].max())), int(np.ceil(tq[:, 1].max()))],
            "card_box": R._card_box(H, card)}
