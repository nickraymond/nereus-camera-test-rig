"""How big a scene crop fits a fixed 50 kB before the decoded image breaks down? (Nick, 2026-10-01)

    python -m compression_study.crop_sweep --data <primary>/data/s4_20260930 [--jobs 4]

IMX708, last night's card frames, cool + warm lamp, in air and underwater-sim. 16:9 crops from
today's 1600×900 field crop up to the full 4608×2592, centred on the card + chart. Each crop is
compressed to 50 kB (±5 %) and rebuilt, then judged on:
- **card found**: AprilTags detected on the decoded raw (``color.locate``, the rig's own
  detector) — the study's primary success metric;
- **colour**: mean stress ΔE00 on the card + chart patches (study protocol), and block ΔE00;
- **detail**: SSIM of the AprilTag-0 region at sensor resolution (when the tag is in the crop).
Methods: W (wl53, sqrt-12 planes), W/bin2 (2×2-binned planes → wl53, decoded back up), D2
(JPEG XL modular, the Phase 1 best) and M2h (HEIC, the best processed baseline at tiny sizes).
Writes ``work/crop_sweep/rows.json`` and before/after renders for the slider page.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from compression_study import metrics, noise, rate, rois, sim  # noqa: E402
from compression_study import run_study as R  # noqa: E402
from compression_study.methods import isp  # noqa: E402
from compression_study.methods import plane_codecs as pc  # noqa: E402
from compression_study.methods import raw_planes as rp  # noqa: E402
from nereus_camera_test_rig.color.card import load_card  # noqa: E402
from nereus_camera_test_rig.color.jpeg_geometry import JpegMap  # noqa: E402
from nereus_camera_test_rig.color.locate import locate_frame, tag_geometry, tag_spec  # noqa: E402
from nereus_camera_test_rig.color.raw_io import RawFrame, demosaic_bilinear  # noqa: E402

BUDGET = 50_000
WIDTHS = (1600, 2000, 2400, 2800, 3200, 3600, 4000, 4608)
RENDER_WIDTHS = (1600, 2400, 3200, 4608)  # before/after images for the slider page
SPECS = {"W": rp.RawSpec("W", "sqrt", 12), "W/bin2": rp.RawSpec("W", "sqrt", 12, binned=True),
         "D2": rp.RawSpec("D2", "sqrt", 12, mode="modular")}
OUT = REPO / "compression_study" / "work" / "crop_sweep"


def crop_box(roi: dict, w: int, frame_w=4608, frame_h=2592) -> tuple[int, int, int, int]:
    h = min(frame_h, round(w * 9 / 16 / 2) * 2)
    pts = np.array([np.mean(p["quad"], axis=0) for p in roi["patches"].values()]) * 2
    cb = np.asarray(roi["card_box"], float) * 2
    cx = (min(pts[:, 0].min(), cb[0]) + max(pts[:, 0].max(), cb[2])) / 2
    cy = (min(pts[:, 1].min(), cb[1]) + max(pts[:, 1].max(), cb[3])) / 2
    x0 = int(np.clip(cx - w / 2, 0, frame_w - w)) // 2 * 2
    y0 = int(np.clip(cy - h / 2, 0, frame_h - h)) // 2 * 2
    return x0, y0, w, h


def tags_found(mosaic: np.ndarray, raw) -> int:
    card = load_card(rois.CARD)
    fr = RawFrame(mosaic=np.clip(np.round(mosaic), 0, raw.white).astype(np.uint16), cfa=raw.cfa,
                  black_level=(float(raw.black),) * 4, white_level=float(raw.white))
    rec = locate_frame(Path("crop"), None, card.corner_map, JpegMap.offset(0, 0),
                       raw_reader=lambda _p: fr, geometry=tag_geometry(card), spec=tag_spec(card))
    return len(rec.get("tags_found") or [])


def render(m: np.ndarray, raw, wb, box=None) -> np.ndarray:
    X0, Y0, X1, Y1 = box or (0, 0, m.shape[1], m.shape[0])
    lin = ((m[Y0:Y1, X0:X1] - raw.black) / (raw.white - raw.black)).astype(np.float32)
    rgb = demosaic_bilinear(lin, raw.cfa) * wb.astype(np.float32)
    return (np.clip(rgb, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)


def job(args: dict) -> list[dict]:
    data, ill, cond, w = Path(args["data"]), args["ill"], args["cond"], args["w"]
    roi = rois.load(R.STUDY / "config" / "card_rois.yaml")[f"imx708_{ill}"]
    air = [R.load(data, "imx708", ill, -1, i) for i in range(3)]
    _, air_noise = R.context_for(air, roi, 2.0)
    reps = air if cond == "air" else [sim.thin(r, air_noise, seed=1000 + 100 + i)
                                      for i, r in enumerate(air)]
    x0, y0, cw, ch = crop_box(roi, w)
    crop = [replace(r, mosaic=np.ascontiguousarray(r.mosaic[y0:y0 + ch, x0:x0 + cw])) for r in reps]
    croi = R.shift_rois(roi, x0 // 2, y0 // 2)
    tb = croi["texture_box"]
    tag_in = tb[0] >= 0 and tb[1] >= 0 and tb[2] <= cw // 2 and tb[3] <= ch // 2
    if not tag_in:
        croi["texture_box"] = [0, 0, 16, 16]  # placeholder; ssim_tex reported as missing
    raw = crop[0]
    ctx = metrics.Context(raw, crop, croi, noise.estimate(crop, croi, 2.0),
                          R.grey_wb(raw, croi))
    base = {"illuminant": ill, "condition": cond, "crop_w": cw, "crop_h": ch, "crop_x": x0,
            "crop_y": y0, "megapixels": cw * ch / 1e6, "tag_in_crop": bool(tag_in),
            "tags_found_original": tags_found(raw.mosaic.astype(float), raw)}
    rows, renders = [], {}
    for name, spec in SPECS.items():
        t0 = time.perf_counter()
        codes, maxval = rp.code_planes(raw, spec)
        curves = {k: rate.Curve(lambda q, c=c: rp.encode_plane(c, maxval, spec, q))
                  for k, c in codes.items()}
        d, lo, hi = pc.KNOBS[spec.codec]
        total = lambda k: sum(c.size(k) for c in curves.values()) + 24  # noqa: E731
        sol = rate.solve(total, BUDGET, lo, hi, d, False, start=2.0)
        k = sol.knobs[0]
        blob = rp.assemble(raw, spec, {p: c.get(k)[0] for p, c in curves.items()})
        rec = rp.decode(blob)
        rows.append(_row(base, name, blob, rec, ctx, raw, sol, k, time.perf_counter() - t0))
        renders[name] = rec
    # HEIC on the rendered crop (processed baseline)
    t0 = time.perf_counter()
    wb_q, _, wb, _ = isp.quantized(ctx.wb_isp)
    img = isp.render(raw, wb)
    curve = rate.Curve(lambda q: isp.encode_image(img, raw, "M2h", q, wb_q))
    sol = rate.solve(curve.size, BUDGET, 0, 100, +1, True)
    k = min(sol.knobs, key=lambda q: abs(curve.size(q) - BUDGET))
    blob = curve.get(k)[0]
    rec = isp.decode(blob)
    rows.append(_row(base, "HEIC", blob, rec, ctx, raw, sol, k, time.perf_counter() - t0))
    if ill == "cool" and w in RENDER_WIDTHS:
        _save_renders(raw, ctx, croi, cond, w, {"wl53": renders["W"], "HEIC": rec})
    return rows


def _row(base, name, blob, rec, ctx, raw, sol, k, secs) -> dict:
    m = metrics.evaluate(ctx, rec)
    return {**base, "method": name, "bytes": len(blob), "bpp": len(blob) * 8 / raw.n_px,
            "reachable": bool(abs(len(blob) / BUDGET - 1) <= 0.05), "knob": k,
            "tags_found": tags_found(rec, raw), "stress_de_mean": m["stress_de_mean"],
            "patch_de_mean": m["patch_de_mean"], "block_de_med": m["block_de_med"],
            "red_err_noise": m["red_err_noise"], "ssim_full": m["ssim_full"],
            "ssim_tag": m["ssim_tex"] if base["tag_in_crop"] else None, "seconds": secs}


def _save_renders(raw, ctx, croi, cond, w, recs):
    d = OUT / "renders"
    d.mkdir(parents=True, exist_ok=True)
    wb = ctx.wb_isp
    full_w = 1200
    views = {"crop": None}
    tb = [2 * v for v in croi["texture_box"]]
    if tb[2] - tb[0] > 32:
        views["tag"] = tuple(tb)
    q = np.asarray(croi["patches"]["orange"]["quad"]) * 2
    cx, cy = (q.mean(0) // 2 * 2).astype(int)
    views["patches"] = (cx - 300, max(0, cy - 170), cx + 300, max(0, cy - 170) + 340)
    for view, box in views.items():
        imgs = {"before": render(raw.mosaic.astype(float), raw, wb, box)}
        for name, rec in recs.items():
            imgs[name] = render(rec, raw, wb, box)
        for tag, img in imgs.items():
            if view == "crop":
                size = (full_w, round(full_w * img.shape[0] / img.shape[1]))
                interp = cv2.INTER_AREA
            else:
                z = 3 if view == "tag" else 2
                size = (img.shape[1] * z, img.shape[0] * z)
                interp = cv2.INTER_NEAREST
            out = cv2.resize(img, size, interpolation=interp)
            cv2.imwrite(str(d / f"{cond}_{w}_{view}_{tag}.jpg"),
                        cv2.cvtColor(out, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args(argv)
    pc.wl53_build()
    jobs = [{"data": args.data, "ill": ill, "cond": cond, "w": w}
            for w in WIDTHS for ill in ("cool", "warm") for cond in ("air", "uw")]
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        for res in ex.map(job, jobs):
            rows += res
            r0 = res[0]
            head = f"{r0['crop_w']}x{r0['crop_h']} {r0['illuminant']} {r0['condition']}: "
            print(head + ", ".join(
                f"{r['method']} {r['bytes'] / 1000:.0f}kB tags {r['tags_found']} sDE "
                f"{r['stress_de_mean']:.2f}" for r in res), flush=True)
            (OUT / "rows.json").write_text(json.dumps(rows, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
