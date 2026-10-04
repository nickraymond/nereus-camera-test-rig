"""Three customer presets for Sprint28 nrjxl stills — WIDE / MEDIUM / HIGH (Nick, 2026-10-02).

    python -m compression_study.presets.preset_study --data <primary>/data/s4_20260930 \
        --prod <bm_cam_legacy export>/BM_Devel_Pi [--lamps cool,warm]

Desk-only (Phase A) on the nereus002 IMX708 full-sensor DNGs. Matches production by calling
the Sprint28 camera code (bm_cam_legacy PR #120, ``rc_raw_jxl``: read_dng_crop → code_planes
(4 Bayer planes, sqrt → 12 bit) → encode_rung (cjxl -m 1 -e 5 -d …) → build_params +
seal_container) and today's still path (``rc_jpeg_encoder.prepare_source`` 1600×900 →
1000×562 Lanczos, progressive JPEG on the ladder 15/13/11/9 under the 195-message cap).

Per candidate × lamp: nrjxl at ~50 / 75 / 112 kB (distance found by bisection, production
range 0.1–15), against the RAW reference render and today's pjpg of the same exposure.
Metrics on the region today's pjpg covers (or the candidate's own region when smaller):
- SSIM (luma) vs the RAW reference render at native resolution (pjpg tone-matched first:
  a per-channel affine fit in linear light, since the camera ISP renders colour differently);
- patch ΔE00 vs the RAW reference on the card / chart patch means in the region;
- AprilTag: detected? and edge acutance (mean edge gradient / tag contrast) relative to RAW.
PASS / WARN / FAIL: "no worse than today's pjpg for the same scene" on all three metrics.

Outputs: runs/<run_id>/{run_manifest.json, metrics.csv, distances.csv, renders/…}.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO)]

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from compression_study import rois  # noqa: E402
from compression_study.common import merge, split, tool  # noqa: E402
from compression_study.methods import raw_planes as rp  # noqa: E402
from compression_study.metrics import ssim  # noqa: E402
from PIL import Image  # noqa: E402

from nereus_camera_test_rig.color.metrics import delta_e2000, linear_to_lab  # noqa: E402
from nereus_camera_test_rig.color.raw_io import demosaic_bilinear  # noqa: E402

TODAY = (1504, 846, 1600, 900)           # production still.crop default → 1000×562
TARGETS = {"50kB": 50_000, "75kB": 75_000, "112kB": 112_000}
CHUNK = 384                              # uplink chunk: 384 base64 chars = 288 B
EFFORT = 5
# bmcam003/004 field profile (bm_cam_legacy development device_profiles/bmcam003/
# camera_schedule.yaml:101-108): the ladder overrides the q_max 15 fallback
LADDER = (90, 80, 70, 60, 50, 40, 30, 25, 20, 15, 13, 11, 9)
MESSAGE_CAP = 195
# (preset, id, label, region x, y, w, h in native px, mode)
#   mode "native": a native crop; "binplanes": full frame, each plane 2×2-averaged on the Pi
#   (study flags bit 16 → CONTAINER v2); "binmode": the sensor's 2304×1296 binned mode,
#   simulated by 2×2-averaging each plane and re-mosaicking (a plain v1 mosaic).
CANDIDATES = [
    ("WIDE", "W-full", "4608×2592 native", (0, 0, 4608, 2592), "native"),
    ("WIDE", "W-binplanes", "4608×2592, planes binned 2×2 on the Pi", (0, 0, 4608, 2592),
     "binplanes"),
    ("WIDE", "W-binmode", "2304×1296 binned sensor mode (simulated)", (0, 0, 4608, 2592),
     "binmode"),
    ("MEDIUM", "M-1600", "1600×900 native, today's centred view", TODAY, "native"),
    ("MEDIUM", "M-1920", "1920×1080 native, slightly wider view (centred, contains today's)",
     (1344, 756, 1920, 1080), "native"),
    ("MEDIUM", "M-2000", "2000×1124 native, slightly wider view (centred, contains today's)",
     (1304, 734, 2000, 1124), "native"),
    ("HIGH", "H-1000", "1000×562 native, on the card's colour patches (centred would be "
     "1804,1014)", (2072, 944, 1000, 562), "native"),
    ("HIGH", "H-800", "800×450 native, on the card's colour patches (centred would be "
     "1904,1070)", (2172, 1000, 800, 450), "native"),
]
APRIL = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)


def load_prod(prod: Path):
    sys.path.insert(0, str(prod))
    import rc_jpeg_encoder
    import rc_raw_jxl
    return rc_raw_jxl, rc_jpeg_encoder


def mac_runner(cmd, *, timeout_s, stdout_path, stderr_path, poll_s=0.05):
    """rc_raw_jxl.run_capped's contract, without the Linux RLIMIT guard (Mac desk run)."""
    t0 = time.monotonic()
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        r = subprocess.run(cmd, stdout=out, stderr=err, timeout=timeout_s)
    return {"rc": r.returncode, "kind": "ok" if r.returncode == 0 else "enc",
            "seconds": round(time.monotonic() - t0, 3), "peak_rss_kb": 0}


# ------------------------------------------------------------------ geometry helpers

def bin2(p: np.ndarray) -> np.ndarray:
    h, w = p.shape[0] // 2 * 2, p.shape[1] // 2 * 2
    return np.floor(p[:h, :w].astype(np.float64).reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
                    + 0.5).astype(np.uint16)


def up2_planes(mosaic: np.ndarray, cfa: str, full_hw) -> np.ndarray:
    """A half-resolution mosaic (binned) → full-resolution mosaic by bilinear plane upsample."""
    H, W = full_hw
    planes = split(mosaic.astype(np.float32), cfa)
    up = {k: cv2.resize(v, (W // 2, H // 2), interpolation=cv2.INTER_LINEAR)
          for k, v in planes.items()}
    return merge(up, cfa)


# ------------------------------------------------------------------ encoders

def nrjxl_blob(rc, crop: dict, codes: dict, params_meta: dict, distance: float, mode: str,
               crop_xywh, native_wh, work: Path):
    payloads, runs = rc.encode_rung(codes, distance, EFFORT, str(work), cjxl=tool("cjxl"),
                                    runner=mac_runner, timeout_s=600)
    h, w = crop["mosaic"].shape
    params = rc.build_params(crop_xywh=crop_xywh, native_wh=native_wh, crc=0,
                             colour=params_meta, distance=distance, effort=EFFORT)
    if mode == "binplanes":  # study flags bit 16: planes are half size → CONTAINER v2
        blob = bytearray(rc.MAGIC)
        blob += bytes([rc.METHOD_D2, rc.FLAGS_4PL_SQRT | 16, rc.CFA_PATTERNS.index(crop["cfa"]),
                       rc.CODE_BITS])
        for n in (w, h, crop["black"], crop["white"], 0, len(params)):
            blob += rc._uvarint(n)
        for p in params:
            blob += rc._uvarint(rc._zz(int(p)))
        blob += rc._uvarint(4)
        for p in payloads:
            blob += rc._uvarint(len(p))
        return bytes(blob) + b"".join(payloads)
    blob, _ = rc.seal_container(w=w, h=h, cfa=crop["cfa"], black=crop["black"],
                                white=crop["white"], params=params, payloads=payloads)
    return blob


def search_distance(make_blob, target: int, cache: dict) -> tuple[float, bytes, bool]:
    """Bisection in log(distance) on the production range 0.1–15 for ≤ target bytes."""
    def size(d):
        d = round(d, 3)
        if d not in cache:
            cache[d] = make_blob(d)
        return len(cache[d])
    lo, hi = math.log(0.1), math.log(15.0)
    if size(15.0) > target:
        return 15.0, cache[15.0], False
    best = None
    for _ in range(14):
        d = math.exp((lo + hi) / 2)
        n = size(d)
        if n <= target and (best is None or n > len(cache[round(best, 3)])):
            best = d
        if abs(n / target - 1) <= 0.02 and n <= target:
            break
        if n > target:
            lo = math.log(d)
        else:
            hi = math.log(d)
    best = best if best is not None else 15.0
    return round(best, 3), cache[round(best, 3)], True


def pjpg_today(rc_jpeg, jpeg_path: Path):
    src = rc_jpeg.prepare_source(str(jpeg_path), TODAY, 1000)
    for q in LADDER:
        enc = rc_jpeg.encode_progressive(src, q, CHUNK)
        if enc["message_count"] <= MESSAGE_CAP:
            break
    return enc


# ------------------------------------------------------------------ rendering and metrics

def render_linear(mosaic: np.ndarray, black: int, white: int, cfa: str, wb, box) -> np.ndarray:
    x, y, w, h = box
    lin = ((mosaic[y:y + h, x:x + w].astype(np.float32) - black) / (white - black))
    return demosaic_bilinear(lin, cfa).astype(np.float32) * np.asarray(wb, np.float32)


def to_srgb8(lin: np.ndarray) -> np.ndarray:
    return (np.clip(lin, 0, 1) ** (1 / 2.2) * 255 + 0.5).astype(np.uint8)


def from_srgb8(img: np.ndarray) -> np.ndarray:
    return (img.astype(np.float32) / 255) ** 2.2


def tone_match(lin: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Per-channel affine fit in linear light (camera ISP vs RAW render)."""
    out = np.empty_like(lin)
    for c in range(3):
        a, b = np.polyfit(lin[..., c].ravel()[::7], ref[..., c].ravel()[::7], 1)
        out[..., c] = a * lin[..., c] + b
    return out


def luma(img8: np.ndarray) -> np.ndarray:
    return (img8.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32))


def tag_stats(ref8: np.ndarray, img8: np.ndarray) -> dict:
    """AprilTags found on the RAW reference: detected on the candidate too? edge acutance and
    Michelson contrast inside each tag, candidate relative to RAW."""
    det = cv2.aruco.ArucoDetector(APRIL, cv2.aruco.DetectorParameters())
    g_ref = luma(ref8).astype(np.uint8)
    corners, ids, _ = det.detectMarkers(g_ref)
    if ids is None:
        return {"tags_ref": 0, "tags_found": None, "acutance_rel": None, "contrast_rel": None}
    g_img = luma(img8).astype(np.uint8)
    _, ids2, _ = det.detectMarkers(g_img)
    found = 0 if ids2 is None else len(set(ids.ravel()) & set(ids2.ravel()))
    acu, con = [], []
    for c in corners:
        q = c.reshape(4, 2)
        x0, y0 = np.floor(q.min(0)).astype(int)
        x1, y1 = np.ceil(q.max(0)).astype(int)
        if x0 < 2 or y0 < 2 or x1 > g_ref.shape[1] - 3 or y1 > g_ref.shape[0] - 3:
            continue
        a, b = g_ref[y0:y1, x0:x1].astype(np.float32), g_img[y0:y1, x0:x1].astype(np.float32)

        def stat(p, mask):
            lo, hi = np.percentile(p, 10), np.percentile(p, 90)
            gx, gy = np.gradient(p)
            return np.hypot(gx, gy)[mask].mean() / max(hi - lo, 1), (hi - lo) / max(hi + lo, 1)
        gxr, gyr = np.gradient(a)
        edges = np.hypot(gxr, gyr) > np.percentile(np.hypot(gxr, gyr), 85)
        ar, cr = stat(a, edges)
        ai, ci = stat(b, edges)
        acu.append(ai / ar)
        con.append(ci / cr)
    return {"tags_ref": int(len(ids)), "tags_found": int(found),
            "acutance_rel": float(np.mean(acu)) if acu else None,
            "contrast_rel": float(np.mean(con)) if con else None}


def patch_de(ref_lin: np.ndarray, img_lin: np.ndarray, quads, box) -> float | None:
    """Mean ΔE00 between patch means (display-linear, after the same WB) inside the box."""
    x0, y0, w, h = box
    des = []
    for q in quads:
        q = np.asarray(q) * 2 - [x0, y0]  # plane → native, relative to the box
        if q.min() < 0 or q[:, 0].max() >= w or q[:, 1].max() >= h:
            continue
        m = np.zeros((h, w), np.uint8)
        cv2.fillPoly(m, [np.round(q).astype(np.int32)], 1)
        m = m.astype(bool)
        if m.sum() < 50:
            continue
        a, b = ref_lin[m].mean(0), img_lin[m].mean(0)
        des.append(float(delta_e2000(linear_to_lab(np.clip(a, 0, None)),
                                     linear_to_lab(np.clip(b, 0, None)))))
    return float(np.mean(des)) if des else None


def inside(quad, box, margin=4) -> bool:
    q = np.asarray(quad) * 2
    x, y, w, h = box
    return (q[:, 0].min() >= x + margin and q[:, 1].min() >= y + margin
            and q[:, 0].max() <= x + w - margin and q[:, 1].max() <= y + h - margin)


def common_patches(roi: dict, boxes) -> list[str]:
    """Patches fully inside every box (every variant of a preset, and today's pjpg)."""
    return [pid for pid, p in roi["patches"].items() if all(inside(p["quad"], b) for b in boxes)]


def patch_means(lin: np.ndarray, roi: dict, ids, box) -> dict:
    x0, y0, w, h = box
    out = {}
    for pid in ids:
        q = np.asarray(roi["patches"][pid]["quad"]) * 2 - [x0, y0]
        m = np.zeros((h, w), np.uint8)
        cv2.fillPoly(m, [np.round(q).astype(np.int32)], 1)
        out[pid] = lin[m.astype(bool)].mean(0)
    return out


def de_vs_raw(ref_m: dict, img_m: dict) -> float | None:
    des = [float(delta_e2000(linear_to_lab(np.clip(ref_m[k], 0, None)),
                             linear_to_lab(np.clip(img_m[k], 0, None)))) for k in ref_m]
    return float(np.mean(des)) if des else None


def de_vs_truth(img_m: dict, truth: dict) -> float | None:
    """The cloud step: a 3×3 colour matrix fitted (least squares) from this variant's card
    patch means to the card's measured truth, then mean ΔE2000 vs the truth."""
    ids = [k for k in img_m if k in truth]
    if len(ids) < 4:
        return None
    A = np.array([img_m[k] for k in ids])
    T = np.array([truth[k] for k in ids])
    M, *_ = np.linalg.lstsq(A, T, rcond=None)
    P = A @ M
    return float(np.mean([delta_e2000(linear_to_lab(np.clip(p, 0, None)), linear_to_lab(t))
                          for p, t in zip(P, T)]))


def fm(v) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def overlap(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


def verdict(c: dict, base: dict) -> str:
    """No worse than today's pjpg: SSIM, patch ΔE, tag acutance (5 % tolerance → WARN)."""
    worse = []
    if c["ssim"] < base["ssim"]:
        worse.append((1 - c["ssim"]) / max(1 - base["ssim"], 1e-6) - 1)
    for k in ("patch_de", "de_truth"):  # colour: vs RAW, and after the card fit vs truth
        if c.get(k) is not None and base.get(k) is not None and c[k] > base[k]:
            worse.append(c[k] / max(base[k], 1e-6) - 1)
    # detail: closeness of tag edge acutance to RAW (> 1 = sharpening / ringing, < 1 = blur)
    if c["acutance_rel"] is not None and base["acutance_rel"] is not None:
        dc, db = abs(c["acutance_rel"] - 1), abs(base["acutance_rel"] - 1)
        if dc > db:
            worse.append((dc - db) / max(db, 0.02))
    if not worse:
        return "PASS"
    return "WARN" if max(worse) <= 0.05 else "FAIL"


# ------------------------------------------------------------------ main loop

def run(args) -> int:
    rc, rc_jpeg = load_prod(Path(args.prod))
    from nereus_camera_test_rig.color.card import load_card
    from nereus_camera_test_rig.color.metrics import srgb8_to_linear
    card = load_card(rois.CARD)
    truth = {p.id: srgb8_to_linear(np.asarray(p.truth, float)) for p in card.patches}
    data = Path(args.data)
    run_id = f"s28_presets_{time.strftime('%Y%m%d')}"
    out = Path(args.out) / run_id
    (out / "renders").mkdir(parents=True, exist_ok=True)
    roi_all = rois.load(REPO / "compression_study" / "config" / "card_rois.yaml")
    rows, dist_rows, patch_sets = [], [], {}
    for lamp in args.lamps.split(","):
        dng = data / f"{lamp}_imx708" / "stop_-1_r0.dng"
        meta = json.loads((data / f"{lamp}_imx708" / "stop_-1_r0.json").read_text())
        jpg = data / f"{lamp}_imx708" / "stop_-1_r0.jpg"
        full = rc.read_dng_crop(str(dng), (0, 0, 4608, 2592))
        mos, cfa, black, white = full["mosaic"], full["cfa"], full["black"], full["white"]
        roi = roi_all[f"imx708_{lamp}"]
        gq = np.asarray(roi["patches"]["gray_mid"]["quad"]) * 2
        gx0, gy0 = np.floor(gq.min(0)).astype(int)
        gx1, gy1 = np.ceil(gq.max(0)).astype(int)
        g = render_linear(mos, black, white, cfa, (1, 1, 1), (gx0, gy0, gx1 - gx0, gy1 - gy0))
        g = g.reshape(-1, 3).mean(0)
        wb = g[1] / g  # one white balance for every render: grey 128 of the original
        colour = rc.colour_params(meta)
        enc = pjpg_today(rc_jpeg, jpg)
        pj = np.asarray(Image.open(io.BytesIO(enc["jpeg_data"])).convert("RGB"))
        pj_up = np.asarray(Image.fromarray(pj).resize(TODAY[2:], Image.Resampling.LANCZOS))
        ref_today = render_linear(mos, black, white, cfa, wb, TODAY)
        pj_lin_today = tone_match(from_srgb8(pj_up), ref_today)
        print(f"[{lamp}] today's pjpg q{enc['quality']} {enc['jpeg_bytes']} B "
              f"{enc['message_count']} msgs", flush=True)
        for preset in ("WIDE", "MEDIUM", "HIGH"):
            cands = [c for c in CANDIDATES if c[0] == preset]
            regions = [c[3] for c in cands]
            comp = TODAY if preset != "HIGH" else (2172, 1000, 800, 450)  # the smallest HIGH
            ids = common_patches(roi, regions + [TODAY, comp])
            patch_sets[preset] = ids
            ref_lin = render_linear(mos, black, white, cfa, wb, comp)
            ref8 = to_srgb8(ref_lin)
            ref_m = patch_means(ref_lin, roi, ids, comp)
            bx, by = comp[0] - TODAY[0], comp[1] - TODAY[1]
            pj_lin = pj_lin_today[by:by + comp[3], bx:bx + comp[2]]
            pj8 = to_srgb8(pj_lin)
            # the cloud step on today's pjpg: decoded sRGB → linear, then the card fit (no
            # tone-match: the card fit is the correction)
            pj_raw_lin = from_srgb8(pj_up)[by:by + comp[3], bx:bx + comp[2]]
            base = {"ssim": ssim(luma(ref8), luma(pj8)),
                    "patch_de": de_vs_raw(ref_m, patch_means(pj_lin, roi, ids, comp)),
                    "de_truth": de_vs_truth(patch_means(pj_raw_lin, roi, ids, comp), truth),
                    **tag_stats(ref8, pj8)}
            raw_truth = de_vs_truth(ref_m, truth)
            common = {"lamp": lamp, "preset": preset, "compare_box": list(comp),
                      "patches": len(ids)}
            rows.append({**common, "candidate": "today_pjpg", "label":
                         f"today's pjpg q{enc['quality']} (1600×900 → 1000×562)",
                         "target": "today", "bytes": enc["jpeg_bytes"],
                         "messages": enc["message_count"], "quality": enc["quality"], **base,
                         "verdict": "baseline"})
            rows.append({**common, "candidate": "raw_reference", "label": "RAW reference",
                         "target": "raw", "bytes": comp[2] * comp[3] * 10 // 8, "ssim": 1.0,
                         "patch_de": 0.0, "de_truth": raw_truth, "verdict": "reference"})
            if lamp == args.render_lamp:
                save_patch_zoom(out / "renders", preset, "ref", ref8, roi, ids, comp)
                save_patch_zoom(out / "renders", preset, "pjpg", to_srgb8(pj_lin), roi, ids, comp)
                Image.fromarray(ref8).save(out / "renders" / f"{preset}_ref_cmp.webp", "WEBP",
                                           lossless=True)
                Image.fromarray(pj8).save(out / "renders" / f"{preset}_pjpg_cmp.webp", "WEBP",
                                          lossless=True)
            for _, cid, label, region, mode in cands:
                x, y, w, h = region
                if mode == "native":
                    crop = rc.read_dng_crop(str(dng), region)
                    native_wh, xywh = (4608, 2592), region
                    codes = rc.code_planes(crop)
                else:
                    planes = {k: bin2(v) for k, v in split(mos, cfa).items()}
                    bm = merge(planes, cfa, dtype=np.uint16)
                    codes = rc.code_planes({**full, "mosaic": bm})
                    if mode == "binmode":
                        crop, native_wh, xywh = {**full, "mosaic": bm}, (2304, 1296), (0, 0, 2304, 1296)
                    else:
                        crop, native_wh, xywh = {**full, "mosaic": mos}, (4608, 2592), (0, 0, 4608, 2592)
                cache: dict = {}
                with tempfile.TemporaryDirectory(prefix="s28p_") as td:
                    make = lambda d: nrjxl_blob(rc, crop, codes, colour, d, mode, xywh,  # noqa: E731
                                                native_wh, Path(td))
                    targets = {"today_size": enc["jpeg_bytes"], "17kB": 16_700, **TARGETS}
                    for tname, tbytes in targets.items():
                        t0 = time.perf_counter()
                        d, blob, ok = search_distance(make, tbytes, cache)
                        rec = rp.decode(blob)
                        if mode == "binmode":
                            rec = up2_planes(rec, cfa, (2592, 4608))
                        elif mode == "native":
                            canvas = np.zeros((2592, 4608))
                            canvas[y:y + h, x:x + w] = rec
                            rec = canvas
                        img_lin = render_linear(rec, black, white, cfa, wb, comp)
                        img8 = to_srgb8(img_lin)
                        im = patch_means(img_lin, roi, ids, comp)
                        m = {"ssim": ssim(luma(ref8), luma(img8)), "patch_de": de_vs_raw(ref_m, im),
                             "de_truth": de_vs_truth(im, truth), **tag_stats(ref8, img8)}
                        row = {**common, "candidate": cid, "label": label, "target": tname,
                               "bytes": len(blob), "messages": rc.message_count(len(blob), CHUNK),
                               "distance": d, "fits": ok, **m,
                               "container_v2": mode == "binplanes",
                               "sha256": hashlib.sha256(blob).hexdigest()[:16],
                               "search_s": round(time.perf_counter() - t0, 1)}
                        row["verdict"] = verdict(row, base)
                        rows.append(row)
                        print(f"[{lamp}] {cid:12s} {tname:6s} d={d:6.3f} {len(blob):6d} B "
                              f"{row['messages']:3d} msg ssim {m['ssim']:.3f}/{base['ssim']:.3f} "
                              f"dE {fm(m['patch_de'])}/{fm(base['patch_de'])} dEtruth "
                              f"{fm(m['de_truth'])}/{fm(base['de_truth'])} (raw {fm(raw_truth)})"
                              f" acu {fm(m['acutance_rel'])}/{fm(base['acutance_rel'])} → "
                              f"{row['verdict']}", flush=True)
                        if lamp == args.render_lamp:
                            r = out / "renders"
                            Image.fromarray(img8).save(r / f"{preset}_{cid}_{tname}_cmp.webp",
                                                       "WEBP", lossless=True)
                            save_patch_zoom(r, preset, f"{cid}_{tname}", img8, roi, ids, comp)
                            if tname == "50kB":
                                save_region(r, cid, rec, mos, black, white, cfa, wb, region)
                dist_rows += [{"lamp": lamp, "candidate": cid, "distance": dd, "bytes": len(bb)}
                              for dd, bb in sorted(cache.items())]
    write_outputs(out, rows, dist_rows, args)
    (out / "patch_sets.json").write_text(json.dumps(patch_sets, indent=1))
    return 0


def save_patch_zoom(d: Path, preset, tag, img8, roi, ids, comp):
    """The card's patches in the comparison box, native px enlarged 2× (nearest)."""
    q = np.concatenate([np.asarray(roi["patches"][i]["quad"]) * 2 for i in ids
                        if roi["patches"][i]["group"] != "chart"]) - [comp[0], comp[1]]
    x0, y0 = np.maximum(np.floor(q.min(0)).astype(int) - 40, 0)
    x1 = min(int(np.ceil(q[:, 0].max())) + 40, img8.shape[1])
    y1 = min(int(np.ceil(q[:, 1].max())) + 40, img8.shape[0])
    z = Image.fromarray(img8[y0:y1, x0:x1])
    z.resize((z.width * 2, z.height * 2), Image.Resampling.NEAREST).save(
        d / f"{preset}_{tag}_patches.webp", "WEBP", lossless=True)


def save_region(d: Path, cid, rec, mos, black, white, cfa, wb, region):
    """The candidate's whole region: full resolution, lossless (opened full screen)."""
    for tag, m in ((f"{cid}_50kB_region", rec), (f"{cid}_ref_region", mos)):
        img = to_srgb8(render_linear(m, black, white, cfa, wb, region))
        Image.fromarray(img).save(d / f"{tag}.webp", "WEBP", lossless=True)


def write_outputs(out: Path, rows, dist_rows, args):
    keys = sorted({k for r in rows for k in r})
    first = ["lamp", "preset", "candidate", "label", "target", "bytes", "messages", "distance",
             "quality", "fits", "ssim", "patch_de", "tags_ref", "tags_found", "acutance_rel",
             "contrast_rel", "verdict", "container_v2", "compare_box"]
    cols = first + [k for k in keys if k not in first]
    with (out / "metrics.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.5g}" if isinstance(v, float) else v) for k, v in r.items()})
    with (out / "distances.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["lamp", "candidate", "distance", "bytes"])
        w.writeheader()
        w.writerows(dist_rows)
    commit = (Path(args.prod).parent / "COMMIT")
    manifest = {
        "run_id": out.name, "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "purpose": "Sprint28 nrjxl customer presets WIDE / MEDIUM / HIGH (Nick, 2026-10-02): "
                   "nrjxl at 50 / 75 / 112 kB vs today's pjpg and the RAW reference",
        "data": str(args.data), "frames": "stop_-1_r0 per lamp (IMX708, nereus002, 2026-09-30)",
        "production_code": {"repo": "bm_cam_legacy", "branch": "feature/sprint28-camera (PR #120)",
                            "commit": commit.read_text().strip() if commit.exists() else None,
                            "modules": ["rc_raw_jxl (read_dng_crop, code_planes, encode_rung, "
                                        "build_params, seal_container, message_count)",
                                        "rc_jpeg_encoder (prepare_source, encode_progressive)"]},
        "encoder": {"cjxl": subprocess.run([tool("cjxl"), "--version"], capture_output=True,
                                           text=True).stdout.splitlines()[0],
                    "effort": EFFORT, "mode": "modular (-m 1)", "threads": 1},
        "pjpg": {"crop": TODAY, "output_width": 1000, "ladder": LADDER, "message_cap": MESSAGE_CAP},
        "uplink": {"chunk_b64_chars": CHUNK, "bytes_per_message": 288},
        "targets_bytes": TARGETS, "candidates": [
            {"preset": p, "id": i, "label": lab, "region_native": r, "mode": m}
            for p, i, lab, r, m in CANDIDATES],
        "metrics": {"de_truth": "the cloud step: a 3x3 colour matrix fitted per variant from its "
                                "card patch means (linear) to the card's measured truth, then "
                                "mean ΔE2000 vs the truth; RAW reference = the floor",
                    "ssim": "luma SSIM vs the RAW reference render (bilinear demosaic, WB on "
                            "grey 128 of the original, gamma 2.2) at native resolution; pjpg "
                            "upsampled 1.6× (Lanczos) and tone-matched (per-channel affine in "
                            "linear light) first",
                    "patch_de": "mean ΔE2000 of card/chart patch means vs the RAW reference",
                    "acutance_rel": "AprilTag edge gradient / tag contrast, relative to RAW",
                    "verdict": "PASS if no worse than today's pjpg on SSIM, patch ΔE vs RAW, ΔE vs "
                               "card truth and tag acutance in the same box (same patch set); "
                               "WARN if worse by ≤ 5 %; FAIL beyond"},
        "rows": len(rows),
    }
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    print(f"wrote {out}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--prod", required=True, help="bm_cam_legacy export: …/BM_Devel_Pi")
    ap.add_argument("--out", default=str(REPO / "compression_study" / "presets" / "runs"))
    ap.add_argument("--lamps", default="cool,warm")
    ap.add_argument("--render-lamp", default="cool")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
