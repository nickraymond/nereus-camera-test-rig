"""Crop × budget for nrjxl over a RANGE of real scenes (Nick via EM, 2026-10-05).

    NRJXL_BM_DIR=<bm #120 export> python -m compression_study.preview_loss.reef_budget \
        --reef <bm_cam_legacy>/reference_images --tg7 <primary>/data/tg7_channel_islands \
        --out reef_budget.json [--jobs 6]

Scene sets (each result row says which):
  reef_jpeg   the 9 reference reef images (bm_cam_legacy reference_images: TG-7 camera JPEGs,
              no RAW exists). RAW-EQUIVALENT, an approximation: the Sprint06 IMX708-size stand-in
              (synthetic_native_4608x2592.jpg: 16:9 centre crop, Lanczos x1.152, q95) -> sRGB
              inverse -> inverse IMX708 CCM and WB (imx708_wide tuning at 5715 K) -> exposure so
              the brightest 0.5 % sits at 75 % of full scale -> BGGR mosaic, 10 bit, black 64 ->
              IMX708 noise sigma^2 = 0.045 v + 0.3 DN^2 (S4 repeats, base gain).
  tg7_raw     real RAW (Olympus TG-7 ORF, 12 bit, other sensor): Channel Islands kelp / reef
              scenes with no or off-centre card, at native density.
  tg7_jpeg    the same TG-7 frames through the reef_jpeg path from their camera JPEGs: measures
              how far the JPEG-derived approximation is from real RAW on the same scene.

Per scene x crop (800x450, 1600x900, 2304x1296, centred): the production encoder
(rc_raw_jxl code_planes -> cjxl -m 1 -e 5 -> seal_container) on a distance grid; bytes ->
messages with the production message_count (288 B). A budget's distance is the byte-target
point (fill 0.97) interpolated in log-log between grid points (+-1.5 % vs exact, measured).
Quality vs the RAW render of the same crop (luma SSIM and fine-detail ratio on the 1600x900
region today's pjpg covers; the 800x450 crop on its own region) — for the JPEG-derived
scenes against the noise-free scene, for real RAW against its own render — and today's pjpg (production
rc_jpeg_encoder: 1600x900 -> 1000x562 Lanczos, q ladder under 195 messages, tone-matched).
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from compression_study import common
from compression_study.metrics import ssim
from compression_study.preview_loss import codec as C

DISTANCES = (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0, 8.0, 9.0, 10.4, 12.0, 15.0)
CROPS = {"800x450": (800, 450), "1600x900": (1600, 900), "2304x1296": (2304, 1296)}
BUDGETS = (180, 195, 250, 300, 500)
FILL = 0.97
PJPG_LADDER = (90, 80, 70, 60, 50, 40, 30, 25, 20, 15, 13, 11, 9)
PJPG_CAP = 195
# IMX708 inverse-ISP parameters (imx708_wide tuning, ct_curve + ccm nearest 5715 K)
CT_RB = (0.4668, 0.5898)                       # grey R/G, B/G at 5715 K
NOISE_A, NOISE_C = 0.045, 0.3                  # sigma^2 = a v + c (DN, 10 bit, base gain)
EXPOSE_P, EXPOSE_LEVEL = 99.5, 0.75
TG7_SKIP = {"P9160606", "P9160616", "P9160617", "P9150420"}   # torch / flash frames


def tuning_ccm(path: Path, ct: float = 5715.0) -> np.ndarray:
    t = json.loads(path.read_text())
    ccms = next(a for a in t["algorithms"] if "rpi.ccm" in a)["rpi.ccm"]["ccms"]
    c = min(ccms, key=lambda x: abs(x["ct"] - ct))
    return np.asarray(c["ccm"], np.float64).reshape(3, 3)


# ------------------------------------------------------------------ scenes

def synth_raw(srgb8: np.ndarray, ccm: np.ndarray, seed: int) -> dict:
    """sRGB image -> IMX708-like BGGR 10-bit mosaic (RAW-equivalent approximation)."""
    lin = common.srgb_eotf(srgb8.astype(np.float64) / 255.0)
    cam = lin @ np.linalg.inv(ccm).T                       # undo the colour matrix
    cam = cam * np.array([CT_RB[0], 1.0, CT_RB[1]])        # undo the WB gains
    cam = np.clip(cam, 0, None)
    cam *= EXPOSE_LEVEL / max(float(np.percentile(cam.max(axis=2), EXPOSE_P)), 1e-6)
    h, w = cam.shape[:2]
    mos = np.empty((h, w))
    mos[0::2, 0::2] = cam[0::2, 0::2, 2]                   # B
    mos[0::2, 1::2] = cam[0::2, 1::2, 1]                   # G
    mos[1::2, 0::2] = cam[1::2, 0::2, 1]                   # G
    mos[1::2, 1::2] = cam[1::2, 1::2, 0]                   # R
    v = np.clip(mos, 0, 1) * (1023 - 64)
    clean = (v + 64).astype(np.float32)               # the noise-free scene (reference)
    rng = np.random.default_rng(seed)
    v = v + rng.normal(0, 1, v.shape) * np.sqrt(NOISE_A * v + NOISE_C)
    dn = np.clip(np.round(v + 64), 0, 1023).astype(np.uint16)
    return {"mosaic": dn, "clean": clean, "cfa": "BGGR", "black": 64, "white": 1023,
            "gains": (1 / CT_RB[0], 1.0, 1 / CT_RB[1]), "ccm": ccm}


def prepare_16x9(jpeg: Path) -> np.ndarray:
    """bm_cam_legacy tools/prepare_reference_images.py: centre 16:9, Lanczos to 4608x2592."""
    im = Image.open(jpeg).convert("RGB")
    W, H = im.size
    h = round(W * 9 / 16)
    top = (H - h) // 2
    return np.asarray(im.crop((0, top, W, top + h)).resize((4608, 2592), Image.Resampling.LANCZOS))


def tg7_raw(orf: Path) -> dict:
    from host_tools.tg7.orf_io import read_orf
    f = read_orf(orf)
    raw, cfa, black = f.active()
    wb = f.as_shot_wb or (2.0, 1.0, 1.6)
    m = np.array([[420, -164, 0], [-64, 392, -72], [8, -112, 360]], np.float64) / 256
    return {"mosaic": np.ascontiguousarray(raw), "cfa": cfa, "black": int(round(float(np.mean(black)))),
            "white": int(f.white_level), "gains": wb, "ccm": m}


# ------------------------------------------------------------------ rendering / metrics

def render_lin(mosaic, scene) -> np.ndarray:
    from nereus_camera_test_rig.color.raw_io import demosaic_bilinear
    lin = (mosaic.astype(np.float32) - scene["black"]) / (scene["white"] - scene["black"])
    rgb = demosaic_bilinear(np.clip(lin, 0, 1), scene["cfa"]) * np.asarray(scene["gains"], np.float32)
    return np.clip(np.minimum(rgb, 1.0) @ np.asarray(scene["ccm"], np.float32).T, 0, None)


def to8(lin, scale):
    return np.round(common.srgb_oetf(np.clip(lin * scale, 0, 1)) * 255).astype(np.uint8)


def luma(u8):
    return u8.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)


def detail(img8, ref8) -> float:
    """Fine-detail ratio: std of the luma Laplacian (sigma 1) vs the reference's."""
    def hp(x):
        return cv2.Laplacian(cv2.GaussianBlur(luma(x), (0, 0), 1.0), cv2.CV_32F)
    return float(hp(img8).std() / max(hp(ref8).std(), 1e-6))


def metrics(img8, ref8) -> dict:
    return {"ssim": round(ssim(luma(img8), luma(ref8)), 4), "detail": round(detail(img8, ref8), 3)}


def tone_match(lin, ref):
    out = np.empty_like(lin)
    for c in range(3):
        a, b = np.polyfit(lin[..., c].ravel()[::7], ref[..., c].ravel()[::7], 1)
        out[..., c] = a * lin[..., c] + b
    return out


# ------------------------------------------------------------------ one scene

def nrjxl_bytes_and_decode(scene, box, d):
    rc = C.rc()
    x, y, w, h = box
    crop = {"mosaic": scene["mosaic"][y:y + h, x:x + w], "cfa": scene["cfa"],
            "black": scene["black"], "white": scene["white"]}
    codes = rc.code_planes(crop)
    pl = [C.encode(codes[n], 4095, d) for n in ("R", "G1", "G2", "B")]
    meta = rc.colour_params({"ExposureTime": 1000, "AnalogueGain": 1.12, "ColourGains": [2.1, 1.7],
                             "ColourCorrectionMatrix": [1, 0, 0, 0, 1, 0, 0, 0, 1]})
    params = rc.build_params(crop_xywh=(x, y, w, h), native_wh=scene["mosaic"].shape[::-1], crc=0,
                             colour=meta, distance=d, effort=C.EFFORT)
    blob, _ = rc.seal_container(w=w, h=h, cfa=scene["cfa"], black=scene["black"],
                                white=scene["white"], params=params, payloads=pl)
    dec = {n: C.decode(b).astype(np.float64) for n, b in zip(("R", "G1", "G2", "B"), pl)}
    s = (2 ** 12 - 1) / math.sqrt(scene["white"] - scene["black"])
    lin = {n: (v / s) ** 2 + scene["black"] for n, v in dec.items()}
    return len(blob), common.merge(lin, scene["cfa"])


def pjpg_today(isp8: np.ndarray, box) -> tuple[np.ndarray, dict]:
    """Production still path on the ISP stand-in image (rc_jpeg_encoder)."""
    import sys
    sys.path.insert(0, os.environ["NRJXL_BM_DIR"])
    import rc_jpeg_encoder as rj
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "native.jpg"
        Image.fromarray(isp8).save(p, quality=95, subsampling=0)
        src = rj.prepare_source(str(p), box, 1000, native_size=isp8.shape[1::-1])
    for q in PJPG_LADDER:
        enc = rj.encode_progressive(src, q, 384)
        if enc["message_count"] <= PJPG_CAP:
            break
    data = enc["jpeg_data"]
    if not isinstance(data, (bytes, bytearray)):
        raise KeyError(f"encode_progressive keys: {list(enc)}")
    img = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
    return img, {"quality": q, "messages": enc["message_count"], "bytes": len(data)}


def run_scene(job: dict) -> dict:
    t0 = time.time()
    kind, name = job["kind"], job["name"]
    if kind == "tg7_raw":
        scene = tg7_raw(Path(job["path"]))
    else:
        scene = synth_raw(prepare_16x9(Path(job["path"])) if job.get("prepare") else
                          np.asarray(Image.open(job["path"]).convert("RGB")),
                          tuning_ccm(Path(job["tuning"])), job["seed"])
    H, W = scene["mosaic"].shape
    boxes = {k: ((W - w) // 2 // 2 * 2, (H - h) // 2 // 2 * 2, w, h) for k, (w, h) in CROPS.items()}
    today = boxes["1600x900"]
    # reference: the noise-free scene where it exists (synthetic), else the RAW render; the
    # pjpg's ISP stand-in is the same image (a noise-free / RAW-rendered camera JPEG)
    ref_lin = render_lin(scene.get("clean", scene["mosaic"]), scene)
    scale = 0.9 / max(float(np.percentile(ref_lin[..., 1], 99.5)), 1e-6)
    ref8 = to8(ref_lin, scale)

    def region(img8_full_or_crop, box, target):
        """Cut `target` (a box in native px) out of an image covering `box`."""
        x0, y0 = target[0] - box[0], target[1] - box[1]
        return img8_full_or_crop[y0:y0 + target[3], x0:x0 + target[2]]

    ref_today = region(ref8, (0, 0, W, H), today)

    def pjpg_scored(isp8):
        pj, pjm = pjpg_today(isp8, today)
        up = np.asarray(Image.fromarray(pj).resize(today[2:], Image.Resampling.LANCZOS))
        lin = tone_match(common.srgb_eotf(up / 255.0), common.srgb_eotf(ref_today / 255.0))
        return np.round(common.srgb_oetf(np.clip(lin, 0, 1)) * 255).astype(np.uint8), pjm

    # today's pjpg from an ISP stand-in: the noise-free scene (an ideal denoising ISP) and,
    # for the synthetic scenes, the noisy RAW render (no denoise) -> the real ISP lies between
    pj8, pjm = pjpg_scored(ref8)
    out = {"kind": kind, "name": name, "path": job["path"], "native": [W, H],
           "reference": "noise-free scene" if "clean" in scene else "RAW render",
           "pjpg": {**pjm, **metrics(pj8, ref_today)}, "crops": {}}
    if "clean" in scene:
        noisy8 = to8(render_lin(scene["mosaic"], scene), scale)
        pjn8, pjnm = pjpg_scored(noisy8)
        out["pjpg_noisy_isp"] = {**pjnm, **metrics(pjn8, ref_today)}
    small = boxes["800x450"]
    out["pjpg_small_region"] = metrics(region(pj8, today, small), region(ref8, (0, 0, W, H), small))
    for cname, box in boxes.items():
        target = small if cname == "800x450" else today
        rows = []
        for d in DISTANCES:
            n, mos = nrjxl_bytes_and_decode(scene, box, d)
            img8 = to8(render_lin(mos, scene), scale)
            m = metrics(region(img8, box, target), region(ref8, (0, 0, W, H), target))
            rows.append({"d": d, "bytes": n, "messages": C.chunks(n), **m})
        out["crops"][cname] = {"box": list(box), "grid": rows}
    out["seconds"] = round(time.time() - t0, 1)
    return out


# ------------------------------------------------------------------ analysis

def _interp_logd(rows, key, d):
    ds = np.log([r["d"] for r in rows])
    return float(np.interp(math.log(d), ds, [r[key] for r in rows]))


def d_for_bytes(rows, target):
    """Byte-target distance: log-log interpolation of bytes(d) (bytes fall with d)."""
    ds = np.log([r["d"] for r in rows])[::-1]
    bs = np.log([r["bytes"] for r in rows])[::-1]
    if target < math.exp(bs[0]):
        return None                                    # nothing fits even at d 15
    return float(math.exp(np.interp(math.log(target), bs, ds)))


def msgs_for_d(rows, d):
    return math.ceil(math.exp(_interp_logd([{**r, "lb": math.log(r["bytes"])} for r in rows],
                                           "lb", d)) / C.MSG_B / FILL)


def msgs_for_quality(rows, key, bar):
    """Fewest messages (fill 0.97) whose nrjxl key >= bar (key falls with d)."""
    best = None
    fine = np.exp(np.linspace(math.log(DISTANCES[0]), math.log(DISTANCES[-1]), 200))
    for d in fine:                                     # largest d still meeting the bar
        if _interp_logd(rows, key, d) >= bar:
            best = d
    return None if best is None else msgs_for_d(rows, best)


def summarise(res: dict) -> dict:
    out = []
    for s in res["scenes"]:
        row = {"kind": s["kind"], "name": s["name"], "pjpg": s["pjpg"]}
        for cname, c in s["crops"].items():
            g = c["grid"]
            cells = {}
            for b in BUDGETS:
                d = d_for_bytes(g, FILL * b * C.MSG_B)
                cells[b] = None if d is None else {
                    "d": round(d, 2), "ssim": round(_interp_logd(g, "ssim", d), 4),
                    "detail": round(_interp_logd(g, "detail", d), 3), "floor": d > 10.4}
            bar = s["pjpg"] if cname != "800x450" else s["pjpg_small_region"]
            row[cname] = {"budgets": cells,
                          "msgs_for_d": {str(d): msgs_for_d(g, d) for d in (4.0, 4.5, 5.0, 10.4)},
                          "msgs_match_pjpg_ssim": msgs_for_quality(g, "ssim", bar["ssim"]),
                          "msgs_match_pjpg_detail": msgs_for_quality(g, "detail", bar["detail"])}
            if cname == "1600x900" and "pjpg_noisy_isp" in s:
                row[cname]["msgs_match_pjpg_noisy_isp_ssim"] = msgs_for_quality(
                    g, "ssim", s["pjpg_noisy_isp"]["ssim"])
        out.append(row)
    return {"rows": out}


def jobs(reef: Path, tg7: Path, tuning: Path, n_tg7: int) -> list[dict]:
    js = []
    for i, p in enumerate(sorted(reef.glob("reference_reef_coral_*.jpg")) + [reef / "P9011394.JPG"]):
        prep = reef / "prepared" / p.stem / "synthetic_native_4608x2592.jpg"
        if p.stem == "reference_reef_coral_primary":
            prep = reef / "prepared" / "P7071008" / "synthetic_native_4608x2592.jpg"
        src = {"path": str(prep)} if prep.exists() else {"path": str(p), "prepare": True}
        js.append({"kind": "reef_jpeg", "name": p.stem, "tuning": str(tuning), "seed": 100 + i, **src})
    import csv
    man = {r["stem"]: r for r in csv.DictReader(open(tg7 / "manifest.csv"))}
    pick = []
    for cat in ("3_scene_card_offcenter", "4_no_card"):
        stems = [p.stem for p in sorted((tg7 / "raw" / cat).glob("*.orf"))
                 if p.stem not in TG7_SKIP and man.get(p.stem, {}).get("capture_mode") == "A_iso100"]
        k = max(1, len(stems) // (n_tg7 // 2))
        pick += [(cat, s) for s in stems[::k][: n_tg7 // 2]]
    for j, (cat, s) in enumerate(pick):
        js.append({"kind": "tg7_raw", "name": s, "path": str(tg7 / "raw" / cat / f"{s}.orf")})
        js.append({"kind": "tg7_jpeg", "name": s, "path": str(tg7 / "raw" / cat / f"{s}.JPG"),
                   "prepare": True, "tuning": str(tuning), "seed": 500 + j})
    return js


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reef", type=Path, required=True)
    ap.add_argument("--tg7", type=Path, required=True)
    ap.add_argument("--tuning", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--n-tg7", type=int, default=12)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    js = jobs(a.reef, a.tg7, a.tuning, a.n_tg7)
    if a.only:
        js = [j for j in js if j["name"] in a.only or j["kind"] in a.only]
    res = {"distances": DISTANCES, "crops": CROPS, "budgets": BUDGETS, "fill": FILL,
           "noise": [NOISE_A, NOISE_C], "scenes": []}
    with ProcessPoolExecutor(a.jobs) as ex:
        for r in ex.map(run_scene, js):
            res["scenes"].append(r)
            print(r["kind"], r["name"], r["seconds"], "s", flush=True)
            a.out.write_text(json.dumps(res))
    res["summary"] = summarise(res)
    a.out.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
