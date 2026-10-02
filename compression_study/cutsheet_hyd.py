"""Before/after cut sheet for the on-board encoders: original vs hydrium vs wl53 (and D2 / today's
JPEG for reference) on the OpenMV frames (2026-10-01).

    python -m compression_study.cutsheet_hyd --data <primary>/data/s4_20260930
    python ~/.claude/skills/before-after-report/scripts/build_report.py \
        compression_study/work/cutsheet_hyd/spec.json compression_study/work/cutsheet_hyd/hyd.html --split

Each method is re-encoded at the knob the study found for the target (``work/rows``), decoded
with the study's decoder, and every side is rendered by one function (bilinear demosaic, white
balance on the card's grey 128 computed once from the original, gamma 1/2.2) at sensor
resolution, saved as lossless WebP. Views: whole frame, card at 1:1, AprilTag at 1:1 (×3).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from compression_study import rois, sim  # noqa: E402
from compression_study import run_study as rs  # noqa: E402
from compression_study.methods import isp  # noqa: E402
from compression_study.methods import raw_planes as rp  # noqa: E402
from nereus_camera_test_rig.color.raw_io import demosaic_bilinear  # noqa: E402

OUT = rs.STUDY / "work" / "cutsheet_hyd"
BPP = {"T1": 0.4, "T2": 0.8}
NAMES = {"H": "hydrium", "W": "wl53", "D2": "D2 libjxl", "M1": "today's JPEG"}


def frameset(data: Path, cam: str, ill: str, cond: str):
    roi = rois.load(rs.STUDY / "config" / "card_rois.yaml")[f"{cam}_{ill}"]
    vmin, dz = rs.CAMERAS[cam][3], rs.CAMERAS[cam][4]
    air = [rs.load(data, cam, ill, -1, i) for i in range(3)]
    air_ctx, air_noise = rs.context_for(air, roi, vmin)
    if cond == "uw":
        reps = [sim.thin(r, air_noise, seed=1000 + 100 * (-1 + 2) + i, dead_zone=dz)
                for i, r in enumerate(air)]
        ctx, _ = rs.context_for(reps, roi, 2.0 if dz == 0 else 3.0)
        return reps[0], ctx, roi
    return air[0], air_ctx, roi


def knob(rows, fsid, method, variant, target):
    """The study's knob for this method at the target: the row closest to the target bytes."""
    rr = [r for r in rows if r.get("fsid") == fsid and r["method"] == method
          and r.get("variant", "") == variant and r["target"] == target and r.get("bytes")]
    r = min(rr, key=lambda r: abs(r["bytes"] - r["target_bytes"]))
    return r["knob"], r["bytes"]


def decoded(raw, ctx, rows, fsid, method, target, d2_mode):
    """(mosaic, bytes) for one method at one target, re-encoded at the study's knob."""
    if method == "M1":
        k, _ = knob(rows, fsid, "M1", "", target)
        wb_q, _, wb, _ = isp.quantized(ctx.wb_isp)
        blob, _ = isp.encode_image(isp.render(raw, wb), raw, "M1", int(float(k)), wb_q, None)
        return isp.decode(blob), len(blob)
    spec = {"H": rp.RawSpec("H", "none"), "W": rp.RawSpec("W", "sqrt", 12),
            "D2": rp.RawSpec("D2", "sqrt", 12, mode=d2_mode)}[method]
    variant = {"H": "H", "W": "W", "D2": f"D2/{d2_mode}"}[method]
    k, _ = knob(rows, fsid, method, variant, target)
    blob = rp.encode(raw, spec, {p: float(k) for p in ("R", "G1", "G2", "B")})
    return rp.decode(blob), len(blob)


def render(m: np.ndarray, raw, wb, box=None) -> np.ndarray:
    x0, y0, x1, y1 = box or (0, 0, m.shape[1], m.shape[0])
    lin = ((m[y0:y1, x0:x1] - raw.black) / (raw.white - raw.black)).astype(np.float32)
    rgb = demosaic_bilinear(lin, raw.cfa) * wb.astype(np.float32)
    return (np.clip(rgb, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)


def save(img: np.ndarray, path: Path, zoom: int = 1) -> str:
    im = Image.fromarray(np.ascontiguousarray(img))
    if zoom > 1:
        im = im.resize((im.width * zoom, im.height * zoom), Image.Resampling.NEAREST)
    im.save(path, "WEBP", lossless=True, quality=100, method=4)
    return f"img/{path.name}"


def views(cam: str, cond: str, roi: dict, shape) -> dict:
    cb = [2 * v for v in roi["card_box"]]
    pad = 24
    card = (max(0, cb[0] - pad) // 2 * 2, max(0, cb[1] - pad) // 2 * 2,
            min(shape[1], cb[2] + pad) // 2 * 2, min(shape[0], cb[3] + pad) // 2 * 2)
    return {"frame": None, "card": card, "tag": tuple(2 * v for v in roi["texture_box"])}


SECTIONS = [  # (camera, condition, view, targets per method)
    ("n6", "uw", "frame", {"T1": ("H", "W", "D2", "M1")}),
    ("n6", "uw", "card", {"T1": ("H", "W", "D2", "M1"), "T2": ("H", "W")}),
    ("ae3", "uw", "frame", {"T1": ("H", "W", "D2", "M1")}),
    ("ae3", "uw", "card", {"T1": ("H", "W", "D2", "M1"), "T2": ("H", "W")}),
    ("n6", "air", "frame", {"T1": ("H", "W", "D2", "M1")}),
    ("n6", "uw", "tag", {"T1": ("H", "W", "D2", "M1")}),
    ("ae3", "air", "tag", {"T1": ("H", "W", "D2", "M1")}),
]
CAM = {"n6": "OpenMV N6", "ae3": "OpenMV AE3"}
COND = {"air": "in air", "uw": "underwater-sim"}
VIEW = {"frame": "whole frame", "card": "the card at 1:1", "tag": "AprilTag at 1:1"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    args = ap.parse_args(argv)
    (OUT / "img").mkdir(parents=True, exist_ok=True)
    rows, _ = __import__("compression_study.report", fromlist=["x"]).load_rows(rs.STUDY / "work")
    modes = json.loads((rs.STUDY / "work" / "d2_modes.json").read_text())
    cache, sections, sizes = {}, [], {}
    for cam, cond, view, todo in SECTIONS:
        fsid = f"{cam}_cool_s-1_{cond}"
        if fsid not in cache:
            cache[fsid] = frameset(args.data, cam, "cool", cond)
        raw, ctx, roi = cache[fsid]
        box = views(cam, cond, roi, raw.shape)[view]
        zoom = 3 if view == "tag" else 1
        wb = ctx.wb_isp
        stem = f"{cam}_{cond}_{view}"
        before = save(render(raw.mosaic.astype(np.float64), raw, wb, box),
                      OUT / "img" / f"{stem}_before.webp", zoom)
        afters = []
        for t, methods in todo.items():
            for m in methods:
                key = (fsid, m, t)
                if key not in cache:
                    cache[key] = decoded(raw, ctx, rows, fsid, m, t, modes.get(cam, "vardct"))
                rec, n = cache[key]
                sizes[f"{fsid} {m} {t}"] = n
                img = save(render(rec, raw, wb, box), OUT / "img" / f"{stem}_{m}_{t}.webp", zoom)
                afters.append({"label": f"{NAMES[m]} · {BPP[t]} bpp ({n / 1000:.0f} kB)",
                               "image": img})
        note = {"frame": "1280×800 Bayer frame, every pixel. Open full screen and zoom to 1:1 "
                         "or more to see the codec's own texture.",
                "card": "The reference card at sensor resolution (1:1). Colour error is "
                        "measured on these patches after the card correction.",
                "tag": "AprilTag 0 at sensor resolution, pixels enlarged 3× (no smoothing)."}
        sections.append({"title": f"{CAM[cam]} · {COND[cond]} · {VIEW[view]}",
                         "note": note[view], "before": before,
                         "before_label": "Before · original raw", "afters": afters,
                         "pixelated": view == "tag"})
        print(stem, len(afters), flush=True)
    spec = json.loads((OUT / "spec_text.json").read_text()) if (OUT / "spec_text.json").exists() \
        else {"title": "On-board encoders compared"}
    spec["sections"] = sections
    spec.setdefault("inline_max_width", 1000)
    (OUT / "spec.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False))
    (OUT / "sizes.json").write_text(json.dumps(sizes, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
