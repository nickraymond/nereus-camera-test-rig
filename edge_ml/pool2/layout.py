"""The W1 board in board millimetres (spec rev 13 must do 16): the v3 card + what is taped on.

Board mm = the card's own frame: origin at the card trim's top-left, x right, y down, as the
board faces the camera upright (configs/cards/<card>.yaml canonical px / 10). Things taped in the
board's margin sit outside 0..420 x 0..270 and may have negative coordinates.

From the card YAML (color.card.load_card): tag IDs + centres, patch boxes and truth (measured over
design when the YAML has a measured block). From the layout file (YAML, one per mounted board):

  board_id: <name>
  card: configs/cards/nereus_v3_c1.yaml      # repo-relative or absolute
  source: <who measured, how, when>          # e.g. "Nick, ruler, Tue 10/20"
  measured: true                             # false = a template; scoring refuses it
  tag_edge_mm: 63.0                          # Nick's measured black-square edge (Wed card)
  outline_mm: [x0, y0, x1, y1]               # the whole board, incl. everything taped on it
  samples:                                   # urchin samples: centre + spine-tip diameter
    u80a: {centre_mm: [x, y], diameter_mm: 80}
    u80b: ...; u50a: ...; u50b: ...
  slanted_edge: {centre_mm: [x, y], side_mm: 50, angle_deg: 5, grey_margin_mm: 10}
                                             # black square on mid-grey; margin = grey around it

Rev 13: positions are measured (Nick, or the rig session from a flat photo), never drawn by hand.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from edge_ml.pool2 import board
from nereus_camera_test_rig.color.card import Card, load_card

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_NAMES = ("u80a", "u80b", "u50a", "u50b")
GREY_MARGIN_MM = 10.0  # assumed mid-grey around the slanted square when the layout doesn't say


class LayoutError(ValueError):
    pass


@dataclass(frozen=True)
class Layout:
    board_id: str
    path: Path
    sha256: str
    source: str
    measured: bool
    card: Card
    tag_edge_mm: float
    tags_mm: dict[int, np.ndarray]                 # id -> 4 x 2 corners (TL, TR, BR, BL)
    tag_centres_mm: dict[int, tuple[float, float]]
    outline_mm: tuple[float, float, float, float]
    samples: dict[str, tuple[tuple[float, float], float]]   # name -> (centre, diameter)
    slanted_edge: dict

    def square_corners_mm(self) -> np.ndarray:
        """The slanted-edge square's corners (TL, TR, BR, BL before rotation), rotated."""
        se = self.slanted_edge
        (cx, cy), s, a = se["centre_mm"], float(se["side_mm"]), math.radians(se["angle_deg"])
        h = s / 2.0
        pts = np.array([[-h, -h], [h, -h], [h, h], [-h, h]])
        rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        return pts @ rot.T + [cx, cy]

    def outline_corners_mm(self) -> np.ndarray:
        x0, y0, x1, y1 = self.outline_mm
        return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)

    def patch_quad_mm(self, patch_id: str, inner: float = 0.6) -> np.ndarray:
        """A card patch's central ``inner`` fraction (color/patches.py samples the central 60 %),
        as 4 corners in board mm."""
        b = self.card.patch(patch_id).box
        x, y, w, h = b.x / 10.0, b.y / 10.0, b.w / 10.0, b.h / 10.0
        mx, my = w * (1 - inner) / 2, h * (1 - inner) / 2
        return np.array([[x + mx, y + my], [x + w - mx, y + my], [x + w - mx, y + h - my],
                         [x + mx, y + h - my]])


def _xy(v, where: str) -> tuple[float, float]:
    if not (isinstance(v, (list, tuple)) and len(v) == 2):
        raise LayoutError(f"{where}: expected [x, y] in mm, got {v!r}")
    return float(v[0]), float(v[1])


def load_layout(path: str | Path, allow_template: bool = False) -> Layout:
    path = Path(path)
    raw = path.read_bytes()
    data = yaml.safe_load(raw) or {}
    where = str(path)
    measured = bool(data.get("measured"))
    if not measured and not allow_template:
        raise LayoutError(f"{where}: measured is not true (a template); scoring needs the mounted "
                          "board's measured positions (spec rev 13 must do 16)")
    card_path = Path(data["card"])
    card = load_card(card_path if card_path.is_absolute() else ROOT / card_path)
    edge = float(data["tag_edge_mm"])
    centres = {t.id: ((t.center[0] + 0.5) / 10.0, (t.center[1] + 0.5) / 10.0)
               for t in card.tags.values()}
    tags_mm = {i: board.tag_corners_mm(cx, cy, edge) for i, (cx, cy) in centres.items()}
    samples = {}
    for name in SAMPLE_NAMES:
        s = (data.get("samples") or {}).get(name)
        if not s:
            raise LayoutError(f"{where}: samples.{name} missing")
        samples[name] = (_xy(s["centre_mm"], f"{where}: samples.{name}"),
                         float(s["diameter_mm"]))
    se = data.get("slanted_edge") or {}
    for k in ("centre_mm", "side_mm", "angle_deg"):
        if k not in se:
            raise LayoutError(f"{where}: slanted_edge.{k} missing")
    se = {"centre_mm": _xy(se["centre_mm"], f"{where}: slanted_edge"),
          "side_mm": float(se["side_mm"]), "angle_deg": float(se["angle_deg"]),
          "grey_margin_mm": float(se.get("grey_margin_mm", GREY_MARGIN_MM))}
    ol = data.get("outline_mm")
    if not (isinstance(ol, list) and len(ol) == 4 and ol[2] > ol[0] and ol[3] > ol[1]):
        raise LayoutError(f"{where}: outline_mm must be [x0, y0, x1, y1] with x1 > x0, y1 > y0")
    lay = Layout(board_id=str(data.get("board_id") or path.stem), path=path,
                 sha256=hashlib.sha256(raw).hexdigest(), source=str(data.get("source") or ""),
                 measured=measured, card=card, tag_edge_mm=edge, tags_mm=tags_mm,
                 tag_centres_mm=centres, outline_mm=tuple(float(v) for v in ol),
                 samples=samples, slanted_edge=se)
    _check_inside(lay, where)
    return lay


def _check_inside(lay: Layout, where: str) -> None:
    """Everything on the board lies inside its outline (a typo in a position fails here)."""
    x0, y0, x1, y1 = lay.outline_mm
    pts = [np.array(list(lay.tags_mm.values())).reshape(-1, 2), lay.square_corners_mm()]
    for name, ((cx, cy), d) in lay.samples.items():
        pts.append(np.array([[cx - d / 2, cy - d / 2], [cx + d / 2, cy + d / 2]]))
    allp = np.concatenate(pts)
    if (allp[:, 0] < x0).any() or (allp[:, 0] > x1).any() or (allp[:, 1] < y0).any() \
            or (allp[:, 1] > y1).any():
        raise LayoutError(f"{where}: a tag, sample or the slanted edge lies outside outline_mm")
