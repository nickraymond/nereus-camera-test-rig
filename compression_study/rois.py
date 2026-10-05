"""Patch regions in Bayer-plane coordinates, found automatically on M0 (spec 5.3).

The card is located by its AprilTags on the RAW (``color.locate``) and the Pixel Perfect
chart in the card plane (``color.chart``) — the same code as the S4 ``calibrate`` stage. A
binned pixel of ``color.raw_io.bin2x2`` is exactly one pixel of each Bayer plane, so the
canonical → binned homography maps straight into plane coordinates.

Each region is stored as the four plane-coordinate corners of its central 60 % box (the
``color.patches`` convention), per camera × illuminant: the rig did not move during the
session, so one set of regions serves every stop and repeat. Masks are rebuilt from the
quads with ``cv2.fillPoly``; ``erode`` trims a further margin for the noise estimate.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import yaml

from nereus_camera_test_rig.color.card import Box, load_card
from nereus_camera_test_rig.color.chart import find_chart, load_chart
from nereus_camera_test_rig.color.jpeg_geometry import JpegMap
from nereus_camera_test_rig.color.locate import locate_frame, tag_geometry, tag_spec
from nereus_camera_test_rig.color.patches import INNER, homography, mosaic_to_binned, sample
from nereus_camera_test_rig.color.raw_io import bin2x2, normalize

from .common import REPO

CARD = REPO / "configs/cards/nereus_v1.yaml"
CHART = REPO / "configs/charts/pixel_perfect_24.yaml"
CHART_REGION = (600, 760, 1420, 1360)  # S4 session config: chart search box, card canonical px


def _inner(box: Box, frac: float = INNER) -> np.ndarray:
    cx, cy = box.x + box.w / 2, box.y + box.h / 2
    hw, hh = box.w * frac / 2, box.h * frac / 2
    return np.array([[cx - hw, cy - hh], [cx + hw, cy - hh], [cx + hw, cy + hh],
                     [cx - hw, cy + hh]], dtype=np.float64)


def _map(H: np.ndarray, pts: np.ndarray) -> list[list[float]]:
    out = cv2.perspectiveTransform(pts[None], H)[0]
    return [[round(float(x), 2), round(float(y), 2)] for x, y in out]


def find_rois(frame) -> dict:
    """{patches: {id: quad}, chart: {...}, tags_quad, texture_box} for one RawFrame."""
    card, chart = load_card(CARD), load_chart(CHART)
    rec = locate_frame(Path(frame.source.get("path", "frame")), None, card.corner_map,
                       JpegMap.offset(0, 0), raw_reader=lambda _p: frame,
                       geometry=tag_geometry(card), spec=tag_spec(card))
    if not rec["located"]:
        raise ValueError(f"card not located: {rec.get('reason')}")
    linear, saturated, cfa = normalize(frame)
    binned, _ = bin2x2(linear, cfa, saturated)
    # quad_raw is in mosaic px; mosaic_to_binned halves it → binned px = Bayer-plane px
    H = mosaic_to_binned(None) @ homography(card, np.asarray(rec["quad_raw"]))
    boxes = {p.id: (p.box, p.group) for p in card.patches}
    chart_info: dict = {}
    try:
        white = sample(binned, H, card.patch("gray_white").box)["mean"]
        found = find_chart(binned, H, white, chart, CHART_REGION)
        for pid, box in found.pop("boxes").items():
            boxes[f"chart_{pid}"] = (box, "chart")
        chart_info = {k: found[k] for k in ("n_found", "grid_rms_px", "patch_px")}
    except (ValueError, IndexError) as exc:
        chart_info = {"error": str(exc)}
    patches = {pid: {"group": g, "quad": _map(H, _inner(b))} for pid, (b, g) in boxes.items()}
    # textured region: the tag-0 neighbourhood (sharp black/white edges) in plane coords
    t0 = card.tags[card.corner_map["tl"]]
    tex = np.array([[t0.center[0] - t0.edge[0], t0.center[1] - t0.edge[1]],
                    [t0.center[0] + t0.edge[0], t0.center[1] + t0.edge[1]]])
    tq = cv2.perspectiveTransform(tex[None], H)[0]
    return {"patches": patches, "chart": chart_info, "tags_found": rec.get("tags_found"),
            "quad_raw": np.asarray(rec["quad_raw"]).round(2).tolist(),
            "texture_box": [int(tq[:, 0].min()), int(tq[:, 1].min()),
                            int(np.ceil(tq[:, 0].max())), int(np.ceil(tq[:, 1].max()))],
            "card_box": _card_box(H, card)}


def _card_box(H, card) -> list[int]:
    c = np.array([[0, 0], [card.canonical_w, 0], [card.canonical_w, card.canonical_h],
                  [0, card.canonical_h]], dtype=np.float64)
    q = cv2.perspectiveTransform(c[None], H)[0]
    return [int(q[:, 0].min()), int(q[:, 1].min()), int(np.ceil(q[:, 0].max())),
            int(np.ceil(q[:, 1].max()))]


def mask(quad, shape: tuple[int, int], erode: int = 0) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    cv2.fillPoly(m, [np.round(np.asarray(quad) * 16).astype(np.int32)], 1, shift=4)
    if erode:
        m = cv2.erode(m, np.ones((2 * erode + 1, 2 * erode + 1), np.uint8))
    return m.astype(bool)


def save(rois: dict, path: Path) -> None:
    head = ("# Card + chart regions per camera x illuminant, in Bayer-PLANE coordinates (x, y):\n"
            "# each quad = the central 60 % of a patch box mapped through the card homography.\n"
            "# Written by compression_study.rois from stop_-1_r0 (M0); review the overlay PNGs\n"
            "# in work/rois/. The rig did not move during the session (S4, 2026-09-30).\n")
    path.write_text(head + yaml.safe_dump(rois, sort_keys=False, width=200))


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def overlay(plane_rgb: np.ndarray, rois: dict, out: Path) -> None:
    """A quick visual check: patch quads drawn on a gamma-encoded plane image."""
    img = (np.clip(plane_rgb / max(np.percentile(plane_rgb, 99.5), 1e-6), 0, 1) ** (1 / 2.2)
           * 255).astype(np.uint8).copy()
    for pid, p in rois["patches"].items():
        col = (0, 255, 0) if p["group"] != "chart" else (255, 128, 0)
        cv2.polylines(img, [np.round(np.asarray(p["quad"])).astype(np.int32)], True, col, 1)
    x0, y0, x1, y1 = rois["texture_box"]
    cv2.rectangle(img, (x0, y0), (x1, y1), (255, 0, 255), 1)
    cv2.imwrite(str(out), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
