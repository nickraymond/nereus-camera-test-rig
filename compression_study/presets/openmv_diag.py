"""OpenMV RAW pipeline diagnosis for an exposure-sweep run (Nick via EM, 2026-10-05).

    python -m compression_study.presets.openmv_diag <experiment folder> <out dir>

Per camera (N6, AE3, and the IMX708 for reference), on the frame with the card best exposed:
renders, crops, histograms, tag detection (sizes in px), and pipeline checks with PASS/FAIL:
CFA phase (4 phases, scored on the card's colour patches and the two green sites), bit depth /
packing / black level, width/height/stride (row-shift scan + registration to the board's own
JPEG), orientation (tag IDs land where the card puts them; registration vs flips), and
exposure / gain read-back vs requested. Writes <out>/diag.json + PNGs for the sheet.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

from compression_study.rois import find_rois  # noqa: E402
from nereus_camera_test_rig.color.card import load_card  # noqa: E402
from nereus_camera_test_rig.color.metrics import (  # noqa: E402
    delta_e2000,
    linear_to_lab,
    srgb8_to_linear,
)
from nereus_camera_test_rig.color.raw_io import (  # noqa: E402
    RawFrame,
    demosaic_bilinear,
    normalize,
    read_dng,
    read_openmv_bayer,
)

CARD = load_card(REPO / "configs" / "cards" / "nereus_v1.yaml")
TRUTH = {p.id: srgb8_to_linear(np.asarray(p.truth, float)) for p in CARD.patches}
DET = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11),
                              cv2.aruco.DetectorParameters())
PHASES = ("RGGB", "BGGR", "GRBG", "GBRG")
TAG_MM = 31.996  # V1 print master


def tags(gray8: np.ndarray, scale: float = 1.0) -> dict:
    im = cv2.resize(gray8, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) \
        if scale != 1 else gray8
    co, ids, _ = DET.detectMarkers(im)
    if ids is None:
        return {}
    return {int(k): {"centre": (q.reshape(4, 2).mean(0) / scale).round(1).tolist(),
                     "edge_px": round(float(np.mean([np.linalg.norm(q[0, i] - q[0, (i + 1) % 4])
                                                     for i in range(4)])) / scale, 1)}
            for k, q in zip(ids.ravel(), co)}


def render(fr: RawFrame, cfa: str | None = None, wb=None) -> tuple[np.ndarray, np.ndarray]:
    """(linear demosaiced RGB, 8-bit display) with ``cfa`` overriding the frame's own."""
    raw, own, black = fr.active()
    f2 = RawFrame(mosaic=raw, cfa=cfa or own, black_level=tuple(np.asarray(black).ravel()),
                  white_level=fr.white_level)
    lin, sat, c = normalize(f2)
    rgb = demosaic_bilinear(lin, c).astype(np.float32)
    if wb is None:
        g = np.median(rgb.reshape(-1, 3), 0)
        wb = g[1] / np.maximum(g, 1e-4)
    disp = np.clip(rgb * wb / max(np.percentile(rgb[..., 1], 99.5), 1e-3), 0, 1) ** (1 / 2.2)
    return rgb, (disp * 255).astype(np.uint8)


def phase_scores(fr: RawFrame, rois: dict | None) -> dict:
    """Per CFA phase: ΔE00 of the card's colour patches after WB on grey 128 (needs the card),
    and the correlation between the two sites that phase calls green (whole frame)."""
    raw = fr.active()[0].astype(np.float32)
    sites = {(0, 0): raw[0::2, 0::2], (0, 1): raw[0::2, 1::2], (1, 0): raw[1::2, 0::2],
             (1, 1): raw[1::2, 1::2]}
    out = {}
    for ph in PHASES:
        gpos = [(i // 2, i % 2) for i, ch in enumerate(ph) if ch == "G"]
        a, b = sites[gpos[0]].ravel(), sites[gpos[1]].ravel()
        corr = float(np.corrcoef(a, b)[0, 1])
        entry = {"green_sites": gpos, "green_corr": round(corr, 4)}
        if rois is not None:
            rgb, _ = render(fr, ph, wb=np.ones(3))
            means = {}
            for pid in [p.id for p in CARD.patches]:
                q = np.round(np.asarray(rois["patches"][pid]["quad"]) * 2).astype(np.int32)
                m = np.zeros(rgb.shape[:2], np.uint8)
                cv2.fillPoly(m, [q], 1)
                means[pid] = rgb[m.astype(bool)].mean(0)
            wb = means["gray_mid"][1] / np.maximum(means["gray_mid"], 1e-6)
            scale = TRUTH["gray_mid"][1] / max(means["gray_mid"][1], 1e-6)
            cols = [p.id for p in CARD.patches if p.group == "color"]
            de = [float(delta_e2000(linear_to_lab(np.clip(means[k] * wb * scale, 0, None)),
                                    linear_to_lab(TRUTH[k]))) for k in cols]
            entry["colour_patch_de_median_wb_only"] = round(float(np.median(de)), 1)
        out[ph] = entry
    return out


def row_shift_scan(fr: RawFrame) -> dict:
    """Horizontal shift between each pair of same-colour rows 2 apart (phase correlation on
    strips): a stride / wrap error shows up as a step; a healthy frame stays at ~0."""
    raw = fr.active()[0].astype(np.float32)
    shifts = []
    for y in range(0, raw.shape[0] - 18, 16):
        a, b = raw[y:y + 8:2], raw[y + 2:y + 10:2]
        (dx, _), _ = cv2.phaseCorrelate(a.mean(0, keepdims=True).astype(np.float64),
                                        b.mean(0, keepdims=True).astype(np.float64))
        shifts.append(round(float(dx), 2))
    s = np.abs(np.array(shifts))
    return {"max_abs_px": round(float(s.max()), 2), "median_abs_px": round(float(np.median(s)), 2),
            "rows_over_1px": int((s > 1).sum()), "n": len(shifts)}


def register(raw_disp: np.ndarray, jpeg: np.ndarray) -> dict:
    """Phase-correlation shift (and response) of the RAW render vs the board's JPEG, for the
    image as is and flipped; the right orientation has the strongest, near-zero match."""
    a = cv2.cvtColor(raw_disp, cv2.COLOR_RGB2GRAY).astype(np.float64)
    b = cv2.cvtColor(np.asarray(jpeg), cv2.COLOR_RGB2GRAY).astype(np.float64)
    if a.shape != b.shape:
        b = cv2.resize(b, a.shape[::-1])
    out = {}
    for name, im in (("as is", a), ("h-flip", a[:, ::-1]), ("v-flip", a[::-1]),
                     ("rot180", a[::-1, ::-1])):
        (dx, dy), resp = cv2.phaseCorrelate(np.ascontiguousarray(im), b)
        out[name] = {"dx": round(float(dx), 1), "dy": round(float(dy), 1),
                     "response": round(float(resp), 3)}
    return out


def tag_sharpness(g8: np.ndarray, found: dict) -> dict:
    """Edge sharpness (mean gradient magnitude / local contrast) in a window at each of the
    card's 4 tag positions; missing tags are placed from the found ones with the card's
    geometry (tag-centre spacing 364.9 x 91.567 mm). Low values = optically soft there."""
    if not found:
        return {}
    pos = {k: np.array(v["centre"]) for k, v in found.items()}
    edge = np.mean([v["edge_px"] for v in found.values()])
    ppm = edge / TAG_MM
    dx, dy = 364.9 * ppm, 91.567 * ppm
    if 1 in pos and 3 in pos:
        down = (pos[3] - pos[1]) / np.linalg.norm(pos[3] - pos[1])
        left = np.array([-down[1], down[0]])
        pos.setdefault(0, pos[1] + left * dx)
        pos.setdefault(2, pos[3] + left * dx)
    elif 0 in pos and 2 in pos:
        down = (pos[2] - pos[0]) / np.linalg.norm(pos[2] - pos[0])
        right = np.array([down[1], -down[0]])
        pos.setdefault(1, pos[0] + right * dx)
        pos.setdefault(3, pos[2] + right * dx)
    out = {}
    h = int(edge * 0.6)
    for k, (x, y) in sorted(pos.items()):
        x, y = int(x), int(y)
        w = g8[max(y - h, 0):y + h, max(x - h, 0):x + h].astype(np.float32)
        if w.size == 0:
            continue
        gx, gy = np.gradient(w)
        contrast = max(np.percentile(w, 95) - np.percentile(w, 5), 1.0)
        out[k] = {"centre": [x, y], "decoded": k in found,
                  "sharpness": round(float(np.hypot(gx, gy).mean() / contrast), 4),
                  "contrast_dn": round(float(contrast), 1)}
    return out


def histogram_png(rgb_raw01: np.ndarray, path: Path, title: str) -> None:
    W, H = 520, 180
    img = np.full((H, W, 3), 255, np.uint8)
    cols = [(200, 60, 60), (40, 150, 70), (60, 90, 210)]
    for c in range(3):
        h, _ = np.histogram(rgb_raw01[..., c].ravel(), bins=256, range=(0, 1))
        h = np.log1p(h) / max(np.log1p(h).max(), 1e-6)
        pts = np.array([[int(i * (W - 20) / 255) + 10, int(H - 20 - v * (H - 40))]
                        for i, v in enumerate(h)], np.int32)
        cv2.polylines(img, [pts], False, cols[c], 1, cv2.LINE_AA)
    cv2.putText(img, title, (10, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (40, 40, 40), 1)
    Image.fromarray(img).save(path)


def main(exp: str, out: str) -> int:
    exp_dir, out_dir = Path(exp), Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rec = json.loads((exp_dir / "experiment.json").read_text())
    res: dict = {"experiment": rec["experiment_id"], "cameras": {}}
    for cam in ("openmv_n6", "openmv_ae3", "imx708"):
        d = exp_dir / "captures" / cam
        ext = "dng" if cam == "imx708" else "bayer"
        frames = sorted(d.glob(f"*sweep*.{ext}"))
        reads = [(f, read_dng(f) if ext == "dng" else read_openmv_bayer(f)) for f in frames]
        # the card-best frame: card located on it, white not clipped, longest such shutter
        best, rois = None, None
        for f, fr in reversed(reads):
            try:
                r = find_rois(fr)
            except ValueError:
                continue
            raw = fr.active()[0]
            if (raw >= fr.white_level).mean() < 0.02:
                best, rois = (f, fr), r
                break
        if best is None:  # no card: the frame where the most tags decode (longest on a tie)
            best = max(reversed(reads), key=lambda t: len(tags(render(t[1])[1][..., 1])))
        f, fr = best
        side = json.loads(f.with_suffix(".json").read_text()) if ext == "bayer" else \
            json.loads(f.with_name(f.with_suffix(".jpg").name + ".rpicam.json").read_text())
        rgb, disp = render(fr)
        Image.fromarray(disp).save(out_dir / f"{cam}_raw.png")
        jpeg = Image.open(sorted(d.glob(f"{cam}_image_*.jpg"))[0]).convert("RGB")
        if cam == "imx708":
            jpeg_small = jpeg.resize((disp.shape[1], disp.shape[0]))
        else:
            jpeg_small = jpeg
        jpeg_small.save(out_dir / f"{cam}_jpeg.jpg", quality=90)
        lin = normalize(fr)[0]
        raw_rgb01 = np.stack([lin[0::2, 0::2], (lin[0::2, 1::2] + lin[1::2, 0::2]) / 2,
                              lin[1::2, 1::2]], -1)
        histogram_png(raw_rgb01, out_dir / f"{cam}_hist.png",
                      f"{cam} RAW sites (log count), x = 0..full scale")
        g8 = disp[..., 1]
        t1, t2 = tags(g8, 1.0), tags(g8, 2.0)
        jt = tags(cv2.cvtColor(np.asarray(jpeg), cv2.COLOR_RGB2GRAY), 1.0)
        entry = {"frame": f.name, "card_located": rois is not None,
                 "tags_found_by_locate": rois.get("tags_found") if rois else None,
                 "aruco_raw_1x": t1, "aruco_raw_2x": t2, "aruco_board_jpeg": jt,
                 "clip_pct": [round(float((raw_rgb01[..., c] >= 0.999).mean() * 100), 2)
                              for c in range(3)],
                 "mosaic": {"shape": list(fr.mosaic.shape), "dtype": str(fr.mosaic.dtype),
                            "min": int(fr.mosaic.min()), "max": int(fr.mosaic.max()),
                            "zeros_pct": round(float((fr.mosaic == 0).mean() * 100), 2),
                            "cfa": fr.cfa, "black": list(np.asarray(fr.black_level).ravel()),
                            "white": fr.white_level, "file_bytes": f.stat().st_size},
                 "sidecar": {k: side.get(k) for k in ("width", "height", "cfa", "bits",
                                                      "black_level", "white_level",
                                                      "exposure_us", "gain_db", "requested",
                                                      "ExposureTime", "AnalogueGain")},
                 "phases": phase_scores(fr, rois),
                 "row_shift": row_shift_scan(fr),
                 "registration_vs_jpeg": register(disp, jpeg_small) if cam != "imx708" else None}
        # 1:1 crop where the card is (from tags if found, else the board-JPEG tags)
        pts = [v["centre"] for v in (t1 or jt or {}).values()]
        if pts:
            cx, cy = np.mean(pts, 0)
        else:
            cx, cy = disp.shape[1] / 2, disp.shape[0] / 2
        x0, y0 = int(max(cx - 260, 0)), int(max(cy - 150, 0))
        crop = disp[y0:y0 + 300, x0:x0 + 520]
        Image.fromarray(crop).resize((crop.shape[1] * 2, crop.shape[0] * 2),
                                     Image.Resampling.NEAREST).save(out_dir / f"{cam}_crop.png")
        entry["crop_origin"] = [x0, y0]
        # 4-phase render strip
        strip = []
        for ph in PHASES:
            _, dp = render(fr, ph)
            dp = dp[y0:y0 + 300, x0:x0 + 520].copy()
            cv2.putText(dp, ph, (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 3)
            cv2.putText(dp, ph, (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 1)
            strip.append(dp)
        Image.fromarray(np.concatenate(strip, 1)).save(out_dir / f"{cam}_phases.png")
        # detection on a demosaiced, 3x-upsampled, contrast-stretched crop around each missing tag
        up = cv2.resize(g8[y0:y0 + 300, x0:x0 + 520], None, fx=3, fy=3,
                        interpolation=cv2.INTER_CUBIC)
        up = cv2.createCLAHE(2.0, (8, 8)).apply(up)
        entry["aruco_crop_3x_clahe"] = tags(up, 1.0) if cam != "imx708" else "n/a (reference)"
        entry["tag_sharpness"] = tag_sharpness(g8, t1 or jt)
        res["cameras"][cam] = entry
        print(cam, f.name, "located", entry["card_located"], "aruco1x", sorted(t1), "2x",
              sorted(t2), "jpeg", sorted(jt), "crop3x", sorted(entry["aruco_crop_3x_clahe"]),
              "row shift", entry["row_shift"], flush=True)
    (out_dir / "diag.json").write_text(json.dumps(res, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:3]))
