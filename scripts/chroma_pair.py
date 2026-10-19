#!/usr/bin/env python3
"""chroma_pair.py — one board's frame from two clips of one fixed scene, the same box cropped.

Purpose: pool test 2 prep B0 (docs/pool2_20261021/TEST_CARD_B.md §4 step 2; spec rev 6 + d56):
  the AE3 in JPEG 4:4:4 vs 4:2:0 on the same bench scene, back to back. Takes the frame nearest
  t s after each clip's fire (capture clock: board ticks tied to the Pi clock by the fire
  receipt), keeps both camera JPEGs byte for byte, and crops the SAME pixel box from each at
  --zoom x (nearest neighbour, so no smoothing hides the chroma), plus a side-by-side pair.
  Equal footprint: both frames must have the same size (same board, same framesize); different
  sizes are refused, because the PAG7936's per-framesize field of view isn't verified.
  --zoom 3 gives the card's 3x crop pair; --zoom 1 gives Nick's 100 % crops (d56).

Usage (Mac, needs ffmpeg on PATH):
  python scripts/chroma_pair.py <clip_a> <clip_b> --camera openmv_ae3 --t 15 \
      --crop X,Y,W,H [--zoom 3] --out DIR
Outputs (DIR): <clip>_<cam>_seqNNNN.jpg (camera bytes, unchanged), <clip>_<cam>_crop_xZ.png per
  clip, pair_<a>_<b>_xZ.png (a left, b right), and one row per clip in chroma_pair.csv (clip,
  frame, capture time, measured size + chroma format from the JPEG header, crop box, zoom).
Exit 0 = both crops written; 1 = refused (sizes differ, box outside the frame, no frame).
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _table():
    spec = importlib.util.spec_from_file_location("video_clip_table",
                                                  ROOT / "scripts" / "video_clip_table.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def frames(clip: Path, cam: str) -> list[dict]:
    """Every frame of <cam> in one clip: seq, byte offset/size in <cam>.mjpeg, and capture time
    in s after that camera's fire (fire_host_mono + (ticks - fire_rx_ms) / 1000)."""
    rec = json.loads((clip / "video_sync.json").read_text())["cameras"][cam]
    bs = rec.get("board_sync") or (rec.get("board_done") or {}).get("sync") or {}
    fire_rx = bs.get("fire_rx_ms")
    if rec.get("fire_host_mono") is None or fire_rx is None:
        return []
    out, off = [], 0
    for r in csv.DictReader((clip / f"{cam}_frames.csv").open()):
        n = int(r["bytes"])
        out.append({"seq": int(r["seq"]), "offset": off, "bytes": n,
                    "t_s": (int(r["ticks_ms"]) - fire_rx) / 1000})
        off += n
    return out


def nearest(fr: list[dict], t_s: float) -> dict | None:
    return min(fr, key=lambda f: abs(f["t_s"] - t_s)) if fr else None


def box_ok(box: tuple[int, int, int, int], w: int, h: int) -> bool:
    x, y, bw, bh = box
    return bw > 0 and bh > 0 and x >= 0 and y >= 0 and x + bw <= w and y + bh <= h


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("clip_a", type=Path)
    ap.add_argument("clip_b", type=Path)
    ap.add_argument("--camera", default="openmv_ae3")
    ap.add_argument("--t", type=float, default=15.0, help="s after the fire")
    ap.add_argument("--crop", required=True, help="X,Y,W,H in pixels of the frame")
    ap.add_argument("--zoom", type=int, default=3)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    box = tuple(int(v) for v in a.crop.split(","))
    if len(box) != 4:
        print("--crop needs X,Y,W,H")
        return 1
    tab = _table()
    a.out.mkdir(parents=True, exist_ok=True)
    picks = []
    # Output names carry "<setting>_<clip>" (e.g. ctl_1_clip01): two settings' clips are both
    # called clipNN, and a bare clip name let the second crop overwrite the first (B0, 10/18).
    names = [f"{c.parent.name}_{c.name}" for c in (a.clip_a, a.clip_b)]
    if names[0] == names[1]:
        print(f"refused: both clips are {names[0]}")
        return 1
    for clip, name in zip((a.clip_a, a.clip_b), names):
        f = nearest(frames(clip, a.camera), a.t)
        if f is None:
            print(f"{clip.name}: no {a.camera} frame with a capture time")
            return 1
        with (clip / f"{a.camera}.mjpeg").open("rb") as raw:
            raw.seek(f["offset"])
            data = raw.read(f["bytes"])
        sof = tab.jpeg_sof(data[:4096])
        if sof is None or data[-2:] != b"\xff\xd9":
            print(f"{clip.name}: frame {f['seq']} is not a whole JPEG")
            return 1
        jpg = a.out / f"{name}_{a.camera}_seq{f['seq']:04d}.jpg"
        jpg.write_bytes(data)
        picks.append({"clip": name, "frame": f["seq"], "t_s": round(f["t_s"], 3),
                      "width": sof["width"], "height": sof["height"],
                      "subsampling_measured": sof["subsampling"], "jpg": jpg})
    if (picks[0]["width"], picks[0]["height"]) != (picks[1]["width"], picks[1]["height"]):
        print(f"refused: frame sizes differ {[(p['width'], p['height']) for p in picks]}; "
              "equal footprint needs the same framesize")
        return 1
    if not box_ok(box, picks[0]["width"], picks[0]["height"]):
        print(f"refused: crop {box} is outside the {picks[0]['width']}x{picks[0]['height']} frame")
        return 1
    x, y, w, h = box
    vf = f"crop={w}:{h}:{x}:{y},scale=iw*{a.zoom}:ih*{a.zoom}:flags=neighbor"
    crops = []
    for p in picks:
        crop = a.out / f"{p['clip']}_{a.camera}_crop_x{a.zoom}.png"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(p["jpg"]), "-vf", vf,
                        str(crop)], check=True)
        crops.append(crop)
    pair = a.out / f"pair_{picks[0]['clip']}_{picks[1]['clip']}_x{a.zoom}.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(crops[0]), "-i",
                    str(crops[1]), "-filter_complex", "hstack=inputs=2", str(pair)], check=True)
    log = a.out / "chroma_pair.csv"
    new = not log.exists()
    with log.open("a", newline="") as f:
        w_ = csv.DictWriter(f, fieldnames=["clip", "camera", "frame", "t_s", "width", "height",
                                           "subsampling_measured", "crop_xywh", "zoom", "jpg",
                                           "crop", "pair"])
        if new:
            w_.writeheader()
        for p, crop in zip(picks, crops):
            w_.writerow({"clip": p["clip"], "camera": a.camera, "frame": p["frame"],
                         "t_s": p["t_s"], "width": p["width"], "height": p["height"],
                         "subsampling_measured": p["subsampling_measured"],
                         "crop_xywh": a.crop, "zoom": a.zoom, "jpg": p["jpg"].name,
                         "crop": crop.name, "pair": pair.name})
    print(f"{pair.name}: {picks[0]['clip']} ({picks[0]['subsampling_measured']}) | "
          f"{picks[1]['clip']} ({picks[1]['subsampling_measured']}) crop {box} x{a.zoom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
