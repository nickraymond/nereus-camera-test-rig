"""Side-by-side human check sheet (PREREG v2 stage 4): what the viewer sees, per candidate (Mac).

    python -m compression_study.video_100k.sheet --run <RUN_ROOT> --data pi --clip bmcam003_round3 \
        --cands pi_svt_p6 pi_svt_p8_lowmem pi_x264_vs --out <RUN_ROOT>/decision/sheet_c3r3.png

Rows: source, then each candidate (as displayed: decoded, frame hold / upscale undone exactly as
scored). Columns: the full frame (half size) and the card box at 2×. Frames: the first
candidate's lowest-VMAF frame and the clip's middle frame (window frame 25). Labels carry the
candidate, bytes and that frame's VMAF.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from .candidates import n_frames, registry
from .common import NFR, card_box, q, read_jsonl, sh, src_path
from .score import display_chain


def display_file(c, rec: dict, data: Path, clip: str, tmp: Path) -> Path:
    """Lossless 50-frame file of what the viewer sees (as scored)."""
    f = data / rec["file"]
    if c.display_file is not None:
        disp, _ = c.display_file(f, clip, tmp / c.name)
        return disp
    out = tmp / f"{c.name}.mkv"
    sh(
        f"ffmpeg -hide_banner -v error -y {c.dec} -i {q(f)} "
        f'-vf "{display_chain(c.k, c.wh, c.display, NFR)}" -frames:v {NFR} -c:v ffv1 {q(out)}'
    )
    if n_frames(c) and not out.exists():
        raise RuntimeError(f"{c.name}: no display file")
    return out


def grab(src: Path, n: int, box: tuple[int, int, int, int]) -> tuple[Image.Image, Image.Image]:
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "f.png"
        sh(
            f"ffmpeg -hide_banner -v error -y -i {q(src)} -vf \"select='eq(n\\,{n})',format=rgb24\" "
            f"-frames:v 1 -update 1 {q(png)}"
        )
        im = Image.open(png).convert("RGB")
        im.load()
    x, y, w, h = box
    full = im.resize((im.width // 2, im.height // 2), Image.LANCZOS)
    card = im.crop((x, y, x + w, y + h)).resize((w * 2, h * 2), Image.NEAREST)
    return full, card


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--data", default="pi", help="stage folder under --run (pi | stage1)")
    ap.add_argument("--clip", required=True)
    ap.add_argument("--cands", nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    reg = registry()
    data = a.run / a.data
    recs = {(r["cand"], r["clip"]): r for r in read_jsonl(data / "results.jsonl") if r.get("status") == "ok"}
    missing = [c for c in a.cands if (c, a.clip) not in recs]
    if missing:
        raise SystemExit(f"no ok result for {missing} on {a.clip} in {data}")
    first = recs[(a.cands[0], a.clip)]["vmaf_per_frame"]
    frames = [min(range(len(first)), key=first.__getitem__), NFR // 2]
    box = card_box(a.clip)
    tmp = Path(tempfile.mkdtemp(prefix="sheet_"))
    try:
        rows = [("source (5 s window, lossless)", src_path(a.clip), None)]
        for name in a.cands:
            r = recs[(name, a.clip)]
            rows.append((f"{name}  {r['size_B']:,} B", display_file(reg[name], r, data, a.clip, tmp), r))
        tiles = []
        for label, path, r in rows:
            line = []
            for n in frames:
                full, card = grab(path, n, box)
                v = f"  frame {n}: VMAF {r['vmaf_per_frame'][n]}" if r else f"  frame {n}"
                line.append((label + v, full, card))
            tiles.append(line)
        fw, fh = tiles[0][0][1].size
        cw, ch = tiles[0][0][2].size
        pad, lab = 8, 22
        W = len(frames) * (fw + cw + 3 * pad)
        H = len(tiles) * (fh + lab + pad) + pad
        sheet = Image.new("RGB", (W, H), (24, 24, 24))
        d = ImageDraw.Draw(sheet)
        for i, line in enumerate(tiles):
            y = pad + i * (fh + lab + pad)
            for j, (text, full, card) in enumerate(line):
                x = j * (fw + cw + 3 * pad) + pad
                d.text((x, y), text, fill=(235, 235, 235))
                sheet.paste(full, (x, y + lab))
                sheet.paste(card, (x + fw + pad, y + lab))
        a.out.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(a.out)
        print(f"wrote {a.out} ({sheet.width}x{sheet.height}), frames {frames}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
