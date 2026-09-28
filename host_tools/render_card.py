"""Render a reference card YAML to print files and previews (Phase 8, card V3).

The card YAML (``configs/cards/<card>.yaml``) is the single source of truth (SPEC §20); this
tool only draws it. It needs the V3 layout fields (``canonical.px_per_mm``, ``layout``,
``back``), so V2 cannot be rendered with it (V2's print master is the vector PDF in
``tests/fixtures/reference_card/``).

    python -m host_tools.render_card configs/cards/nereus_v3_c1.yaml --out results/cards/

writes ``<card_id>_front.svg/.pdf``, ``<card_id>_back.svg/.pdf`` and ``<card_id>_preview.png``.
Print files are the trimmed card plus a 3 mm bleed of the surround colour, crop marks and the
cut line (rounded corners) as a separate magenta hairline. Colours are the design sRGB values;
the printed card is measured afterwards (docs/reference_card_v3.md, "Print and measure").
Mac-side tool: the PDF writer uses matplotlib (``[tg7]`` extra).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:  # run this checkout's code, not another worktree's
    sys.path.insert(0, str(_REPO / "src"))

from nereus_camera_test_rig.color.card import Card, load_card  # noqa: E402
from nereus_camera_test_rig.config import load_yaml  # noqa: E402

FAMILIES = {"tag25h9": "DICT_APRILTAG_25h9", "tag36h11": "DICT_APRILTAG_36h11",
            "tag16h5": "DICT_APRILTAG_16h5"}
BLEED_MM = 3.0
MARK_MM = 8.0  # space outside the bleed for crop marks


def opencv_dictionary(family: str):
    name = FAMILIES.get(family, family)
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


def marker_bits(dictionary, marker_id: int) -> np.ndarray:
    """Cells of one marker including its 1-cell black border; 1 = black."""
    n = dictionary.markerSize + 2
    img = cv2.aruco.generateImageMarker(dictionary, marker_id, n * 10, borderBits=1)
    return (img[5::10, 5::10] < 128).astype(np.uint8)


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float
    rgb: tuple
    id: str = ""


@dataclass(frozen=True)
class Label:
    x: float
    y: float
    text: str
    size: float
    rgb: tuple = (0, 0, 0)
    anchor: str = "start"  # start | middle | end


@dataclass
class Side:
    """One printed side in card mm (origin = top-left of the trim, y down)."""

    width: float
    height: float
    background: tuple
    corner_radius: float
    rects: list
    labels: list


def _marker_rects(bits: np.ndarray, x0: float, y0: float, edge: float, dark: tuple,
                  prefix: str) -> list[Rect]:
    n = bits.shape[0]
    cell = edge / n
    return [Rect(x0 + k * cell, y0 + r * cell, cell, cell, dark, f"{prefix}_{r}_{k}")
            for r in range(n) for k in range(n) if bits[r, k]]


def front(card: Card, spec: dict) -> Side:
    """The measurement side: surround, patches, then tags on their light quiet squares."""
    px = float(spec["canonical"]["px_per_mm"])
    lay = spec["layout"]
    dark, light = tuple(lay["tag_dark_rgb"]), tuple(lay["tag_light_rgb"])
    rects: list[Rect] = []
    for p in card.patches:
        rects.append(Rect(p.box.x / px, p.box.y / px, p.box.w / px, p.box.h / px, p.truth, p.id))
    d = opencv_dictionary(card.tag_family)
    for tid, tag in sorted(card.tags.items()):
        edge = float(spec["tags"][tid]["edge_mm"])
        cx, cy = (tag.center[0] + 0.5) / px, (tag.center[1] + 0.5) / px  # pixel centre -> mm
        q = edge / (d.markerSize + 2) * float(lay["tag_quiet_cells"])
        rects.append(Rect(cx - edge / 2 - q, cy - edge / 2 - q, edge + 2 * q, edge + 2 * q, light,
                          f"tag{tid}_quiet"))
        rects += _marker_rects(marker_bits(d, tid), cx - edge / 2, cy - edge / 2, edge, dark,
                               f"tag{tid}")
    size = spec["physical_mm"]
    return Side(float(size["card_width"]), float(size["card_height"]), tuple(lay["surround_rgb"]),
                float(lay["corner_radius_mm"]), rects, [])


def back(card: Card, spec: dict) -> Side:
    """The calibration side: an OpenCV ChArUco board (top-left square black), plus a label."""
    b = spec["back"]
    nx, ny = b["squares"]
    sq, mk = float(b["square_mm"]), float(b["marker_mm"])
    d = opencv_dictionary(b["dictionary"])
    size = spec["physical_mm"]
    w, h = float(size["card_width"]), float(size["card_height"])
    x0, y0 = (w - nx * sq) / 2, (h - ny * sq) / 2
    rects: list[Rect] = []
    mid = int(b.get("first_marker_id", 0))
    for r in range(ny):
        for k in range(nx):
            x, y = x0 + k * sq, y0 + r * sq
            if (r + k) % 2 == 0:
                rects.append(Rect(x, y, sq, sq, (0, 0, 0), f"sq_{r}_{k}"))
            else:
                off = (sq - mk) / 2
                rects += _marker_rects(marker_bits(d, mid), x + off, y + off, mk, (0, 0, 0),
                                       f"aruco{mid}")
                mid += 1
    ids = sorted(card.tags)
    text = (f"{card.card_id.upper()}  ·  front: {card.tag_family} IDs {ids[0]}–{ids[-1]}  ·  "
            f"back: ChArUco {nx}×{ny}, {sq:g} mm squares, {mk:g} mm {b['dictionary']}  ·  "
            f"{w:g} × {h:g} mm")
    labels = [Label(w / 2, y0 + ny * sq + 6.5, text, 3.2, (0, 0, 0), "middle"),
              Label(w / 2, y0 - 3.5, "printed ________   measured ________   file "
                    + Path(card.path).name, 3.0, (0, 0, 0), "middle")]
    return Side(w, h, (255, 255, 255), float(spec["layout"]["corner_radius_mm"]), rects, labels)


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb)


def to_svg(side: Side, title: str) -> str:
    """Print SVG: trim + bleed + crop marks; the cut line is its own group (magenta hairline)."""
    pad = BLEED_MM + MARK_MM
    W, H = side.width + 2 * pad, side.height + 2 * pad
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:g}mm" height="{H:g}mm" '
           f'viewBox="{-pad:g} {-pad:g} {W:g} {H:g}">', f"<title>{title}</title>",
           f'<rect id="bleed" x="{-BLEED_MM:g}" y="{-BLEED_MM:g}" width="{side.width + 2 * BLEED_MM:g}" '
           f'height="{side.height + 2 * BLEED_MM:g}" fill="{_hex(side.background)}"/>', '<g id="art">']
    for r in side.rects:
        out.append(f'<rect id="{r.id}" x="{r.x:.3f}" y="{r.y:.3f}" width="{r.w:.3f}" '
                   f'height="{r.h:.3f}" fill="{_hex(r.rgb)}" shape-rendering="crispEdges"/>')
    for t in side.labels:
        out.append(f'<text x="{t.x:.2f}" y="{t.y:.2f}" font-family="Helvetica, Arial, sans-serif" '
                   f'font-size="{t.size:g}" text-anchor="{t.anchor}" fill="{_hex(t.rgb)}">{t.text}</text>')
    out.append("</g>")
    out.append(f'<g id="cut"><rect x="0" y="0" width="{side.width:g}" height="{side.height:g}" '
               f'rx="{side.corner_radius:g}" fill="none" stroke="#ff00ff" stroke-width="0.1"/></g>')
    marks = []
    for x in (0.0, side.width):
        for y in (0.0, side.height):
            sx = -1 if x == 0 else 1
            sy = -1 if y == 0 else 1
            marks.append(f"M{x + sx * (BLEED_MM + 1):g} {y:g}h{sx * (MARK_MM - 2):g}"
                         f"M{x:g} {y + sy * (BLEED_MM + 1):g}v{sy * (MARK_MM - 2):g}")
    out.append(f'<path id="crop_marks" d="{"".join(marks)}" stroke="#000" stroke-width="0.15"/>')
    out.append("</svg>")
    return "\n".join(out)


def to_raster(side: Side, px_per_mm: float = 4.0, trim_only: bool = True) -> np.ndarray:
    """RGB uint8 image of the trimmed side (no text); used for previews and the detection test."""
    s = px_per_mm
    img = np.empty((int(round(side.height * s)), int(round(side.width * s)), 3), np.uint8)
    img[:] = side.background
    for r in side.rects:
        x0, y0 = int(round(r.x * s)), int(round(r.y * s))
        x1, y1 = int(round((r.x + r.w) * s)), int(round((r.y + r.h) * s))
        img[y0:y1, x0:x1] = r.rgb
    return img


def to_pdf(side: Side, path: Path, title: str) -> None:
    """Vector PDF at true size (same geometry as the SVG). Needs matplotlib."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Rectangle

    pad = BLEED_MM + MARK_MM
    W, H = side.width + 2 * pad, side.height + 2 * pad
    fig = plt.figure(figsize=(W / 25.4, H / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(-pad, side.width + pad)
    ax.set_ylim(side.height + pad, -pad)
    ax.set_axis_off()
    col = lambda rgb: tuple(v / 255 for v in rgb)  # noqa: E731
    ax.add_patch(Rectangle((-BLEED_MM, -BLEED_MM), side.width + 2 * BLEED_MM,
                           side.height + 2 * BLEED_MM, color=col(side.background), lw=0))
    for r in side.rects:
        ax.add_patch(Rectangle((r.x, r.y), r.w, r.h, facecolor=col(r.rgb), edgecolor="none", lw=0))
    for t in side.labels:
        ax.text(t.x, t.y, t.text, fontsize=t.size / 25.4 * 72 / 0.72, ha={"start": "left"}.get(
            t.anchor, "center" if t.anchor == "middle" else "right"), va="baseline", color=col(t.rgb))
    ax.add_patch(FancyBboxPatch((0, 0), side.width, side.height,
                                boxstyle=f"round,pad=0,rounding_size={side.corner_radius}",
                                fill=False, edgecolor=(1, 0, 1), lw=0.3))
    for x in (0.0, side.width):
        for y in (0.0, side.height):
            sx = -1 if x == 0 else 1
            sy = -1 if y == 0 else 1
            ax.plot([x + sx * (BLEED_MM + 1), x + sx * (BLEED_MM + MARK_MM - 1)], [y, y], "k", lw=0.4)
            ax.plot([x, x], [y + sy * (BLEED_MM + 1), y + sy * (BLEED_MM + MARK_MM - 1)], "k", lw=0.4)
    fig.savefig(path, metadata={"Title": title})
    plt.close(fig)


def render(card_path: Path, out_dir: Path, pdf: bool = True) -> list[Path]:
    card = load_card(card_path)
    spec = load_yaml(card_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    sides = {"front": front(card, spec), "back": back(card, spec)}
    for name, side in sides.items():
        title = f"{card.card_id} {name}"
        p = out_dir / f"{card.card_id}_{name}.svg"
        p.write_text(to_svg(side, title))
        written.append(p)
        if pdf:
            q = out_dir / f"{card.card_id}_{name}.pdf"
            to_pdf(side, q, title)
            written.append(q)
    fr = to_raster(sides["front"])
    preview = np.hstack([fr, np.full((fr.shape[0], 40, 3), 255, np.uint8),
                         to_raster(sides["back"])])
    p = out_dir / f"{card.card_id}_preview.png"
    cv2.imwrite(str(p), preview[..., ::-1])
    written.append(p)
    return written


STICKER_PPI = 300


def to_trim_svg(side: Side, title: str) -> str:
    """Artwork at exactly the trim size: no bleed, crop marks or cut line (sticker upload)."""
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{side.width:g}mm" '
           f'height="{side.height:g}mm" viewBox="0 0 {side.width:g} {side.height:g}">',
           f"<title>{title}</title>",
           f'<rect x="0" y="0" width="{side.width:g}" height="{side.height:g}" '
           f'fill="{_hex(side.background)}"/>']
    for r in side.rects:
        out.append(f'<rect id="{r.id}" x="{r.x:.3f}" y="{r.y:.3f}" width="{r.w:.3f}" '
                   f'height="{r.h:.3f}" fill="{_hex(r.rgb)}" shape-rendering="crispEdges"/>')
    out.append("</svg>")
    return "\n".join(out)


def to_trim_pdf(side: Side, path: Path, title: str) -> None:
    """Vector PDF, page = trim size exactly. Needs matplotlib."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    fig = plt.figure(figsize=(side.width / 25.4, side.height / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, side.width)
    ax.set_ylim(side.height, 0)
    ax.set_axis_off()
    col = lambda rgb: tuple(v / 255 for v in rgb)  # noqa: E731
    ax.add_patch(Rectangle((0, 0), side.width, side.height, color=col(side.background), lw=0))
    for r in side.rects:
        ax.add_patch(Rectangle((r.x, r.y), r.w, r.h, facecolor=col(r.rgb), edgecolor="none", lw=0))
    fig.savefig(path, metadata={"Title": title})
    plt.close(fig)


def sticker(card_path: Path, out_dir: Path) -> list[Path]:
    """Front only, trim size, for a sticker printer (e.g. Sticker Mule): SVG, PDF, 300 ppi PNG.

    The outer 4 mm of the card is uniform surround grey, so a cut that is slightly off only
    trims grey. The printed size must be measured afterwards (physical_mm)."""
    card = load_card(card_path)
    side = front(card, load_yaml(card_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    title = f"{card.card_id} front {side.width:g} x {side.height:g} mm"
    stem = out_dir / f"{card.card_id}_sticker_{side.width:g}x{side.height:g}mm"
    svg, pdf, png = stem.with_suffix(".svg"), stem.with_suffix(".pdf"), stem.with_suffix(".png")
    svg.write_text(to_trim_svg(side, title))
    to_trim_pdf(side, pdf, title)
    img = to_raster(side, STICKER_PPI / 25.4)
    ok, buf = cv2.imencode(".png", img[..., ::-1],
                           [cv2.IMWRITE_PNG_COMPRESSION, 9])
    png.write_bytes(_png_with_dpi(buf.tobytes(), STICKER_PPI))
    return [svg, pdf, png]


def _png_with_dpi(data: bytes, ppi: int) -> bytes:
    """Insert a pHYs chunk so the PNG opens at its true physical size."""
    import struct
    import zlib

    ppm = int(round(ppi / 0.0254))
    body = b"pHYs" + struct.pack(">IIB", ppm, ppm, 1)
    chunk = struct.pack(">I", 9) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    return data[:33] + chunk + data[33:]  # after the 8-byte signature + 25-byte IHDR chunk


def template(card_path: Path, out_dir: Path) -> Path:
    """The canonical rectified card (canonical.width x height px): what patch boxes refer to.

    Pixel i covers card mm [i / px_per_mm, (i + 1) / px_per_mm), so this is the front drawn at
    px_per_mm — the V3 equivalent of V2's reference_card_template_3000x1000.png."""
    card = load_card(card_path)
    spec = load_yaml(card_path)
    img = to_raster(front(card, spec), float(spec["canonical"]["px_per_mm"]))
    assert img.shape[:2] == (card.canonical_h, card.canonical_w), img.shape
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{card.card_id}_canonical_{card.canonical_w}x{card.canonical_h}.png"
    cv2.imwrite(str(p), img[..., ::-1], [cv2.IMWRITE_PNG_COMPRESSION, 9])
    return p


# Example view: an OpenMV N6-like camera under water (1280 x 800, 933 px focal length x 1.33 flat
# port), card at 1.0 m, turned 20 deg (yaw) and 10 deg (pitch). Geometry only: no water, noise or blur.
EXAMPLE = {"width": 1280, "height": 800, "focal_px": 933.0 * 1.33, "z_m": 1.0, "yaw_deg": 20.0,
           "pitch_deg": 10.0, "background_rgb": [70, 95, 105]}


def example_view(card_path: Path, out_dir: Path) -> list[Path]:
    """A synthetic camera frame of the card + its exact ground truth (JSON), for pipeline tests.

    Ground truth: the canonical-px -> image-px homography, each tag's 4 corners (TL, TR, BR, BL
    of the black border) and centre, and each patch's centre, all in image pixels."""
    import json
    import math

    card = load_card(card_path)
    spec = load_yaml(card_path)
    e = EXAMPLE
    px = float(spec["canonical"]["px_per_mm"])
    a, b = math.radians(e["yaw_deg"]), math.radians(e["pitch_deg"])
    Ry = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(b), -math.sin(b)], [0, math.sin(b), math.cos(b)]])
    R = Ry @ Rx
    K = np.array([[e["focal_px"], 0, e["width"] / 2], [0, e["focal_px"], e["height"] / 2], [0, 0, 1]])
    # canonical px (pixel centres) -> card mm centred on the card -> camera
    to_mm = np.array([[1 / px, 0, 0.5 / px - card.canonical_w / px / 2],
                      [0, 1 / px, 0.5 / px - card.canonical_h / px / 2], [0, 0, 1]])
    H = K @ np.column_stack([R[:, 0], R[:, 1], [0, 0, e["z_m"] * 1000]]) @ to_mm
    H /= H[2, 2]
    canon = cv2.cvtColor(to_raster(front(card, spec), px), cv2.COLOR_RGB2BGR)
    scale = e["focal_px"] / (e["z_m"] * 1000) / px            # image px per canonical px (approx.)
    canon = cv2.GaussianBlur(canon, (0, 0), max(0.5 / scale * 0.5, 0.1))  # anti-alias the downscale
    bg = np.array(e["background_rgb"][::-1], np.float32)
    img = cv2.warpPerspective(canon.astype(np.float32), H, (e["width"], e["height"]),
                              flags=cv2.INTER_LINEAR, borderValue=bg.tolist())
    img = np.clip(img + 0.5, 0, 255).astype(np.uint8)

    def proj(pts):
        return cv2.perspectiveTransform(np.asarray(pts, np.float64).reshape(-1, 1, 2), H).reshape(-1, 2)

    tags = {}
    for tid, tag in sorted(card.tags.items()):
        (cx, cy), (ex, ey) = tag.center, tag.edge
        c = [[cx - ex / 2, cy - ey / 2], [cx + ex / 2, cy - ey / 2], [cx + ex / 2, cy + ey / 2],
             [cx - ex / 2, cy + ey / 2]]
        tags[str(tid)] = {"corners": np.round(proj(c), 3).tolist(),
                          "center": np.round(proj([tag.center])[0], 3).tolist()}
    patches = {p.id: np.round(proj([[p.box.x + p.box.w / 2 - 0.5, p.box.y + p.box.h / 2 - 0.5]])[0],
                              3).tolist() for p in card.patches}
    truth = {"card": card.card_id, "card_yaml": f"configs/cards/{Path(card_path).name}",
             "camera": {**e, "note": "pinhole, no distortion; geometry only (no water, noise, blur)"},
             "homography_canonical_to_image": np.round(H, 9).tolist(),
             "tags": tags, "patch_centers": patches}
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"{card.card_id}_example_{e['width']}x{e['height']}"
    cv2.imwrite(str(stem.with_suffix(".png")), img, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    stem.with_suffix(".json").write_text(json.dumps(truth, indent=1) + "\n")
    return [stem.with_suffix(".png"), stem.with_suffix(".json")]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cards", nargs="+", type=Path, help="card YAML file(s)")
    ap.add_argument("--out", type=Path, default=Path("results/cards"))
    ap.add_argument("--no-pdf", action="store_true", help="skip the matplotlib PDF")
    ap.add_argument("--sticker", action="store_true",
                    help="front only at trim size (no bleed / marks / cut line) + 300 ppi PNG")
    ap.add_argument("--reference", action="store_true",
                    help="canonical rectified template PNG + a synthetic example frame with ground truth")
    a = ap.parse_args(argv)
    for c in a.cards:
        if a.reference:
            written = [template(c, a.out), *example_view(c, a.out)]
        elif a.sticker:
            written = sticker(c, a.out)
        else:
            written = render(c, a.out, pdf=not a.no_pdf)
        for p in written:
            print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
