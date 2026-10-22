"""Card truth from a reference chart — measure a printed card through a camera calibrated on a
chart with known values, both in one frame under one light (card V3, Nick 2026-10-05).

Procedure: ``docs/v3_card_truth_capture.md``. Session config:
``configs/calibration/sessions/v3c1_truth_TEMPLATE.yaml``. CLI: ``python -m host_tools.card_truth``.

1. **Sample** every frame (RAW first): the card located by its tags; the chart either found in
   the card plane (``color.chart`` in a canonical-px search ``region``) or placed from its four
   corner-patch centres (``quad_raw``, RAW px); binned linear camera RGB means of every patch
   (central 60 %, per-channel clip fractions). Optional flat-field frames (a matte board in the
   same plane) divide out light falloff and lens shading; their spread over the card and chart
   is reported as the evenness.
2. **Fit** camera RGB -> CIE XYZ (D50, the chart reference's illuminant) on the chart patches:
   a 3x3 matrix and a root-polynomial (degree 2, Finlayson et al. 2015: exposure-invariant),
   least squares on error relative to each patch's Y. **Held-out score:** leave-one-patch-out
   CIEDE2000 (Lab D50) on the chart; the model with the lower held-out median is used.
3. **Measure** each card patch: median over frames -> XYZ D50 -> Lab D50, and -> Bradford D65
   -> linear sRGB -> sRGB 8-bit (the scale ``card.py`` reads; out-of-gamut channels clipped to
   0 and listed). Written as the card YAML's ``measured:`` block (``measured_wet:`` for the wet
   condition) with provenance.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from .card import Box, load_card
from .chart import find_chart, load_chart
from .jpeg_geometry import JpegMap
from .locate import locate_frame, tag_geometry, tag_spec
from .metrics import SRGB_TO_XYZ, delta_e2000
from .patches import homography, mosaic_to_binned, sample
from .raw_io import bin2x2, normalize

D50 = np.array([0.96422, 1.0, 0.82521])
D65 = np.array([0.95047, 1.0, 1.08883])
BRADFORD = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367],
                     [0.0389, -0.0685, 1.0296]])
CLIP_MAX = 0.001          # a patch with more clipped pixels than this in any channel is dropped
CELL = 0.7                # quad_raw charts: sampled box = this share of the patch pitch


# ------------------------------------------------------------------ colour maths

def lab_to_xyz(lab, white=D50) -> np.ndarray:
    lab = np.asarray(lab, np.float64)
    fy = (lab[..., 0] + 16) / 116
    fx, fz = fy + lab[..., 1] / 500, fy - lab[..., 2] / 200
    d = 6 / 29

    def inv(f):
        return np.where(f > d, f ** 3, 3 * d * d * (f - 4 / 29))
    return np.stack([inv(fx), inv(fy), inv(fz)], -1) * white


def xyz_to_lab(xyz, white=D50) -> np.ndarray:
    t = np.asarray(xyz, np.float64) / white
    d = 6 / 29
    f = np.where(t > d ** 3, np.cbrt(t), t / (3 * d * d) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], -1)


def bradford(src, dst) -> np.ndarray:
    s, d = BRADFORD @ src, BRADFORD @ dst
    return np.linalg.inv(BRADFORD) @ np.diag(d / s) @ BRADFORD


def xyz50_to_srgb8(xyz50: np.ndarray) -> tuple[np.ndarray, bool]:
    """XYZ D50 -> sRGB 8-bit (D65). -> (values clipped to 0..300, out-of-gamut flag)."""
    lin = np.linalg.inv(SRGB_TO_XYZ) @ (bradford(D50, D65) @ xyz50)
    oog = bool(np.any(lin < 0))
    lin = np.clip(lin, 0, None)
    v = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * lin ** (1 / 2.4) - 0.055) * 255
    return np.clip(v, 0, 300), oog


def _terms(rgb: np.ndarray, model: str) -> np.ndarray:
    rgb = np.clip(np.asarray(rgb, np.float64), 0, None)
    if model == "linear3x3":
        return rgb
    if model == "rootpoly2":
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        return np.stack([r, g, b, np.sqrt(r * g), np.sqrt(g * b), np.sqrt(r * b)], -1)
    raise ValueError(f"unknown model {model!r}")


def fit(rgb: np.ndarray, xyz: np.ndarray, model: str) -> np.ndarray:
    """Least squares on error relative to each target's Y: XYZ = terms(rgb) @ M."""
    w = 1 / np.maximum(xyz[:, 1], 0.02)
    T = _terms(rgb, model) * w[:, None]
    M, *_ = np.linalg.lstsq(T, xyz * w[:, None], rcond=None)
    return M


def apply(M: np.ndarray, rgb: np.ndarray, model: str) -> np.ndarray:
    return _terms(rgb, model) @ M


def loo(rgb: np.ndarray, xyz: np.ndarray, model: str) -> np.ndarray:
    """Leave-one-patch-out CIEDE2000 (Lab D50) per patch."""
    de = []
    for i in range(len(rgb)):
        k = np.arange(len(rgb)) != i
        pred = apply(fit(rgb[k], xyz[k], model), rgb[i:i + 1], model)
        de.append(float(delta_e2000(xyz_to_lab(pred), xyz_to_lab(xyz[i:i + 1]))[0]))
    return np.array(de)


def stats(de) -> dict[str, Any]:
    de = np.asarray(de)
    return {"n": int(len(de)), "median": round(float(np.median(de)), 2),
            "p90": round(float(np.percentile(de, 90)), 2), "max": round(float(de.max()), 2)}


# ------------------------------------------------------------------ reference values

def load_reference(path: Path) -> dict[str, dict[str, Any]]:
    """Chart reference CSV -> {name: {"lab": (L, a, b)}} in file order. Accepts a header with a
    name column (name / patch / id / swatch) and L, a, b columns (``L``, ``L*``, ``LAB_L`` …),
    or X, Y, Z columns (Y in 0..100). Comment lines (#) are skipped."""
    text = "\n".join(ln for ln in Path(path).read_text(encoding="utf-8-sig").splitlines()
                     if ln.strip() and not ln.lstrip().startswith("#"))
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows:
        raise ValueError(f"{path}: no rows")

    def col(*pats):
        for c in rows[0]:
            key = re.sub(r"[^a-z]", "", c.lower())
            if key in pats:
                return c
        return None
    name = col("name", "patch", "id", "swatch", "sample", "samplename", "patchname")
    L, A, B = col("l", "lab_l", "labl", "cielabl"), col("a", "laba", "cielaba"), \
        col("b", "labb", "cielabb")
    X, Y, Z = col("x", "xyzx"), col("y", "xyzy"), col("z", "xyzz")
    if name is None or not ((L and A and B) or (X and Y and Z)):
        raise ValueError(f"{path}: need a name column and L/a/b (or X/Y/Z) columns, "
                         f"got {list(rows[0])}")
    out = {}
    for r in rows:
        if L and A and B:
            lab = (float(r[L]), float(r[A]), float(r[B]))
        else:
            lab = tuple(xyz_to_lab(np.array([float(r[X]), float(r[Y]), float(r[Z])]) / 100))
        out[r[name].strip()] = {"lab": tuple(float(v) for v in lab)}
    return out


# ------------------------------------------------------------------ sampling

def _flat(paths: list[Path], open_raw) -> Optional[np.ndarray]:
    """Median flat-field (binned linear RGB), smoothed, each channel / its median."""
    if not paths:
        return None
    stack = []
    for p in paths:
        lin, sat, cfa = normalize(open_raw(p))
        stack.append(bin2x2(lin, cfa, sat)[0])
    f = np.median(stack, axis=0).astype(np.float32)
    f = cv2.GaussianBlur(f, (0, 0), 8)
    return f / np.median(f.reshape(-1, 3), axis=0)


def chart_boxes_from_quad(quad_raw, valid_crop, rows: int, cols: int, chart) -> tuple:
    """Grid homography (cell coords -> binned px) from the 4 corner-patch centres (RAW px,
    order r1c1, r1cN, rMcN, rMc1)."""
    q = np.asarray(quad_raw, np.float64)
    src = np.array([[0, 0], [cols - 1, 0], [cols - 1, rows - 1], [0, rows - 1]], np.float64)
    to_bin = mosaic_to_binned(valid_crop)
    qb = cv2.perspectiveTransform(q[None], to_bin)[0]
    G = cv2.getPerspectiveTransform(src.astype(np.float32), qb.astype(np.float32))
    boxes = {p.id: Box(x=p.col - CELL / 2, y=p.row - CELL / 2, w=CELL, h=CELL)
             for p in chart.patches}
    return G, boxes


def sample_frame(path: Path, card, chart, region=None, quad_raw=None, flat=None,
                 open_raw=None, overlay: Optional[Path] = None) -> dict[str, Any]:
    from .stages import open_raw as _open
    open_raw = open_raw or _open
    frame = open_raw(path)
    rec = locate_frame(Path(path), None, card.corner_map, JpegMap.offset(0, 0),
                       raw_reader=lambda _p: frame, geometry=tag_geometry(card),
                       spec=tag_spec(card))
    out: dict[str, Any] = {"file": Path(path).name, "tags_found": rec.get("tags_found"),
                           "exposure_s": frame.exposure_s}
    if not rec["located"]:
        return {**out, "error": f"card not located: {rec.get('reason')}"}
    linear, saturated, cfa = normalize(frame)
    binned, clip = bin2x2(linear, cfa, saturated)
    if flat is not None:
        binned = binned / np.maximum(flat, 1e-3)
    H = mosaic_to_binned(frame.valid_crop) @ homography(card, np.asarray(rec["quad_raw"]))
    ref_id = card.roles.wb_anchors[0]       # normalises the chart search (no white patch needed)
    out["patches"], out["chart_patches"] = {}, {}
    for p in card.patches:
        st = sample(binned, H, p.box, clip=clip)
        if st.get("mean"):
            out["patches"][p.id] = {k: st.get(k) for k in ("mean", "std", "n_px", "clip_frac")}
    drawn = [(H, {p.id: p.box for p in card.patches}, (0, 255, 0))]
    if quad_raw is not None:
        G, boxes = chart_boxes_from_quad(quad_raw, frame.valid_crop, chart.rows, chart.cols, chart)
        out["chart"] = {"method": "quad_raw"}
    else:
        white = out["patches"][ref_id]["mean"]
        try:
            found = find_chart(binned, H, white, chart, tuple(region))
        except ValueError as exc:
            return {**out, "chart": {"error": str(exc)}}
        G, boxes = H, found.pop("boxes")
        out["chart"] = {"method": "found", **found}
    for pid, box in boxes.items():
        st = sample(binned, G, box, clip=clip)
        if st.get("mean"):
            out["chart_patches"][pid] = {k: st.get(k) for k in ("mean", "std", "n_px", "clip_frac")}
    drawn.append((G, boxes, (255, 128, 0)))
    if overlay is not None:
        _overlay(binned, drawn, overlay)
    return out


def _overlay(binned, drawn, path: Path) -> None:
    img = binned / max(float(np.percentile(binned, 99.5)), 1e-6)
    img = (np.clip(img, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)[..., ::-1].copy()
    for H, boxes, col in drawn:
        for pid, b in boxes.items():
            c = np.array([[[b.x, b.y]], [[b.x + b.w, b.y]], [[b.x + b.w, b.y + b.h]],
                          [[b.x, b.y + b.h]]], np.float64)
            pts = cv2.perspectiveTransform(c, H).reshape(-1, 2)
            cv2.polylines(img, [np.round(pts).astype(np.int32)], True, col, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)


def usable(p: Optional[dict]) -> bool:
    return bool(p) and max(p.get("clip_frac") or [0]) <= CLIP_MAX


def median_means(frames: list[dict], key: str) -> dict[str, np.ndarray]:
    reads: dict[str, list] = {}
    for f in frames:
        for pid, p in (f.get(key) or {}).items():
            if usable(p):
                reads.setdefault(pid, []).append(p["mean"])
    return {pid: np.median(np.array(v), axis=0) for pid, v in reads.items()}


# ------------------------------------------------------------------ run

def _raw_frames(folder: Path) -> list[Path]:
    """The locked captures in a capture folder: ``stop_*`` RAWs when present (the capture
    scripts also write a metering ``probe`` RAW at another exposure), else every RAW."""
    raws = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".dng", ".bayer"))
    locked = [p for p in raws if p.name.startswith("stop_")]
    return locked or raws


def measure(session: dict, root: Path, open_raw=None, overlay_dir: Optional[Path] = None) -> dict:
    """Session dict (paths relative to ``root``) -> result dict with the ``measured`` block."""
    card = load_card(root / session["card"])
    chart = load_chart(root / session["chart"]["config"])
    ref_path = root / session["chart"]["reference"]
    ref = load_reference(ref_path)
    order = session["chart"].get("order") or list(ref)
    if len(order) != len(chart.patches):
        raise ValueError(f"{len(order)} reference names for {len(chart.patches)} chart patches "
                         "(chart.order lists the reference names row by row as framed)")
    name_of = {p.id: name for p, name in zip(chart.patches, order)}
    frames_dir = root / session["frames"]
    frames = _raw_frames(frames_dir)
    flats = _raw_frames(root / session["flat"]) if session.get("flat") else []
    from .stages import open_raw as _open
    flat = _flat(flats, open_raw or _open)
    samples = [sample_frame(p, card, chart, session["chart"].get("region"),
                            session["chart"].get("quad_raw"), flat, open_raw,
                            None if overlay_dir is None else overlay_dir / f"{p.stem}_overlay.png")
               for p in frames]
    good = [s for s in samples if "error" not in s and "error" not in s.get("chart", {})]
    if not good:
        raise ValueError("no frame with both the card and the chart: "
                         + "; ".join(f"{s['file']}: {s.get('error') or s['chart'].get('error')}"
                                     for s in samples))
    cam_chart = median_means(good, "chart_patches")
    cam_card = median_means(good, "patches")
    ids = [pid for pid in name_of if pid in cam_chart and name_of[pid] in ref]
    rgb = np.array([cam_chart[pid] for pid in ids])
    xyz = np.array([lab_to_xyz(ref[name_of[pid]]["lab"]) for pid in ids])
    models = {m: stats(loo(rgb, xyz, m)) for m in ("linear3x3", "rootpoly2")}
    best = min(models, key=lambda m: models[m]["median"])
    M = fit(rgb, xyz, best)
    values, labs, oog, over = {}, {}, [], []
    for p in card.patches:
        if p.id not in cam_card:
            continue
        x50 = apply(M, cam_card[p.id][None], best)[0]
        v, out_of = xyz50_to_srgb8(x50)
        values[p.id] = [round(float(c), 1) for c in v]
        labs[p.id] = [round(float(c), 2) for c in xyz_to_lab(x50)]
        if out_of:
            oog.append(p.id)
        if max(v) >= 299.5:     # brighter than the chart allows for: exposure / light evenness
            over.append(p.id)
    evenness = None
    if flat is not None:
        evenness = round(float(100 * (np.percentile(flat[..., 1], 95) /
                                      np.percentile(flat[..., 1], 5) - 1)), 1)
    return {"samples": samples, "n_frames": len(good), "chart_patches_used": len(ids),
            "card_patches": len(values), "fit": {"model": best, "loo_de2000": models,
                                                  "matrix": np.round(M, 6).tolist()},
            "values": values, "lab_d50": labs, "out_of_srgb": oog, "over_range": over,
            "reference_sha256": hashlib.sha256(ref_path.read_bytes()).hexdigest(),
            "flat_frames": [p.name for p in flats], "frames": [s["file"] for s in good],
            "flat_spread_pct_p95_p5": evenness}


def _y(v) -> str:
    """A YAML scalar for the flow mappings below."""
    if v is None:
        return "null"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return str(v)


def measured_block(res: dict, session: dict, date_utc: str) -> str:
    """The YAML text of the ``measured:`` / ``measured_wet:`` block (no YAML library needed)."""
    cond = session.get("condition", "dry")
    key = "measured" if cond == "dry" else f"measured_{cond}"
    light = session.get("light", {})
    ft = res["fit"]
    lo = ft["loo_de2000"][ft["model"]]
    chart_id = Path(session["chart"]["config"]).stem
    flat = ", flat-fielded" if res["flat_frames"] else ""
    ch = session["chart"]
    lines = [
        f"{key}:",
        f"  source: measured_imx708_{chart_id}_{date_utc[:10]}_{cond}",
        f"  method: IMX708 RAW (locked exposure, lowest gain){flat},"
        f" camera RGB -> XYZ D50 fitted on {chart_id} ({ft['model']}),"
        " Bradford D50 -> D65, sRGB; median over frames",
        f"  condition: {cond}",
        f"  date_utc: '{date_utc}'",
        f"  chart: {{config: {ch['config']}, reference: {ch['reference']},"
        f" reference_sha256: {res['reference_sha256'][:16]}, illuminant: D50, observer: 2}}",
        f"  light: {{{', '.join(f'{k}: {_y(v)}' for k, v in light.items())}}}",
        f"  frames: [{', '.join(res['frames'])}]",
        f"  flat_frames: [{', '.join(res['flat_frames'])}]",
        f"  flat_spread_pct_p95_p5: {_y(res['flat_spread_pct_p95_p5'])}",
        f"  fit: {{model: {ft['model']}, chart_patches: {res['chart_patches_used']},"
        f" held_out_de2000_median: {lo['median']}, p90: {lo['p90']}, max: {lo['max']}}}",
        f"  out_of_srgb: [{', '.join(res['out_of_srgb'])}]   # clipped to 0 in values; lab exact",
        f"  over_range: [{', '.join(res['over_range'])}]   # capped at 300: check light, exposure",
        "  values:                # sRGB 8-bit scale (D65); what card.py reads as the truth",
    ]
    lines += [f"    {pid}: [{', '.join(f'{c:.1f}' for c in v)}]"
              for pid, v in res["values"].items()]
    lines += ["  lab_d50:"]
    lines += [f"    {pid}: [{', '.join(f'{c:.2f}' for c in v)}]"
              for pid, v in res["lab_d50"].items()]
    return "\n".join(lines) + "\n"


def write_block(card_yaml: Path, block: str) -> None:
    """Replace this block's top-level key in the card YAML (or append it); other text stays."""
    text = card_yaml.read_text()
    key = block.split(":", 1)[0]
    pat = re.compile(rf"^{re.escape(key)}:\n(?:(?:[ #].*)?\n)*", re.M)
    if pat.search(text):
        text = pat.sub(block, text, count=1)
    else:
        text = text.rstrip("\n") + "\n\n" + block
    card_yaml.write_text(text)
