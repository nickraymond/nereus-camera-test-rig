"""Host tool: S4 calibration cut sheet — each camera's corrected card + chart next to the truth.

Usage (after ``python -m host_tools.color calibrate <session.yaml> --data <root>``)::

    python -m host_tools.s4_cutsheet configs/calibration/sessions/<session>.yaml \
        --data <root> [--past imx708=<dir/stop_+0.dng> --past openmv_n6=<dir/locked.bayer> ...]

Per camera, one panel per illuminant (the held-out stop-0 frame) and, optionally, one per
``--past`` frame (an earlier light test, corrected with the reference illuminant's matrix):
the card + chart rectified from the RAW, white-balanced on the anchor grey, through the
fitted matrix, sRGB; swatches camera (top) vs truth (bottom) with ΔE00. Writes
``results/color/<session>/calibrate/cutsheet.html`` (self-contained).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from nereus_camera_test_rig.color.calibrate import (  # noqa: E402
    anchor_y,
    sample_frame,
    white_balanced,
)
from nereus_camera_test_rig.color.card import load_card  # noqa: E402
from nereus_camera_test_rig.color.chart import load_chart  # noqa: E402
from nereus_camera_test_rig.color.jpeg_geometry import JpegMap  # noqa: E402
from nereus_camera_test_rig.color.locate import locate_frame, tag_geometry, tag_spec  # noqa: E402
from nereus_camera_test_rig.color.measure_card import linear_to_srgb8  # noqa: E402
from nereus_camera_test_rig.color.metrics import delta_e2000, linear_to_lab  # noqa: E402
from nereus_camera_test_rig.color.patches import homography, mosaic_to_binned  # noqa: E402
from nereus_camera_test_rig.color.raw_io import bin2x2, normalize  # noqa: E402
from nereus_camera_test_rig.color.stages import open_raw  # noqa: E402
from nereus_camera_test_rig.config import load_yaml  # noqa: E402

CANVAS = (1000, 680)  # card + chart area of the canonical frame (2000 x 1360) at 0.5 px


def enc(lin) -> np.ndarray:
    return np.clip(linear_to_srgb8(np.maximum(lin, 0)), 0, 255)


def render(path: Path, card, matrix: np.ndarray, gain: np.ndarray) -> str:
    frame = open_raw(path)
    rec = locate_frame(path, None, card.corner_map, JpegMap.offset(0, 0),
                       raw_reader=lambda _p: frame, geometry=tag_geometry(card),
                       spec=tag_spec(card))
    linear, sat, cfa = normalize(frame)
    binned, _ = bin2x2(linear, cfa, sat)
    H = mosaic_to_binned(frame.valid_crop) @ homography(card, np.asarray(rec["quad_raw"]))
    T = np.diag([2.0, 2.0, 1.0])
    warped = cv2.warpPerspective((binned * gain).astype(np.float32), H @ T, CANVAS,
                                 flags=cv2.WARP_INVERSE_MAP | cv2.INTER_AREA)
    img = enc(warped.reshape(-1, 3) @ matrix.T).reshape(warped.shape).astype(np.uint8)
    ok, jpg = cv2.imencode(".jpg", img[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 88])
    return base64.b64encode(jpg).decode()


def panel(label: str, path: Path, cfg: dict, card, chart, truth: dict, matrix, ids) -> str:
    s = sample_frame(path, Path(cfg["card"]), Path(cfg["chart"]["config"]),
                     tuple(cfg["chart"]["region"]))
    anchor = cfg["wb_anchor"]
    y = anchor_y(card, anchor)
    wb = white_balanced(s, anchor, y)
    gain = y / np.asarray(s["patches"][anchor]["mean"])
    sw, des, wb_only = "", [], []
    for pid in ids:
        if pid not in truth or pid not in wb:
            continue
        pred = wb[pid] @ matrix.T
        de = float(delta_e2000(linear_to_lab(truth[pid]), linear_to_lab(pred)))
        if pid != anchor:
            des.append(de)
            wb_only.append(float(delta_e2000(linear_to_lab(truth[pid]), linear_to_lab(wb[pid]))))
        cls = "bad" if de > 6 else "mid" if de > 3 else ""
        sw += (f'<div class="sw" title="{pid}: ΔE00 {de:.1f}">'
               f'<div style="background:rgb{tuple(int(v) for v in enc(pred))}"></div>'
               f'<div style="background:rgb{tuple(int(v) for v in enc(truth[pid]))}"></div>'
               f'<span class="{cls}">{de:.1f}</span></div>')
    chart_ok = "error" not in s.get("chart", {})
    return (f'<div class="cell"><div class="lab">{label}</div>'
            f'<img src="data:image/jpeg;base64,{render(path, card, matrix, gain)}">'
            f'<div class="stats">ΔE00 vs truth: <b>{np.median(des):.1f}</b> median · p90 '
            f'{np.percentile(des, 90):.1f} · n={len(des)} | WB only {np.median(wb_only):.1f}'
            f'<br>chart {"found" if chart_ok else "not found"} · <span class="f">{path.name}'
            f'</span></div><div class="sws">{sw}</div></div>')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session", type=Path)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=REPO / "results" / "color")
    ap.add_argument("--past", action="append", default=[], metavar="CAMERA=FRAME",
                    help="an earlier light-test frame for that camera (repeatable)")
    args = ap.parse_args(argv)
    cfg = load_yaml(args.session)
    card, chart = load_card(cfg["card"]), load_chart(cfg["chart"]["config"])
    cal = args.out / cfg["session"] / "calibrate"
    summary = json.loads((cal / "summary.json").read_text())
    truth = {k: np.asarray(v) for k, v in
             json.loads((cal / "truth.json").read_text())["values_linear_srgb"].items()}
    ids = [p.id for p in card.patches] + [p.id for p in chart.patches]
    past = dict(p.split("=", 1) for p in args.past)
    ref_ill = cfg["reference"]["illuminant"]
    body = ""
    for cam, c in cfg["cameras"].items():
        mats = {i: np.asarray(m) for i, m in summary["matrices"][cam].items()}
        cells = []
        if cam in past:
            cells.append(panel(f"Past test · {ref_ill} matrix", Path(past[cam]), cfg, card,
                               chart, truth, mats[ref_ill], ids))
        for ill, folder in c["captures"].items():
            held = sorted((args.data / folder).glob("stop_+0_r2.*"))
            frame = next(p for p in held if p.suffix in (".dng", ".bayer"))
            note = cfg["illuminants"].get(ill, {}).get("cct_k")
            cells.append(panel(f"{ill}{f' · {note} K' if note else ''} · held-out r2", frame,
                               cfg, card, chart, truth, mats[ill], ids))
        body += f'<h2>{cam}</h2><div class="row">{"".join(cells)}</div>'
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>S4 Calibration Cut Sheet</title>
<style>body{{font:13px/1.45 -apple-system,Helvetica,Arial,sans-serif;color:#1a1d21;margin:20px}}
h2{{font-size:16px;margin:22px 0 6px;border-bottom:1px solid #dde1e6}}
.row{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}} .cell img{{width:100%}}
.lab{{font-weight:600}} .stats{{font-size:12px;color:#5c6570}} .stats b{{color:#1a1d21}}
.f{{font-family:Menlo,monospace;font-size:11px}} .sws{{display:flex;flex-wrap:wrap;gap:3px}}
.sw{{width:30px;font-size:9px;text-align:center}} .sw div{{height:13px}}
.mid{{color:#b26b00}} .bad{{color:#c0272d;font-weight:700}}</style></head><body>
<h1>S4 calibration cut sheet — {cfg['session']}</h1>
<p>Card + chart from the RAW, white-balanced on {cfg['wb_anchor']}, through the fitted matrix,
sRGB. Swatches: camera (top) vs truth (bottom), ΔE00 (orange &gt; 3, red &gt; 6). Truth:
{json.loads((cal / 'truth.json').read_text())['method']} — provisional.</p>{body}</body></html>"""
    (cal / "cutsheet.html").write_text(html)
    print(cal / "cutsheet.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
