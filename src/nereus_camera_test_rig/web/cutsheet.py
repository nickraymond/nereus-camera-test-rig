"""Cut-sheet export — one shareable PNG/PDF per experiment (Phase 6).

Composites the side-by-side comparison (annotated stills, card crops, metric
table) into a single high-resolution image for sharing outside the rig UI.
Pillow-based, adapted from the proven pattern in
``bm_cam_legacy/tools/make_crop_q20_cut_sheet.py`` (font loading, banner +
grid compositing); PDF output is the same render saved via Pillow's PDF writer.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont

# Layout constants (pixels). Width chosen so a 3-camera sheet prints crisply.
SHEET_W = 2400
MARGIN = 60
COL_GAP = 30
HEADER_H = 170
LABEL_H = 44
ROW_GAP = 26
TABLE_LABEL_W = 520
TABLE_ROW_H = 42
FOOTER_H = 70

BRAND_LEFT = (31, 79, 138)  # #1f4f8a
BRAND_RIGHT = (47, 140, 188)  # #2f8cbc
INK = (36, 51, 65)  # #243341
MUTED = (97, 113, 129)  # #617181
BORDER = (216, 227, 236)  # #d8e3ec
BEST_BG = (232, 241, 251)  # #e8f1fb
PAGE_BG = (255, 255, 255)
PANEL_BG = (247, 250, 252)  # #f7fafc


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """System-font lookup with graceful fallback (same approach as prior art)."""
    names = (
        ["Arial Bold.ttf", "Arial-Bold.ttf", "DejaVuSans-Bold.ttf"]
        if bold
        else ["Arial.ttf", "DejaVuSans.ttf"]
    )
    roots = [
        Path("/System/Library/Fonts/Supplemental"),
        Path("/Library/Fonts"),
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype/liberation"),
    ]
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.exists():
                return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _gradient(width: int, height: int) -> Image.Image:
    """Horizontal brand gradient, matching the dashboard header."""
    strip = Image.new("RGB", (width, 1))
    for x in range(width):
        t = x / max(1, width - 1)
        strip.putpixel(
            (x, 0),
            tuple(round(a + (b - a) * t) for a, b in zip(BRAND_LEFT, BRAND_RIGHT)),
        )
    return strip.resize((width, height))


def _paste_fitted(
    sheet: Image.Image,
    draw: ImageDraw.ImageDraw,
    path: Optional[Path],
    x: int,
    y: int,
    width: int,
    placeholder: str,
    max_h: Optional[int] = None,
) -> int:
    """Paste an image scaled to ``width`` at (x, y); returns the drawn height."""
    if path is not None and path.is_file():
        try:
            img = Image.open(path).convert("RGB")
        except OSError:
            img = None
        if img is not None:
            h = round(img.height * width / img.width)
            if max_h and h > max_h:
                h = max_h
                w = round(img.width * h / img.height)
                img = img.resize((w, h))
                sheet.paste(img, (x + (width - w) // 2, y))
            else:
                sheet.paste(img.resize((width, h)), (x, y))
            draw.rectangle([x, y, x + width, y + h], outline=BORDER, width=2)
            return h
    h = max_h or round(width * 2 / 3)
    draw.rectangle([x, y, x + width, y + h], fill=PANEL_BG, outline=BORDER, width=2)
    font = _load_font(26)
    tw = draw.textlength(placeholder, font=font)
    draw.text((x + (width - tw) / 2, y + h / 2 - 14), placeholder, fill=MUTED, font=font)
    return h


def render_cutsheet(view: Any, comparison: list[dict[str, Any]], fmt: str = "png") -> bytes:
    """Render one experiment as a cut sheet; returns PNG or PDF bytes.

    ``view`` is a ``results_reader.ExperimentView``; ``comparison`` is the same
    row structure the detail page renders, so the sheet and the page never
    disagree.
    """
    cameras = view.cameras
    ncols = max(1, len(cameras))
    col_w = (SHEET_W - 2 * MARGIN - (ncols - 1) * COL_GAP) // ncols
    xs = [MARGIN + i * (col_w + COL_GAP) for i in range(ncols)]

    f_title = _load_font(44, bold=True)
    f_sub = _load_font(26)
    f_col = _load_font(30, bold=True)
    f_cell = _load_font(24)
    f_small = _load_font(20)

    # Probe pass: measure image heights so rows align across cameras.
    ann_h = 10
    crop_h = 10
    for cam in cameras:
        for rel, cap in ((cam.annotated_rel or cam.image_rel, "ann"), (cam.crop_rel, "crop")):
            if not rel:
                continue
            path = view.root / rel
            if not path.is_file():
                continue
            try:
                with Image.open(path) as img:
                    h = round(img.height * col_w / img.width)
            except OSError:
                continue
            if cap == "ann":
                ann_h = min(max(ann_h, h), round(col_w * 0.75))
            else:
                crop_h = min(max(crop_h, h), round(col_w * 0.75))

    table_rows = 1 + len(comparison)  # detection row + metric rows
    sheet_h = (
        HEADER_H + ROW_GAP
        + LABEL_H + ann_h + ROW_GAP
        + LABEL_H + crop_h + ROW_GAP
        + LABEL_H + table_rows * TABLE_ROW_H
        + FOOTER_H + MARGIN
    )

    sheet = Image.new("RGB", (SHEET_W, sheet_h), PAGE_BG)
    draw = ImageDraw.Draw(sheet)

    # Header band.
    sheet.paste(_gradient(SHEET_W, HEADER_H), (0, 0))
    draw.text((MARGIN, 38), "Nereus Vision — Camera Comparison", fill="white", font=f_title)
    record = view.record or {}
    sub = "  ·  ".join(
        s for s in (
            view.experiment_id,
            record.get("timestamp", ""),
            record.get("environment_label", ""),
            f"status: {view.status}",
        ) if s
    )
    draw.text((MARGIN, 102), sub, fill=(226, 238, 248), font=f_sub)

    y = HEADER_H + ROW_GAP

    # Column titles.
    from .app import camera_label  # avoid a circular import at module load

    for x, cam in zip(xs, cameras):
        draw.text((x, y), camera_label(cam.name), fill=INK, font=f_col)
        ident = (cam.capture or {}).get("camera") or {}
        sub = f"fw {ident['firmware']}" if ident.get("firmware") else (ident.get("sensor") or "")
        if sub:
            tw = draw.textlength(sub, font=f_small)
            draw.text((x + col_w - tw, y + 8), sub, fill=MUTED, font=f_small)
    y += LABEL_H

    # Annotated row.
    for x, cam in zip(xs, cameras):
        rel = cam.annotated_rel or cam.image_rel
        err = (cam.error or {}).get("code", "no capture")
        _paste_fitted(
            sheet, draw, view.root / rel if rel else None, x, y, col_w,
            placeholder=err, max_h=ann_h,
        )
    y += ann_h + ROW_GAP
    draw.text((MARGIN, y), "RECTIFIED CARD CROP", fill=MUTED, font=f_small)
    y += LABEL_H - 8

    # Crop row.
    for x, cam in zip(xs, cameras):
        _paste_fitted(
            sheet, draw, view.root / cam.crop_rel if cam.crop_rel else None, x, y, col_w,
            placeholder="no card detected", max_h=crop_h,
        )
    y += crop_h + ROW_GAP
    draw.text((MARGIN, y), "QUANTITATIVE COMPARISON", fill=MUTED, font=f_small)
    y += LABEL_H - 8

    # Metrics table: label gutter + one column per camera.
    col_val_w = (SHEET_W - 2 * MARGIN - TABLE_LABEL_W) // ncols

    def cell_x(i: int) -> int:
        return MARGIN + TABLE_LABEL_W + i * col_val_w

    rows: list[tuple[str, list[Any], set[int]]] = [(
        "Card detection",
        [
            "n/a" if cam.analysis_pass is None else ("pass" if cam.analysis_pass else "fail")
            for cam in cameras
        ],
        set(),
    )]
    rows += [(r["label"], r["values"], r["best"]) for r in comparison]

    for ridx, (label, values, best) in enumerate(rows):
        ry = y + ridx * TABLE_ROW_H
        if ridx % 2 == 0:
            draw.rectangle([MARGIN, ry, SHEET_W - MARGIN, ry + TABLE_ROW_H], fill=PANEL_BG)
        for i, value in enumerate(values):
            if i in best:
                draw.rectangle(
                    [cell_x(i), ry, cell_x(i) + col_val_w, ry + TABLE_ROW_H], fill=BEST_BG
                )
        draw.line([MARGIN, ry, SHEET_W - MARGIN, ry], fill=BORDER, width=1)
        draw.text((MARGIN + 10, ry + 9), str(label), fill=MUTED, font=f_cell)
        for i, value in enumerate(values):
            text = "—" if value is None else str(value)
            tw = draw.textlength(text, font=f_cell)
            draw.text((cell_x(i) + col_val_w - tw - 16, ry + 9), text, fill=INK, font=f_cell)
    y += len(rows) * TABLE_ROW_H
    draw.line([MARGIN, y, SHEET_W - MARGIN, y], fill=BORDER, width=1)

    # Footer.
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    draw.text(
        (MARGIN, y + 22),
        f"Generated {stamp} · nereus-camera-test-rig · left/expected vs right/measured "
        f"color detail available in the web UI",
        fill=MUTED,
        font=f_small,
    )

    buf = io.BytesIO()
    if fmt == "pdf":
        sheet.save(buf, format="PDF", resolution=150.0)
    else:
        sheet.save(buf, format="PNG")
    return buf.getvalue()
