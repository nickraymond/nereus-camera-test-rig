#!/usr/bin/env python3
"""w1_print_targets.py — pool test 2 W1 board print sheet (spec rev 12 must do 16), US Letter, 1:1.

Page 1: four urchin photos, 2 at 80 mm and 2 at 50 mm (spine tip to spine tip = the detector box's longer side), each
        a square photo tile inside a thin cut box.
Page 2: 80 mm and 50 mm black-and-grey quartered disks, each inside a thin square cut box with white space between
        the disk and the box (cut a straight square, not a circle), and the slanted-edge target: a 50 mm black
        square rotated 5° on an 80 mm mid-grey square (board_layout.template.yaml: side 50, angle 5, grey margin
        >= 10 mm), with a thin cut box on the grey square's edge.
Every page: a 100 mm scale bar and "print at Actual size / 100 %". Rendered with Pillow at 300 dpi; the PDF's page is
exactly 8.5 x 11 in, so printing at 100 % (no fit-to-page) gives 1:1 sizes.

Urchin photos are crops of Reef Check stills from the urchin pilot (E6), picked from Nick's RF-DETR detections
(compare_rfdetr/rfdetr_dets.json). The source stills, boxes and the PDF's sha256 go in OUT/MANIFEST.md.

Usage:
  python scripts/w1_print_targets.py OUT_DIR --urchin NAME=STILL_PATH:x1,y1,x2,y2:MM ... [--dpi 300]
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LETTER_IN = (8.5, 11.0)
GREY = (128, 128, 128)        # mid-grey: no white inside the disks, so nothing clips (Nick, 10/18)
BLACK = (0, 0, 0)
CUT_GAP_MM = 10.0             # white space between a disk and its cut box
TILE_PAD = 1.15               # urchin tile side = diameter x this (photo background around the spines)
LINE_MM = 0.25                # thin cut lines


def mm(v: float, dpi: int) -> int:
    return round(v / 25.4 * dpi)


def font(size_px: int):
    for name in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf",
                 "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size_px)
        except OSError:
            continue
    return ImageFont.load_default()


def page(dpi: int) -> Image.Image:
    return Image.new("RGB", (round(LETTER_IN[0] * dpi), round(LETTER_IN[1] * dpi)), "white")


def cut_box(d: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int, dpi: int) -> None:
    d.rectangle((x0, y0, x1, y1), outline=BLACK, width=max(1, mm(LINE_MM, dpi)))


def footer(im: Image.Image, title: str, dpi: int) -> None:
    d = ImageDraw.Draw(im)
    f = font(mm(3.2, dpi))
    x0, y = mm(15, dpi), im.height - mm(22, dpi)
    d.rectangle((x0, y, x0 + mm(100, dpi), y + mm(4, dpi)), fill=BLACK)
    for k in range(11):  # 10 mm ticks
        xt = x0 + mm(10 * k, dpi)
        d.line((xt, y - mm(2, dpi), xt, y + mm(4, dpi)), fill=BLACK, width=max(1, mm(0.3, dpi)))
    d.text((x0, y + mm(6, dpi)), "100 mm scale bar: measure it. Print at Actual size / 100 % (no fit-to-page), "
           "US Letter.", fill=BLACK, font=f)
    d.text((mm(15, dpi), mm(10, dpi)), title, fill=BLACK, font=font(mm(4.5, dpi)))


def urchin_tile(path: Path, box: tuple[float, float, float, float], diam_mm: float, dpi: int) -> Image.Image:
    """A square crop centred on the box, scaled so the box's longer side prints as diam_mm."""
    src = Image.open(path).convert("RGB")
    x1, y1, x2, y2 = box
    diam_px = max(x2 - x1, y2 - y1)
    side_src = diam_px * TILE_PAD
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    crop = src.crop((round(cx - side_src / 2), round(cy - side_src / 2),
                     round(cx + side_src / 2), round(cy + side_src / 2)))  # out-of-frame = black
    side = mm(diam_mm * TILE_PAD, dpi)
    return crop.resize((side, side), Image.LANCZOS)


def page_urchins(urchins: list[dict], dpi: int) -> Image.Image:
    im = page(dpi)
    d = ImageDraw.Draw(im)
    f = font(mm(3.0, dpi))
    # 2 x 2 grid: 80 mm tiles on the top row, 50 mm on the bottom row
    slots = {"u80a": (58, 72), "u80b": (158, 72), "u50a": (58, 168), "u50b": (158, 168)}  # tile centres, mm
    for u in urchins:
        tile = urchin_tile(Path(u["path"]), u["box"], u["mm"], dpi)
        cxm, cym = slots[u["name"]]
        x0, y0 = mm(cxm, dpi) - tile.width // 2, mm(cym, dpi) - tile.height // 2
        im.paste(tile, (x0, y0))
        g = mm(3, dpi)
        cut_box(d, x0 - g, y0 - g, x0 + tile.width + g, y0 + tile.height + g, dpi)
        d.text((x0 - g, y0 + tile.height + g + mm(1.5, dpi)),
               f"{u['name']}: {u['mm']:.0f} mm spine tip to tip", fill=BLACK, font=f)
    footer(im, "W1 urchin samples (Reef Check stills; cut on the thin box)", dpi)
    return im


def quartered_disk(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
    bb = (cx - r, cy - r, cx + r, cy + r)
    for k, fill in enumerate((BLACK, GREY, BLACK, GREY)):
        d.pieslice(bb, start=90 * k, end=90 * (k + 1), fill=fill)


def page_targets(dpi: int) -> Image.Image:
    im = page(dpi)
    d = ImageDraw.Draw(im)
    f = font(mm(3.0, dpi))
    for dia, (cxm, cym) in ((80, (60, 85)), (50, (160, 85))):
        cx, cy, r = mm(cxm, dpi), mm(cym, dpi), mm(dia / 2, dpi)
        quartered_disk(d, cx, cy, r)
        h = r + mm(CUT_GAP_MM, dpi)
        cut_box(d, cx - h, cy - h, cx + h, cy + h, dpi)
        d.text((cx - h, cy + h + mm(1.5, dpi)), f"disk {dia} mm (black / grey), cut box {dia + 2 * CUT_GAP_MM:.0f} mm",
               fill=BLACK, font=f)
    # slanted edge: 80 mm grey square, 50 mm black square rotated 5 degrees, centred
    gs, bs, ang = 80.0, 50.0, 5.0
    cx, cy = mm(108, dpi), mm(190, dpi)
    hg = mm(gs / 2, dpi)
    d.rectangle((cx - hg, cy - hg, cx + hg, cy + hg), fill=GREY)
    hb = bs / 2
    pts = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        a = math.radians(ang)
        x, y = sx * hb, sy * hb
        pts.append((cx + mm(x * math.cos(a) - y * math.sin(a), dpi), cy + mm(x * math.sin(a) + y * math.cos(a), dpi)))
    d.polygon(pts, fill=BLACK)
    cut_box(d, cx - hg, cy - hg, cx + hg, cy + hg, dpi)
    d.text((cx - hg, cy + hg + mm(1.5, dpi)), f"slanted edge: {bs:.0f} mm black square at {ang:.0f}° on an {gs:.0f} mm "
           "grey square; cut on the grey square's edge", fill=BLACK, font=f)
    footer(im, "W1 reference disks + slanted-edge square (cut on the thin boxes)", dpi)
    return im


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--urchin", action="append", required=True,
                    help="NAME=STILL_PATH:x1,y1,x2,y2:MM with NAME in u80a u80b u50a u50b")
    ap.add_argument("--dpi", type=int, default=300)
    a = ap.parse_args(argv)
    urchins = []
    for spec in a.urchin:
        name, _, rest = spec.partition("=")
        path, box, size = rest.rsplit(":", 2)
        urchins.append({"name": name, "path": path, "box": tuple(float(v) for v in box.split(",")),
                        "mm": float(size)})
    if sorted(u["name"] for u in urchins) != ["u50a", "u50b", "u80a", "u80b"]:
        print("need exactly u80a, u80b, u50a, u50b", file=sys.stderr)
        return 2
    a.out_dir.mkdir(parents=True, exist_ok=True)
    pages = [page_urchins(urchins, a.dpi), page_targets(a.dpi)]
    pdf = a.out_dir / "w1_print_targets_letter.pdf"
    pages[0].save(pdf, "PDF", resolution=a.dpi, save_all=True, append_images=pages[1:])
    for k, p in enumerate(pages, 1):
        p.resize((p.width // 4, p.height // 4)).save(a.out_dir / f"preview_page{k}.png")
    lines = ["# W1 print targets (scripts/w1_print_targets.py)", "",
             f"- PDF: {pdf.name}, sha256 {sha256(pdf)}, US Letter, {a.dpi} dpi, print at Actual size / 100 %.",
             "- Urchin sources (Reef Check stills, urchin pilot; box = RF-DETR detection, longer side = the size):"]
    for u in urchins:
        lines.append(f"  - {u['name']} {u['mm']:.0f} mm: {u['path']} box {list(u['box'])} sha256 "
                     f"{sha256(Path(u['path']))[:16]}")
    (a.out_dir / "MANIFEST.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
