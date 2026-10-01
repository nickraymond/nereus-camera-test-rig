"""Run the raw compression study end to end (Phase 1, offline).

    python -m compression_study.run_study --data <primary>/data/s4_20260930 [--jobs 4] [--quick]

Writes ``compression_study/work/`` (rows per frame set, crops, ROI overlays) and
``compression_study/results/`` (results.csv, versions.json, inventory.json, report.html).
Re-running skips frame sets whose rows already exist (``--force`` to redo).

Frame sets = camera × illuminant × stop × condition. Condition ``air`` is last night's frame
as captured; ``uw`` is the same frames red-starved at capture by binomial thinning
(``sim.py``). Primary sets (stop −1, unclipped) run every method; stop 0 runs the
pre-registered variants only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "src"), str(REPO)]  # this checkout's code, not the venv's editable
from compression_study.common import STUDY_LIB  # noqa: E402

sys.path.insert(0, str(STUDY_LIB))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from compression_study import metrics, noise, rate, rois, sim  # noqa: E402
from compression_study.common import Raw, from_rawframe, split  # noqa: E402
from compression_study.methods import isp, lin_jxl, packer  # noqa: E402
from compression_study.methods import plane_codecs as pc  # noqa: E402
from compression_study.methods import raw_planes as rp  # noqa: E402
from nereus_camera_test_rig.color.raw_io import (  # noqa: E402
    cfa_shift,
    demosaic_bilinear,
    read_dng,
    read_openmv_bayer,
)

STUDY = REPO / "compression_study"
CAMERAS = {  # name: (folder suffix, extension, reader, noise-fit vmin DN, dead zone DN)
    "imx708": ("imx708", "dng", read_dng, 2.0, 0.0),
    "n6": ("n6", "bayer", read_openmv_bayer, 10.0, 1.0),
    "ae3": ("ae3", "bayer", read_openmv_bayer, 10.0, 1.0),
}
ILLUMINANTS = ("cool", "warm")
BPP_TARGETS = {"T1": 0.4, "T2": 0.8, "T3": 1.6}
ABS_TARGETS = {"imx708": {"50kB": 50_000}}
D2_MODES = ("vardct", "modular", "vardct_np", "modular_np")
PREREG = {"D": "D/eq", "D2": "D2/eq"}  # pre-registered raw variants (chosen before results)
FIELD_CROP = (1600, 900)  # bmcam001 recipe: 1600×900 crop → 1000×562 JPEG at ~50 kB
FIELD_OUT = (1000, 562)
FIELD_BYTES = 50_000


# ------------------------------------------------------------------ inputs

def frame_path(data: Path, cam: str, ill: str, stop: int, rep: int) -> Path:
    folder, ext = CAMERAS[cam][0], CAMERAS[cam][1]
    return data / f"{ill}_{folder}" / f"stop_{stop:+d}_r{rep}.{ext}"


def load(data: Path, cam: str, ill: str, stop: int, rep: int) -> Raw:
    return from_rawframe(CAMERAS[cam][2](frame_path(data, cam, ill, stop, rep)), cam)


def verify_inputs(data: Path, used: list[Path]) -> dict:
    """SHA-256 of every input, checked against the copy's SHA256SUMS (never modified)."""
    sums = {}
    for line in (data / "SHA256SUMS").read_text().splitlines():
        h, name = line.split(maxsplit=1)
        sums[name.removeprefix("./")] = h
    out = {}
    for p in used:
        rel = str(p.relative_to(data))
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        if sums.get(rel) != h:
            raise SystemExit(f"input {rel}: sha256 {h[:12]} does not match SHA256SUMS")
        out[rel] = h
    return out


def grey_wb(raw: Raw, roi: dict) -> np.ndarray:
    """Spec-M1 white balance: linear multipliers making grey 128 neutral (G = 1)."""
    pl = split(raw.mosaic.astype(np.float64), raw.cfa)
    m = rois.mask(roi["patches"]["gray_mid"]["quad"], pl["R"].shape)
    v = np.array([pl["R"][m].mean(), (pl["G1"][m].mean() + pl["G2"][m].mean()) / 2,
                  pl["B"][m].mean()]) - raw.black
    return v[1] / np.maximum(v, 1e-6)


# ------------------------------------------------------------------ rows

def _row(fs: dict, method: str, variant: str, family: str, target: str, tbytes, blob: bytes,
         recon: np.ndarray, ctx, raw: Raw, enc_s, dec_s, knob, note: str = "",
         lossless_expected: bool = False) -> dict:
    m = metrics.evaluate(ctx, recon)
    n = len(blob) if blob is not None else None
    row = {**fs, "method": method, "variant": variant, "family": family, "target": target,
           "target_bytes": tbytes, "bytes": n, "bpp": None if n is None else n * 8 / raw.n_px,
           "hit": None if (n is None or not tbytes) else abs(n / tbytes - 1) <= rate.TOL_HIT,
           "ratio_vs16": None if not n else raw.n_px * 2 / n,
           "ratio_vs_native": None if not n else raw.n_px * raw.bits / 8 / n,
           "knob": json.dumps(knob) if knob is not None else "", "enc_s": enc_s,
           "dec_s": dec_s, "note": note, **m}
    exact = bool(np.array_equal(recon, raw.mosaic.astype(np.float64)))
    row["bit_exact"] = exact
    if lossless_expected and not exact:
        row["note"] += " FAIL: lossless method not bit-exact"
    if not lossless_expected and blob is not None and exact:
        row["note"] += " SUSPECT: lossy error is exactly 0"
    return row


def _decode_timed(fn, blob):
    t = time.perf_counter()
    rec = fn(blob)
    return rec, time.perf_counter() - t


def targets_for(cam: str, raw: Raw, quick: bool) -> dict[str, float]:
    t = {k: v * raw.n_px / 8 for k, v in BPP_TARGETS.items()}
    if quick:
        t = {"T1": t["T1"]}
    t.update(ABS_TARGETS.get(cam, {}) if not quick else {})
    return t


# ------------------------------------------------------------------ processed methods

def processed_rows(fs, raw, ctx, targets, renders, methods, crops) -> list[dict]:
    rows = []
    for method in methods:
        rgb8, wb_q, m_q = renders["fix" if method == "M1-fix" else "spec"]
        curve = rate.Curve(lambda k, m=method: isp.encode_image(rgb8, raw, m, k, wb_q, m_q))
        d, lo, hi, integer = isp.KNOBS[method]
        for tname, tbytes in targets.items():
            try:
                sol = rate.solve(curve.size, tbytes, lo, hi, d, integer, log_knob=False)
            except Exception as exc:  # noqa: BLE001 — a codec failure is a result, not a crash
                rows.append({**fs, "method": method, "variant": "", "family": "processed",
                             "target": tname, "target_bytes": tbytes,
                             "note": f"ERROR {type(exc).__name__}: {exc}"[:300]})
                continue
            for k in sol.knobs:
                blob, enc_s = curve.get(k)
                rec, dec_s = _decode_timed(isp.decode, blob)
                note = sol.note if len(sol.knobs) > 1 or not sol.reachable else ""
                rows.append(_row(fs, method, "", "processed", tname, tbytes, blob, rec, ctx, raw,
                                 enc_s, dec_s, k, note))
                if crops is not None and tname == "T1" and k == sol.knobs[0]:
                    crops[method] = rec
    return rows


# ------------------------------------------------------------------ raw-plane methods

def plane_curves(raw: Raw, spec: rp.RawSpec) -> tuple[dict[str, rate.Curve], int]:
    codes, maxval = rp.code_planes(raw, spec)
    return ({k: rate.Curve(lambda q, c=c: rp.encode_plane(c, maxval, spec, q))
             for k, c in codes.items()}, maxval)


def raw_rows(fs, raw, ctx, targets, spec: rp.RawSpec, crops, label=None) -> list[dict]:
    """eq / tiled / 3pl / binned at every target, one knob for all planes."""
    rows = []
    label = label or spec.label()
    curves, _ = plane_curves(raw, spec)
    d, lo, hi = pc.KNOBS[spec.codec]
    integer = spec.codec in ("jpeg", "jpegli")
    total = lambda k: sum(c.size(k) for c in curves.values()) + 24  # noqa: E731 (≈ header)
    for tname, tbytes in targets.items():
        sol = rate.solve(total, tbytes, lo, hi, d, integer, start=None if integer else 2.0)
        for k in sol.knobs:
            payloads = {p: c.get(k)[0] for p, c in curves.items()}
            enc_s = sum(c.get(k)[1] or 0 for c in curves.values())
            blob = rp.assemble(raw, spec, payloads)
            rec, dec_s = _decode_timed(rp.decode, blob)
            note = sol.note if len(sol.knobs) > 1 or not sol.reachable else ""
            rows.append(_row(fs, spec.method, label, "raw", tname, tbytes, blob, rec, ctx, raw,
                             enc_s, dec_s, k, note))
            if crops is not None and tname == "T1" and k == sol.knobs[0]:
                crops[label] = rec
    return rows


def redplus_rows(fs, raw, ctx, targets, spec: rp.RawSpec, factor: float, crops) -> list[dict]:
    """R plane gets ``factor`` × its equal-quality share of the bytes; G1/G2/B share the rest."""
    rows = []
    curves, _ = plane_curves(raw, replace(spec, variant="eq"))
    d, lo, hi = pc.KNOBS[spec.codec]
    integer = spec.codec in ("jpeg", "jpegli")
    total = lambda k: sum(c.size(k) for c in curves.values()) + 24  # noqa: E731
    rest = lambda k: sum(c.size(k) for p, c in curves.items() if p != "R")  # noqa: E731
    label = f"{spec.label()}/red+{factor:g}"
    for tname, tbytes in targets.items():
        eq = rate.solve(total, tbytes, lo, hi, d, integer, start=None if integer else 2.0)
        k0 = eq.knobs[0]
        share = curves["R"].size(k0) / max(total(k0), 1)
        r_t = min(0.85, factor * share) * tbytes
        sr = rate.solve(curves["R"].size, r_t, lo, hi, d, integer)
        so = rate.solve(rest, tbytes - r_t, lo, hi, d, integer)
        kr = _closest(curves["R"].size, sr.knobs, r_t)
        ko = _closest(rest, so.knobs, tbytes - r_t)
        payloads = {"R": curves["R"].get(kr)[0],
                    **{p: c.get(ko)[0] for p, c in curves.items() if p != "R"}}
        enc_s = (curves["R"].get(kr)[1] or 0) + sum(c.get(ko)[1] or 0 for p, c in
                                                    curves.items() if p != "R")
        blob = rp.assemble(raw, spec, payloads)
        rec, dec_s = _decode_timed(rp.decode, blob)
        rows.append(_row(fs, spec.method, label, "raw", tname, tbytes, blob, rec, ctx, raw,
                         enc_s, dec_s, {"R": kr, "GB": ko},
                         f"R share {len(payloads['R']) / len(blob):.2f} (eq {share:.2f})"))
        if crops is not None and tname == "T1":
            crops[label] = rec
    return rows


def _closest(size, knobs, target):
    return min(knobs, key=lambda k: abs(size(k) - target))


def lossless_rows(fs, raw, ctx) -> list[dict]:
    rows = []
    specs = [(rp.RawSpec("C"), "C"), (rp.RawSpec("C-jls"), "C-jls"),
             (rp.RawSpec("C-png"), "C-png"), (rp.RawSpec("C2", effort=3), "C2/e3"),
             (rp.RawSpec("C2", effort=7), "C2/e7"),
             (rp.RawSpec("C2", effort=7, layout="tiled", variant="tiled"), "C2/e7/tiled")]
    for spec, label in specs:
        codes, maxval = rp.code_planes(raw, spec)
        t = time.perf_counter()
        payloads = {k: rp.encode_plane(v, maxval, spec)[0] for k, v in codes.items()}
        enc_s = time.perf_counter() - t
        blob = rp.assemble(raw, spec, payloads)
        rec, dec_s = _decode_timed(rp.decode, blob)
        row = _row(fs, spec.method, label, "lossless", "lossless", None, blob, rec, ctx,
                   raw, enc_s, dec_s, None, lossless_expected=True)
        if spec.method == "C":  # the C binary did the work; the Python reference must agree
            row["py_c_identical"] = all(packer.encode_plane(v.astype(np.uint16),
                                                            maxval.bit_length(), impl="py")
                                        == payloads[k] for k, v in codes.items())
            if not row["py_c_identical"]:
                row["note"] += " FAIL: Python and C packer differ"
        rows.append(row)
    for b in (7, 8, 9, 10):
        if b > raw.bits + 2:
            continue
        spec = rp.RawSpec("N", "sqrt", b)
        codes, maxval = rp.code_planes(raw, spec)
        t = time.perf_counter()
        payloads = {k: rp.encode_plane(v, maxval, spec)[0] for k, v in codes.items()}
        enc_s = time.perf_counter() - t
        blob = rp.assemble(raw, spec, payloads)
        rec, dec_s = _decode_timed(rp.decode, blob)
        rows.append(_row(fs, "N", spec.label(), "near-lossless", "near-lossless", None, blob,
                         rec, ctx, raw, enc_s, dec_s, None))
    return rows


def l_rows(fs, raw, ctx, targets, wb, crops) -> list[dict]:
    rows = []
    rgb = lin_jxl.binned_linear(raw)
    curve = rate.Curve(lambda k: lin_jxl.encode(raw, wb, k, rgb))
    for tname, tbytes in targets.items():
        sol = rate.solve(curve.size, tbytes, 0.05, 25.0, -1, False, start=1.0)
        k = sol.knobs[0]
        blob, enc_s = curve.get(k)
        rec, dec_s = _decode_timed(lin_jxl.decode, blob)
        rows.append(_row(fs, "L", "L", "raw", tname, tbytes, blob, rec, ctx, raw, enc_s, dec_s,
                         k, sol.note if not sol.reachable else ""))
        if crops is not None and tname == "T1":
            crops["L"] = rec
    return rows


# ------------------------------------------------------------------ crops (spec 5.4)

def crop_strip(ctx, mosaic: np.ndarray, out: Path) -> None:
    """grey 128 | red-orange | tag edge, each normal and with the R×4 G×1.5 stress gain."""
    r = ctx.ref
    tiles = []
    boxes = []
    for pid in ("gray_mid", "red_orange"):
        q = np.asarray(ctx.rois["patches"][pid]["quad"])
        cx, cy = q.mean(axis=0)
        half = max(np.ptp(q[:, 0]), np.ptp(q[:, 1])) * 1.2
        boxes.append((cx - half, cy - half, cx + half, cy + half))
    x0, y0, x1, y1 = ctx.rois["texture_box"]
    boxes.append((x0, y0, x1, y1))
    for stress in (np.ones(3), metrics.STRESS_A):
        for bx0, by0, bx1, by1 in boxes:
            X0, Y0 = int(bx0) * 2, int(by0) * 2
            X1, Y1 = int(np.ceil(bx1)) * 2, int(np.ceil(by1)) * 2
            sub = mosaic[Y0:Y1, X0:X1]
            lin = ((sub - r.black) / (r.white - r.black)).astype(np.float32)
            rgb = demosaic_bilinear(lin, cfa_shift(r.cfa, X0, Y0)) * ctx.wb_isp * stress
            img = (np.clip(rgb, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
            tiles.append(cv2.resize(img, (128, 128), interpolation=cv2.INTER_NEAREST))
    strip = np.vstack([np.hstack(tiles[:3]), np.hstack(tiles[3:])])
    cv2.imwrite(str(out), cv2.cvtColor(strip, cv2.COLOR_RGB2BGR))


# ------------------------------------------------------------------ one frame set

def context_for(reps: list[Raw], roi: dict, vmin: float):
    nm = noise.estimate(reps, roi, vmin)
    return metrics.Context(reps[0], reps, roi, nm, grey_wb(reps[0], roi)), nm


def run_frameset(job: dict) -> dict:
    t0 = time.perf_counter()
    data, work = Path(job["data"]), Path(job["work"])
    cam, ill, stop, cond = job["cam"], job["ill"], job["stop"], job["cond"]
    fsid = f"{cam}_{ill}_s{stop:+d}_{cond}"
    out = work / "rows" / f"{fsid}.json"
    if out.exists() and not job.get("force"):
        return {"fsid": fsid, "skipped": True}
    roi = rois.load(STUDY / "config" / "card_rois.yaml")[f"{cam}_{ill}"]
    vmin, dz = CAMERAS[cam][3], CAMERAS[cam][4]
    air = [load(data, cam, ill, stop, i) for i in range(3)]
    air_ctx, air_noise = context_for(air, roi, vmin)
    if cond == "uw":
        reps = [sim.thin(r, air_noise, seed=1000 + 100 * (stop + 2) + i, dead_zone=dz)
                for i, r in enumerate(air)]
        ctx, nm = context_for(reps, roi, 2.0 if dz == 0 else 3.0)
    else:
        reps, ctx, nm = air, air_ctx, air_noise
    raw = reps[0]
    fs = {"camera": cam, "illuminant": ill, "stop": stop, "condition": cond, "fsid": fsid,
          "width": raw.shape[1], "height": raw.shape[0], "native_bits": raw.bits}
    quick, primary = job.get("quick"), stop == -1
    targets = targets_for(cam, raw, quick)
    crops = {} if (primary and ill == "cool") else None
    rows: list[dict] = []
    # floors: a second exposure (sensor noise) and the ISP with no codec (8-bit rounding/clip)
    rows.append(_row(fs, "M0-r1", "repeat", "floor", "floor", None, None,
                     reps[1].mosaic.astype(np.float64), ctx, raw, None, None, None))
    wb_q, _, wb, _ = isp.quantized(ctx.wb_isp)
    spec_img = isp.render(raw, wb)
    rows.append(_row(fs, "M1-q∞", "isp-floor", "floor", "floor", None, None,
                     isp.unrender(spec_img, raw.cfa, raw.black, raw.white, wb), ctx, raw,
                     None, None, None))
    fwb_q, fm_q, fwb, fm = isp.quantized(air_ctx.wb_isp, air_ctx.ccm)
    renders = {"spec": (spec_img, wb_q, None), "fix": (isp.render(raw, fwb, fm), fwb_q, fm_q)}
    isp_clip = {"spec": isp.clip_fractions(raw, wb), "fix": isp.clip_fractions(raw, fwb, fm)}
    if quick:
        proc = ["M1", "M1-fix"]
    elif primary:
        proc = ["M1", "M1-444", "M1j", "M1-fix", "M2", "M2h"]
    else:
        proc = ["M1", "M1-fix"]
    for r in processed_rows(fs, raw, ctx, targets, renders, proc, crops):
        rows.append({**r, **isp_clip["fix" if r["method"] == "M1-fix" else "spec"]})
    d2_mode = job["d2_mode"]
    if primary and not quick:
        rows += lossless_rows(fs, raw, ctx)
    d = rp.RawSpec("D", "sqrt", 8)
    d2 = rp.RawSpec("D2", "sqrt", 12, mode=d2_mode)
    rows += raw_rows(fs, raw, ctx, targets, d, crops)
    rows += raw_rows(fs, raw, ctx, targets, d2, crops)
    if primary and not quick:
        for spec in (d, d2):
            for f in (1.5, 2.0):
                rows += redplus_rows(fs, raw, ctx, targets, spec, f, crops)
            rows += raw_rows(fs, raw, ctx, targets, rp.with_variant(spec, "3pl"), crops)
            rows += raw_rows(fs, raw, ctx, targets, rp.with_variant(spec, "tiled"), crops)
        rows += raw_rows(fs, raw, ctx, targets, rp.RawSpec("D-j", "sqrt", 8), crops)
        rows += l_rows(fs, raw, ctx, targets, ctx.wb_isp, crops)
        if cam != "imx708":  # OpenMV ablation: no curve on already-8-bit data
            rows += raw_rows(fs, raw, ctx, targets, rp.RawSpec("D-lin", "linear", 8), crops)
            rows += raw_rows(fs, raw, ctx, targets, rp.RawSpec("D2-lin", "linear", 8,
                                                               mode=d2_mode), crops)
        if job.get("mode_choice"):
            for mode in D2_MODES:
                if mode != d2_mode:
                    s = replace(d2, mode=mode)
                    rows += raw_rows(fs, raw, ctx, {k: targets[k] for k in ("T1", "T2")}, s,
                                     None)
        if cam == "imx708":
            rows += field_rows(fs, reps, roi, nm, air_ctx, d2_mode)
    if crops is not None:
        cdir = work / "crops" / fsid
        cdir.mkdir(parents=True, exist_ok=True)
        crop_strip(ctx, raw.mosaic.astype(np.float64), cdir / "M0.png")
        for label, rec in crops.items():
            crop_strip(ctx, rec, cdir / (label.replace("/", "_") + ".png"))
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"fsid": fsid, "seconds": time.perf_counter() - t0, "noise": nm.summary(),
            "noise_air": air_noise.summary(), "wb_isp": ctx.wb_isp.tolist(),
            "ccm": ctx.ccm.tolist(), "d2_mode": d2_mode, "n_rows": len(rows)}
    out.write_text(json.dumps({"meta": meta, "rows": rows}, default=_json))
    return {"fsid": fsid, "rows": len(rows), "seconds": round(meta["seconds"], 1)}


def _json(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


# ------------------------------------------------------------------ IMX708 field row

def field_rows(fs, reps, roi, nm, air_ctx, d2_mode) -> list[dict]:
    """The deployed recipe: a 1600×900 crop → 1000×562 JPEG at 50 kB, vs raw on the same crop."""
    raw = reps[0]
    # centre on the card + chart patches (the whole card is wider than the 1600 px crop)
    cx = np.mean([np.mean(p["quad"], axis=0) for p in roi["patches"].values()], axis=0) * 2
    w, h = FIELD_CROP
    x0 = int(np.clip(cx[0] - w / 2, 0, raw.shape[1] - w)) // 2 * 2
    y0 = int(np.clip(cx[1] - h / 2, 0, raw.shape[0] - h)) // 2 * 2
    crop = [replace(r, mosaic=np.ascontiguousarray(r.mosaic[y0:y0 + h, x0:x0 + w]))
            for r in reps]
    croi = shift_rois(roi, x0 // 2, y0 // 2)
    nm_c = noise.estimate(crop, croi, 2.0 if fs["condition"] == "uw" else 2.0)
    ctx = metrics.Context(crop[0], crop, croi, nm_c, grey_wb(crop[0], croi))
    cfs = {**fs, "fsid": fs["fsid"] + "_field", "width": w, "height": h}
    rows = []
    wb_q, _, wb, _ = isp.quantized(ctx.wb_isp)
    small = isp.resize_area(isp.render(crop[0], wb), *FIELD_OUT)
    curve = rate.Curve(lambda k: isp.encode_image(small, crop[0], "M1", k, wb_q, resized=True))
    sol = rate.solve(curve.size, FIELD_BYTES, 1, 100, +1, True)
    for k in sol.knobs:
        blob, enc_s = curve.get(k)
        rec, dec_s = _decode_timed(isp.decode, blob)
        rows.append(_row(cfs, "M1", "field-1000x562", "processed", "50kB", FIELD_BYTES, blob,
                         rec, ctx, crop[0], enc_s, dec_s, k, sol.note))
    t = {"50kB": FIELD_BYTES}
    for spec in (rp.RawSpec("D", "sqrt", 8), rp.RawSpec("D2", "sqrt", 12, mode=d2_mode),
                 rp.RawSpec("D2", "sqrt", 12, mode=d2_mode, binned=True)):
        rows += raw_rows(cfs, crop[0], ctx, t, spec, None)
    return rows


def shift_rois(roi: dict, dx: float, dy: float) -> dict:
    out = {"patches": {k: {"group": v["group"],
                           "quad": [[x - dx, y - dy] for x, y in v["quad"]]}
                       for k, v in roi["patches"].items()}}
    x0, y0, x1, y1 = roi["texture_box"]
    out["texture_box"] = [int(x0 - dx), int(y0 - dy), int(x1 - dx), int(y1 - dy)]
    out["card_box"] = roi["card_box"]
    return out


# ------------------------------------------------------------------ main

def jobs_for(args) -> list[dict]:
    cams = args.only.split(",") if args.only else list(CAMERAS)
    jobs = []
    for cam in cams:
        for ill in ILLUMINANTS:
            for stop in ((-1,) if args.quick else (-1, 0)):
                for cond in ("air", "uw"):
                    if args.quick and (ill != "cool"):
                        continue
                    jobs.append({"cam": cam, "ill": ill, "stop": stop, "cond": cond})
    return jobs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", type=Path, required=True,
                    help="copy of the S4 session with SHA256SUMS (<primary>/data/s4_20260930)")
    ap.add_argument("--work", type=Path, default=STUDY / "work")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--only", default="", help="comma list of cameras")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    packer.build_c()
    used = [frame_path(args.data, c, i, s, r) for c in CAMERAS for i in ILLUMINANTS
            for s in (-1, 0) for r in range(3)]
    args.work.mkdir(parents=True, exist_ok=True)
    (args.work / "inputs.json").write_text(json.dumps(verify_inputs(args.data, used), indent=1))
    base = {"data": str(args.data), "work": str(args.work), "quick": args.quick,
            "force": args.force}
    jobs = [{**base, **j} for j in jobs_for(args)]
    # phase A: choose the D2 JPEG XL mode per camera on (cool, stop −1, air)
    choice = [j for j in jobs if j["ill"] == "cool" and j["stop"] == -1 and j["cond"] == "air"]
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        modes = dict(zip([j["cam"] for j in choice],
                         ex.map(choose_mode, choice, [args] * len(choice))))
    print("D2 mode per camera:", modes, flush=True)
    (args.work / "d2_modes.json").write_text(json.dumps(modes, indent=1))
    todo = [{**j, "d2_mode": modes.get(j["cam"], "vardct"),
             "mode_choice": j in choice} for j in jobs]
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(run_frameset, j): j for j in todo}
        for f in as_completed(futs):
            j = futs[f]
            try:
                print("done", f.result(), flush=True)
            except Exception:  # noqa: BLE001 — keep the other frame sets going
                print(f"FAILED {j['cam']} {j['ill']} {j['stop']} {j['cond']}\n"
                      f"{traceback.format_exc()}", flush=True)
    return 0


def choose_mode(job: dict, args) -> str:
    """D2 mode with the lowest mean stress ΔE00 at T1 + T2 on (cool, stop −1, air)."""
    cache = args.work / f"mode_choice_{job['cam']}.json"
    if cache.exists() and not args.force:
        return json.loads(cache.read_text())["mode"]
    data = Path(job["data"])
    roi = rois.load(STUDY / "config" / "card_rois.yaml")[f"{job['cam']}_cool"]
    reps = [load(data, job["cam"], "cool", -1, i) for i in range(3)]
    ctx, _ = context_for(reps, roi, CAMERAS[job["cam"]][3])
    raw = reps[0]
    t = {k: v * raw.n_px / 8 for k, v in BPP_TARGETS.items() if k in ("T1", "T2")}
    score = {}
    for mode in D2_MODES:
        rows = raw_rows({}, raw, ctx, t, rp.RawSpec("D2", "sqrt", 12, mode=mode), None)
        score[mode] = float(np.mean([r["stress_de_mean"] for r in rows]))
        print(f"  {job['cam']} D2 {mode}: mean stress ΔE {score[mode]:.3f}", flush=True)
    best = min(score, key=score.get)
    cache.write_text(json.dumps({"mode": best, "score": score}, indent=1))
    return best


if __name__ == "__main__":
    raise SystemExit(main())
