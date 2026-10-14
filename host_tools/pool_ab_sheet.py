"""Host tool: A/B cut sheet — our RAW render (A) next to each camera's own out-of-the-box image (B).

Purpose
    Nick (2026-10-14): judge "how good are the OpenMV settings" without our processing getting
    in the way. For one capture set, per camera, A and B are placed side by side at NATIVE
    resolution (no resampling for the OpenMV boards) in one lossless PNG he can keep.

    A = our pipeline: OpenMV 8-bit Bayer RAW (metered, then exposure/gain/WB locked) rendered on
        the Mac by pool_render (bilinear demosaic, gray-world AWB, sRGB); IMX708 DNG rendered by
        pool_render (camera WB + the DNG's colour matrix, sRGB).
    B = the camera's own image: OpenMV board JPEG after sensor.reset() (auto exposure / gain /
        white balance, 2 s settle; q100 N6 / q98 AE3 from 2026-10-14), IMX708 rpicam JPEG.

Inputs
    --render DIR   a pool_render output folder for the run (run it with --all so every set has its
                   renders): <DIR>/<run>/<set>/ holds the per-set renders and board JPEG copies.
    --set NAME     the set folder name (default: the first set that has all three cameras)

Output
    <DIR>/ab_<set>.png   lossless PNG. Rows: N6 (A | B at 1:1), AE3 (A | B at 1:1), IMX708 (A | B,
                         each scaled to the OpenMV row width, labelled with the scale).

Example
    .venv/bin/python -m host_tools.pool_ab_sheet --render ~/NereusPool/<date>/nereus002/<run>_render

Limitations
    - A's colour depends on our Mac render (gray-world AWB for OpenMV); B is the board's own ISP.
      Differences between A and B are pipeline differences, not hardware.
    - The IMX708 row is downscaled (4608 px wide sources); the PNG states the scale.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

ROW_W = 2560          # two 1280-wide OpenMV frames side by side at 1:1
LABEL_H = 64


def _font(size: int):
    for path in ("/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _first(folder: Path, patterns: list[str]) -> Optional[Path]:
    for pat in patterns:
        hits = sorted(p for p in folder.glob(pat) if "_captioned" not in p.name)
        if hits:
            return hits[0]
    return None


def source_set(set_dir: Path) -> Optional[Path]:
    """The pulled run's set folder for a render set folder (render_manifest.json ``runs``)."""
    import json
    try:
        man = json.loads((set_dir.parent.parent / "render_manifest.json").read_text())
    except (OSError, ValueError):
        return None
    for run in man.get("runs") or []:
        hits = list(Path(run).glob(f"*/{set_dir.name}"))
        if hits:
            return hits[0]
    return None


def find_pair(set_dir: Path, camera: str) -> tuple[Optional[Path], Optional[Path]]:
    """(A, B) image paths for one camera in a pool_render per-set folder."""
    if camera == "imx708":
        a = _first(set_dir, ["imx708_raw_*_mac_srgb_8bit.png"])
        src = source_set(set_dir)  # the rpicam JPEG is an original: read it from the pulled run
        b = _first(src / "captures" / "imx708", ["imx708_raw_*.jpg"]) if src else None
        return a, b
    a = _first(set_dir, [f"{camera}_raw_*_mac*.png", f"{camera}_raw_*.png"])
    b = _first(set_dir, [f"{camera}_image_*_board_isp_render.jpg", f"{camera}_image_*.jpg"])
    return a, b


def pick_set(run_render: Path, want: Optional[str]) -> Path:
    sets = sorted(p for p in run_render.iterdir() if p.is_dir())
    if want:
        hit = [s for s in sets if s.name == want]
        if not hit:
            raise SystemExit(f"set {want!r} not found under {run_render}")
        return hit[0]
    for s in sets:
        if all(all(find_pair(s, c)) for c in ("openmv_n6", "openmv_ae3")):
            return s
    raise SystemExit(f"no set under {run_render} has A and B for both OpenMV cameras "
                     "(run pool_render with --all and pull the sets)")


def row(label: str, a: Optional[Path], b: Optional[Path], scale_to: Optional[int]) -> Image.Image:
    """One labelled row: A | B. ``scale_to`` = width per half (None = native, no resampling)."""
    tiles, notes = [], []
    for tag, path in (("A  our RAW render", a), ("B  camera's own image", b)):
        if path is None:
            img = Image.new("RGB", (1280, 800), (60, 60, 60))
            notes.append(f"{tag}: MISSING")
        else:
            img = Image.open(path).convert("RGB")
            native = f"{img.width}x{img.height}"
            if scale_to and img.width != scale_to:
                s = scale_to / img.width
                img = img.resize((scale_to, round(img.height * s)), Image.LANCZOS)
                notes.append(f"{tag}: {path.name} ({native}, shown scaled to {s * 100:.1f} %)")
            else:
                notes.append(f"{tag}: {path.name} ({native}, 1:1 native pixels)")
        tiles.append(img)
    h = max(t.height for t in tiles)
    out = Image.new("RGB", (sum(t.width for t in tiles), h + LABEL_H), "white")
    d = ImageDraw.Draw(out)
    d.text((10, 6), label, fill="black", font=_font(30))
    d.text((10, 38), "   |   ".join(notes), fill=(60, 60, 60), font=_font(18))
    x = 0
    for t in tiles:
        out.paste(t, (x, LABEL_H))
        x += t.width
    return out


def build(run_render: Path, set_name: Optional[str]) -> Path:
    set_dir = pick_set(run_render, set_name)
    rows = [row("OpenMV N6 — A: Mac render of 8-bit Bayer RAW (gray-world AWB) | B: board JPEG, "
                "out of the box (auto exposure/gain/WB)", *find_pair(set_dir, "openmv_n6"), None),
            row("OpenMV AE3 — A: Mac render of 8-bit Bayer RAW (gray-world AWB) | B: board JPEG, "
                "out of the box (auto exposure/gain/WB)", *find_pair(set_dir, "openmv_ae3"), None),
            row("IMX708 — A: Mac render of the DNG | B: rpicam JPEG (libcamera auto)",
                *find_pair(set_dir, "imx708"), ROW_W // 2)]
    title_h = 70
    W = max(r.width for r in rows)
    sheet = Image.new("RGB", (W, title_h + sum(r.height + 20 for r in rows)), "white")
    d = ImageDraw.Draw(sheet)
    d.text((10, 14), f"A/B: our RAW pipeline vs the camera's own image — set {set_dir.name} "
                     f"(run {set_dir.parent.name})", fill="black", font=_font(36))
    y = title_h
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height + 20
    out = run_render.parent / f"ab_{set_dir.name}.png"
    sheet.save(out, optimize=False)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--render", type=Path, required=True, help="pool_render output folder")
    ap.add_argument("--set", default=None, help="set folder name (default: first complete set)")
    args = ap.parse_args(argv)
    runs = [p for p in args.render.iterdir() if p.is_dir() and p.name not in ("sheet", "spotter")]
    if not runs:
        print(f"[pool_ab_sheet] no run folder in {args.render}", file=sys.stderr)
        return 2
    out = build(runs[0], args.set)
    print(f"[pool_ab_sheet] wrote {out} ({Image.open(out).size[0]}x{Image.open(out).size[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
