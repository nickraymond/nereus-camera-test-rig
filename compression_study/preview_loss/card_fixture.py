"""A production .nrjxl fixture with a reference card fully inside the crop (for the backend's
raw_card_v1 "card found" tests; EM 2026-10-05).

    NRJXL_BM_DIR=<bm #120 export> python -m compression_study.preview_loss.card_fixture \
        --dng stop_+0_r0.dng [--metadata stop_+0_r0.json] --card configs/cards/nereus_v3_c1.yaml \
        --cap 180 --out <dir>

1. Locate the card on the DNG (RAW first) and take its full outline (the card's canonical
   corners through the tag homography), in native active-area px.
2. Crop 1600x900 centred on the card, even origin, clamped to the sensor; fail loudly unless the
   whole card is inside with a 16 px margin.
3. Encode with bm #120's own path: ``rc_raw_jxl.encode_still`` (read_dng_crop -> code_planes
   -> byte-target search -> sealed container) at ``--cap`` messages of 384 base64 chars, with a
   budget that never runs out (the wake's room is the cap; START/END excluded as on a unit).
4. Check the fixture: unpack (CRC) and decode the .nrjxl, rebuild the mosaic, and locate the
   card on the DECODED image — the "card found" the backend test expects.

Writes <out>/<stem>_compressed.nrjxl, <stem>.dng (copy), <stem>.json (metadata, if given) and
fixture.json (crop, card outline in native and crop px, encoder result, decode check).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from compression_study.preview_loss import codec as C

W, H = 1600, 900
MARGIN = 16


class Budget:
    """A wake budget that never runs out: the room is the message cap alone."""
    seconds_per_message = 1.3

    def __init__(self, cap: int):
        self.cap = cap

    def remaining_s(self) -> float:
        return 1e9

    def messages_fit(self, n: int) -> bool:
        return n <= self.cap + 2


def card_outline(frame, card) -> tuple[np.ndarray, dict]:
    from nereus_camera_test_rig.color.jpeg_geometry import JpegMap
    from nereus_camera_test_rig.color.locate import locate_frame, tag_geometry, tag_spec
    from nereus_camera_test_rig.color.patches import homography
    rec = locate_frame(Path("frame"), None, card.corner_map, JpegMap.offset(0, 0),
                       raw_reader=lambda _p: frame, geometry=tag_geometry(card),
                       spec=tag_spec(card))
    if not rec["located"]:
        raise SystemExit(f"card not located: {rec.get('reason')}")
    Hm = homography(card, np.asarray(rec["quad_raw"], float))
    cw, ch = card.canonical_w, card.canonical_h
    corners = np.array([[[0, 0], [cw, 0], [cw, ch], [0, ch]]], np.float64)
    return cv2.perspectiveTransform(corners, Hm)[0], rec


def crop_for(outline: np.ndarray, native_wh) -> tuple[int, int, int, int]:
    cx, cy = outline.mean(axis=0)
    x = int(np.clip(cx - W / 2, 0, native_wh[0] - W)) // 2 * 2
    y = int(np.clip(cy - H / 2, 0, native_wh[1] - H)) // 2 * 2
    lo, hi = outline.min(axis=0), outline.max(axis=0)
    if lo[0] < x + MARGIN or lo[1] < y + MARGIN or hi[0] > x + W - MARGIN or hi[1] > y + H - MARGIN:
        raise SystemExit(f"card outline {np.round(lo).tolist()}..{np.round(hi).tolist()} does not fit "
                         f"a {W}x{H} crop with a {MARGIN} px margin: move the camera back or use "
                         "another frame")
    return x, y, W, H


def meta_from_dng(frame) -> dict:
    """Stand-in capture metadata when the rpicam JSON is missing (labelled in fixture.json)."""
    wb = frame.as_shot_wb or (2.0, 1.0, 1.6)
    return {"ExposureTime": int(round((frame.exposure_s or 0.01) * 1e6)), "AnalogueGain": 1.0,
            "ColourGains": [float(wb[0]), float(wb[2])],
            "ColourCorrectionMatrix": [1, 0, 0, 0, 1, 0, 0, 0, 1]}


def decode_check(blob: bytes, card) -> dict:
    from compression_study.preview_loss.nrjxl_view import decode_nrjxl
    from nereus_camera_test_rig.color.raw_io import RawFrame
    d = decode_nrjxl(blob)
    head = d["head"]
    mos = np.round(d["mosaic"] * (head["white"] - head["black"]) + head["black"]).astype(np.uint16)
    fr = RawFrame(mosaic=mos, cfa=head["cfa"], black_level=(float(head["black"]),) * 4,
                  white_level=float(head["white"]), exposure_s=None)
    try:
        outline, rec = card_outline(fr, card)
    except SystemExit as exc:
        return {"card_found": False, "reason": str(exc)}
    return {"card_found": True, "tags_found": rec["tags_found"],
            "quad_crop_px": np.round(np.asarray(rec["quad_raw"]), 2).tolist(),
            "outline_crop_px": np.round(outline, 1).tolist(), "distance": d["meta"]["distance"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dng", type=Path, required=True)
    ap.add_argument("--metadata", type=Path)
    ap.add_argument("--card", type=Path, required=True)
    ap.add_argument("--cap", type=int, default=180)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--crop", help="x,y,w,h to force (native px); default: centred on the card")
    a = ap.parse_args()
    rc = C.rc()
    from nereus_camera_test_rig.color.card import load_card
    from nereus_camera_test_rig.color.raw_io import read_dng
    card = load_card(a.card)
    frame = read_dng(a.dng)
    raw = frame.active()[0]
    outline, rec = card_outline(frame, card)
    xywh = tuple(int(v) for v in a.crop.split(",")) if a.crop else crop_for(outline, raw.shape[::-1])
    meta = json.loads(a.metadata.read_text()) if a.metadata else meta_from_dng(frame)
    cfg = dict(rc.DEFAULT_CONFIG, format="nrjxl", source="fixture")
    a.out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as work:
        res = rc.encode_still(str(a.dng), meta, cfg, crop_xywh=xywh, budget=Budget(a.cap),
                              message_cap=a.cap, chunk_b64_chars=384, work_dir=work,
                              log=lambda m: print(m, flush=True))
    blob = res["blob"]
    stem = a.dng.stem
    nr = a.out / f"{stem}{rc.CONTENT_SUFFIX}"
    nr.write_bytes(blob)
    shutil.copy(a.dng, a.out / a.dng.name)
    if a.metadata:
        shutil.copy(a.metadata, a.out / a.metadata.name)
    x, y = xywh[:2]
    check = decode_check(blob, card)
    cjxl = subprocess.run([C.CJXL, "--version"], capture_output=True, text=True).stdout.split("\n")[0]
    bm = Path(__import__("os").environ["NRJXL_BM_DIR"]) / "rc_raw_jxl.py"
    info = {
        "fixture": nr.name, "dng": a.dng.name, "dng_sha256": hashlib.sha256(a.dng.read_bytes()).hexdigest(),
        "nrjxl_sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob),
        "message_count": res["message_count"], "message_cap": a.cap, "chunk_b64_chars": 384,
        "crop_xywh_native": list(xywh), "native_wh": list(raw.shape[::-1]),
        "card": card.card_id, "card_truth": card.truth_source,
        "card_tags_found_on_dng": rec["tags_found"],
        "card_outline_native_px": np.round(outline, 1).tolist(),
        "card_outline_crop_px": np.round(outline - [x, y], 1).tolist(),
        "distance": res["distance"], "attempts": res["attempts"], "attempt_log": res["attempt_log"],
        "rate_mode": res["rate_mode"], "encode_s": round(time.monotonic() - t0, 1),
        "metadata": a.metadata.name if a.metadata else "derived from DNG tags (stand-in)",
        "encoder": {"rc_raw_jxl_sha256": hashlib.sha256(bm.read_bytes()).hexdigest()[:16],
                    "cjxl": cjxl, "effort": cfg["effort"], "target_fill": cfg["target_fill"]},
        "decode_check": check}
    (a.out / "fixture.json").write_text(json.dumps(info, indent=1))
    print(json.dumps({k: info[k] for k in ("fixture", "bytes", "message_count", "distance",
                                           "crop_xywh_native")}), "card found on decode:",
          check["card_found"], check.get("tags_found"))
    return 0 if check["card_found"] else 1


if __name__ == "__main__":
    sys.exit(main())
