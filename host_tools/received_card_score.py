# ruff: noqa: E501  (inline HTML template)
"""Score the V3 reference card in RECEIVED images: the backend's B3a Linear DNG or its display
JPEG (or any 8-bit JPEG/PNG of the same scene). Mac-side, one shot, no cloud code.

    python -m host_tools.received_card_score IMAGE [IMAGE ...] [--output-dir DIR]
        [--card configs/cards/nereus_v3_c1.yaml] [--truth provisional|yaml|design]
        [--calib card_calib.json]

What it does, per image:
  1. finds the V3 card: AprilTag tag25h9 on an 8-bit view of the image (the DNG's linear
     green, normalised and gamma-encoded; a JPEG as is), the rig's tested locate code;
  2. samples the 12 V3 patches (central 60 %, canonical boxes from the card YAML);
  3. converts to CIELAB D50 by every path the input supports:
       dng_camera  LinearRaw camera RGB -> XYZ D65 by the DNG's own ColorMatrix1 (the unit's
                   libcamera CCM + AWB, as the backend wrote it) -> Bradford D50
       dng_cardwb  the same matrix, but white-balanced on the V3 greys instead of the AWB
       dng_calib   (with --calib, IMX708 only) WB on the V3 greys + the 2026-10-06 bench matrix
       display     8-bit sRGB (the backend's display JPEG, or any JPEG/PNG)
     Every path gets ONE exposure scale (Y of gray_light set to the truth's); no colour change;
  4. ΔE2000 per patch vs the truth, the lightness-free ΔE2000 (L* set to the truth's: hue +
     chroma only), and ΔE2000 vs the print design; summary over the 8 colour patches (the greys
     are anchors / exposure references and are reported, not averaged).

Truth (--truth): ``provisional`` (default) = the IMX708 measurement of V3 c1 on 2026-10-06
(docs/card_truth/truth_20261006/truth_alsc.json, no flat-field: card L* uncertain by up to
~10–15); ``yaml`` = the card YAML's ``measured: lab_d50`` block once it exists; ``design`` =
the print file. The truth used is written into every output.

Outputs (default results/received_card_<UTC date>/): received_scores.csv / .json (one row per
image x path), patches.csv (every patch), <stem>_overlay.jpg, index.html (cut sheet).

Assumptions: the card is V3 c1 (tag IDs 0–3, tag25h9), seen whole; the DNG is the backend's
LinearRaw profile v2 (B3a) or a CFA-free 3-sample linear DNG with ColorMatrix1 + AsShotNeutral;
a JPEG/PNG is sRGB. Not handled: V2 cards (use bm_cam_legacy tools/bm_reference_card_*), CFA
DNGs (use the rig's raw pipeline), lens distortion (a flat card at 1600x900 is near-projective).
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from nereus_camera_test_rig.color import card_truth as T
from nereus_camera_test_rig.color.card import load_card
from nereus_camera_test_rig.color.jpeg_geometry import JpegMap, read_jpeg
from nereus_camera_test_rig.color.locate import locate_frame, tag_geometry, tag_spec
from nereus_camera_test_rig.color.metrics import SRGB_TO_XYZ, delta_e2000
from nereus_camera_test_rig.color.patches import homography, sample

REPO = Path(__file__).resolve().parents[1]
CARD = REPO / "configs/cards/nereus_v3_c1.yaml"
PROVISIONAL = REPO / "docs/card_truth/truth_20261006/truth_alsc.json"
D65 = np.array([0.95047, 1.0, 1.08883])
ANCHORS = ("gray_light", "gray_light2", "gray_mid")
EXPOSURE_REF = "gray_light"


# ---------------------------------------------------------------- inputs

def read_linear_dng(path: Path) -> tuple[np.ndarray, dict]:
    """LinearRaw DNG -> (h, w, 3) float camera RGB in [0, 1], tags."""
    import tifffile

    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        tags = {t.code: t.value for t in page.tags}
        if tags.get(262) != 34892 or page.samplesperpixel != 3:
            raise ValueError(f"{path.name}: not a 3-sample LinearRaw DNG (Photometric "
                             f"{tags.get(262)}, {page.samplesperpixel} samples)")
        data = page.asarray().astype(np.float32)
    white = float(np.atleast_1d(tags.get(50717, 65535))[0])
    black = float(np.atleast_1d(tags.get(50714, 0))[0])
    rgb = (data - black) / (white - black)

    def rationals(v):
        v = np.asarray(v, float).ravel()
        return v[0::2] / v[1::2] if v.size % 2 == 0 and v.size >= 2 and np.all(v[1::2] > 0) and v.size in (6, 18) else v

    cm = rationals(tags[50721]).reshape(3, 3)
    neutral = rationals(tags[50728])
    desc = tags.get(270)
    try:
        desc = json.loads(desc) if isinstance(desc, str) else desc
    except ValueError:
        pass
    return rgb, {"color_matrix1": cm, "as_shot_neutral": neutral, "description": desc}


def detection_png(rgb_linear: np.ndarray, out: Path) -> Path:
    g = rgb_linear[..., 1]
    g = np.clip(g / max(np.percentile(g, 99.5), 1e-6), 0, 1) ** (1 / 2.2)
    cv2.imwrite(str(out), (g * 255 + 0.5).astype(np.uint8))
    return out


def find_card(view: Path, card) -> dict:
    rec = locate_frame(None, view, card.corner_map, JpegMap.offset(0, 0),
                       geometry=tag_geometry(card), spec=tag_spec(card))
    if not rec["located"]:
        raise ValueError(f"V3 card not found: {rec.get('reason')} (tags {rec.get('tags_found')})")
    return rec


# ---------------------------------------------------------------- colour

def srgb8_to_xyz50(v) -> np.ndarray:
    v = np.asarray(v, float) / 255
    lin = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    return (lin @ SRGB_TO_XYZ.T) @ T.bradford(D65, T.D50).T


def load_truth(kind: str, card, card_path: Path) -> tuple[dict, str]:
    design = {p.id: T.xyz_to_lab(srgb8_to_xyz50(p.design or p.truth)) for p in card.patches}
    if kind == "design":
        return design, "design (print file sRGB)"
    if kind == "yaml":
        import yaml

        m = (yaml.safe_load(card_path.read_text()) or {}).get("measured") or {}
        if "lab_d50" not in m:
            raise SystemExit(f"{card_path}: no measured: lab_d50 block yet (use --truth provisional)")
        return ({k: np.asarray(v, float) for k, v in m["lab_d50"].items()},
                f"card YAML measured ({m.get('source')})")
    t = json.loads(PROVISIONAL.read_text())["cams"]["imx708"]["stops"]["stop_+0"]["v3_lab"]
    return ({k: np.asarray(v, float) for k, v in t.items()},
            "PROVISIONAL: IMX708 2026-10-06, no flat-field (card L* uncertain up to ~10-15)")


def to_lab_exposed(xyz: dict, truth: dict) -> dict:
    k = T.lab_to_xyz(truth[EXPOSURE_REF])[1] / max(xyz[EXPOSURE_REF][1], 1e-9)
    return {pid: T.xyz_to_lab(v * k) for pid, v in xyz.items()}


def paths_for(kind: str, means: dict, meta: dict, calib: dict | None) -> dict[str, dict]:
    """{path name: {patch: XYZ D50}} for every path the input supports."""
    out = {}
    if kind == "dng":
        cam2xyz = np.linalg.inv(meta["color_matrix1"])          # camera -> XYZ D65 (white = AsShotNeutral)
        to50 = T.bradford(D65, T.D50)
        out["dng_camera"] = {k: to50 @ (cam2xyz @ v) for k, v in means.items()}
        grey = np.mean([means[k] for k in ANCHORS], axis=0)
        g = np.asarray(meta["as_shot_neutral"], float) / grey   # card grey -> the matrix's neutral
        g = g / g[1]
        out["dng_cardwb"] = {k: to50 @ (cam2xyz @ (v * g)) for k, v in means.items()}
        if calib is not None:
            model = "rootpoly2"
            M = np.asarray(calib["models"][model]["matrix"], float)
            gc = grey[1] / grey
            out["dng_calib"] = {k: T.apply(M, (v * gc)[None], model)[0] for k, v in means.items()}
    else:
        out["display"] = {k: srgb8_to_xyz50(v) for k, v in means.items()}
    return out


def score(lab: dict, truth: dict, design: dict, colours: list) -> tuple[dict, list]:
    rows = []
    for pid, v in lab.items():
        rows.append({"patch": pid, "L": round(float(v[0]), 2), "a": round(float(v[1]), 2),
                     "b": round(float(v[2]), 2),
                     "de00": round(float(delta_e2000(v, truth[pid])), 2),
                     "de00_nolightness": round(float(delta_e2000(np.r_[truth[pid][0], v[1:]], truth[pid])), 2),
                     "de00_vs_design": round(float(delta_e2000(v, design[pid])), 2)})
    col = [r for r in rows if r["patch"] in colours]
    worst = max(col, key=lambda r: r["de00"])
    summ = {"de00_mean": round(float(np.mean([r["de00"] for r in col])), 2),
            "de00_median": round(float(np.median([r["de00"] for r in col])), 2),
            "de00_worst": worst["de00"], "worst_patch": worst["patch"],
            "de00_nolightness_mean": round(float(np.mean([r["de00_nolightness"] for r in col])), 2),
            "de00_vs_design_mean": round(float(np.mean([r["de00_vs_design"] for r in col])), 2),
            "grey_ab_max": round(float(max(np.hypot(lab[k][1], lab[k][2]) for k in ANCHORS)), 2)}
    return summ, rows


# ---------------------------------------------------------------- outputs

def lab_hex(lab) -> str:
    xyz = T.lab_to_xyz(np.asarray(lab, float))
    v, _ = T.xyz50_to_srgb8(xyz)
    return "#" + "".join(f"{int(round(min(max(c, 0), 255))):02x}" for c in v)


def overlay(img8: np.ndarray, H: np.ndarray, card, out: Path) -> None:
    vis = img8.copy()
    for p in card.patches:
        b = p.box
        c = np.array([[[b.x, b.y]], [[b.x + b.w, b.y]], [[b.x + b.w, b.y + b.h]], [[b.x, b.y + b.h]]], float)
        q = cv2.perspectiveTransform(c, H).reshape(-1, 2).astype(np.int32)
        cv2.polylines(vis, [q], True, (0, 255, 255), 2)
    cv2.imwrite(str(out), vis, [cv2.IMWRITE_JPEG_QUALITY, 85])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("images", nargs="+", type=Path)
    ap.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--card", type=Path, default=CARD)
    ap.add_argument("--truth", choices=("provisional", "yaml", "design"), default="provisional")
    ap.add_argument("--calib", type=Path, default=None,
                    help="IMX708 bench calibration (scripts/s28_card_calib.py) for the dng_calib path")
    a = ap.parse_args(argv)
    out = a.output_dir or REPO / "results" / f"received_card_{datetime.now(timezone.utc):%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    card = load_card(a.card)
    truth, truth_label = load_truth(a.truth, card, a.card)
    design, _ = load_truth("design", card, a.card)
    colours = [p.id for p in card.patches if p.group == "color"]
    calib = json.loads(a.calib.read_text()) if a.calib else None
    summary_rows, patch_rows, sheets = [], [], []
    for img_path in a.images:
        kind = "dng" if img_path.suffix.lower() == ".dng" else "display"
        try:
            with tempfile.TemporaryDirectory() as td:
                if kind == "dng":
                    data, meta = read_linear_dng(img_path)
                    view = detection_png(data, Path(td) / "view.png")
                    img8 = cv2.cvtColor(cv2.imread(str(view)), cv2.COLOR_BGR2RGB)
                else:
                    bgr = read_jpeg(img_path)
                    if bgr is None:
                        raise ValueError("cannot read image")
                    data, meta, view = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32), {}, img_path
                    img8 = data.astype(np.uint8)
                try:
                    rec = find_card(view, card)
                except ValueError:
                    if kind == "dng":
                        raise
                    # a dim display image: retry on a contrast-stretched grey view
                    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
                    lo, hi = np.percentile(g, (0.5, 99.5))
                    view = Path(td) / "stretched.png"
                    cv2.imwrite(str(view), np.clip((g - lo) / max(hi - lo, 1) * 255, 0, 255).astype(np.uint8))
                    rec = find_card(view, card)
            H = homography(card, np.asarray(rec["quad_raw"], float))
            stats = {p.id: sample(data, H, p.box) for p in card.patches}
            means = {k: np.asarray(s["mean"], float) for k, s in stats.items()}
            clipped = [k for k, s in stats.items()
                       if (kind == "dng" and max(s["mean"]) >= 0.999) or (kind != "dng" and max(s["mean"]) >= 254)]
        except Exception as exc:  # noqa: BLE001 - one bad image must not stop the batch
            print(f"{img_path.name}: FAILED: {exc}")
            summary_rows.append({"image": img_path.name, "input": kind, "path": "", "error": str(exc)})
            continue
        overlay(cv2.cvtColor(img8, cv2.COLOR_RGB2BGR), H, card, out / f"{img_path.stem}_overlay.jpg")
        cells = []
        for path, xyz in paths_for(kind, means, meta, calib).items():
            lab = to_lab_exposed(xyz, truth)
            summ, rows = score(lab, truth, design, colours)
            summary_rows.append({"image": img_path.name, "input": kind, "path": path,
                                 "tags": " ".join(map(str, rec["tags_found"])), **summ,
                                 "clipped_patches": " ".join(clipped), "truth": truth_label, "error": ""})
            patch_rows += [{"image": img_path.name, "path": path, **r} for r in rows]
            sw = "".join(
                f'<tr><td>{r["patch"]}</td><td><span class=sw style="background:{lab_hex(truth[r["patch"]])}"></span>'
                f'<span class=sw style="background:{lab_hex(lab[r["patch"]])}"></span></td>'
                f'<td class=num>{r["L"]:.1f} / {r["a"]:.1f} / {r["b"]:.1f}</td><td class=num><b>{r["de00"]:.1f}</b></td>'
                f'<td class=num>{r["de00_nolightness"]:.1f}</td><td class=num>{r["de00_vs_design"]:.1f}</td></tr>' for r in rows)
            cells.append(f'<div><h4>{path}: ΔE00 mean {summ["de00_mean"]} · worst {summ["de00_worst"]} ({summ["worst_patch"]}) · hue-chroma {summ["de00_nolightness_mean"]}</h4>'
                         f'<div class=wrap><table><tr><th>patch</th><th>truth | measured</th><th class=num>L* / a* / b*</th><th class=num>ΔE00</th><th class=num>hue-chroma</th><th class=num>vs design</th></tr>{sw}</table></div></div>')
        ov = base64.b64encode((out / f"{img_path.stem}_overlay.jpg").read_bytes()).decode()
        sheets.append(f'<section><h3>{img_path.name}</h3><p class=muted>tags {rec["tags_found"]} · {rec["locate_method"]}'
                      f'{" · clipped: " + ", ".join(clipped) if clipped else ""}</p>'
                      f'<img src="data:image/jpeg;base64,{ov}" alt="{img_path.name} with patch boxes"><div class=grid>{"".join(cells)}</div></section>')
        print(f"{img_path.name}: " + "; ".join(f"{r['path']} ΔE00 {r['de00_mean']} (worst {r['de00_worst']} {r['worst_patch']})"
                                              for r in summary_rows if r["image"] == img_path.name))
    keys = sorted({k for r in summary_rows for k in r}, key=lambda k: list(summary_rows[0]).index(k) if k in summary_rows[0] else 99)
    with open(out / "received_scores.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(summary_rows)
    (out / "received_scores.json").write_text(json.dumps({"truth": truth_label, "card": str(a.card),
                                                          "rows": summary_rows}, indent=1))
    if patch_rows:
        with open(out / "patches.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(patch_rows[0]))
            w.writeheader()
            w.writerows(patch_rows)
    page = """<title>Received Card Score</title>
<style>
:root{--bg:#f2f4f6;--panel:#fff;--ink:#141b21;--ink2:#4a5761;--rule:#d5dce1;--acc:#1f6f8b;--accbg:#e2eff4}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b0bcc5;--rule:#2b353d;--acc:#62bcd6;--accbg:#12303a}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b0bcc5;--rule:#2b353d;--acc:#62bcd6;--accbg:#12303a}
body{background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:18px 40px}
main{max-width:1240px;margin:0 auto;display:grid;gap:14px} h1{font-size:1.35rem;margin:0} h3{margin:0;font-size:1rem} h4{margin:0 0 4px;font-size:.88rem}
section{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:12px;display:grid;gap:8px} .rec{border-left:4px solid var(--acc);background:var(--accbg)}
.muted{color:var(--ink2)} p{margin:0;max-width:120ch} img{max-width:100%;border-radius:4px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px} .wrap{overflow-x:auto}
table{border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:.8rem} th,td{padding:3px 6px;border-bottom:1px solid var(--rule);text-align:left} .num{text-align:right}
.sw{display:inline-block;width:26px;height:18px;border:1px solid var(--rule);vertical-align:middle}
</style>""" + f"""<main><h1>V3 card colour error in received images</h1>
<section class=rec><p><b>Truth:</b> {truth_label}. ΔE2000 on the 8 colour patches after one exposure scale (gray_light's Y set to the truth's), no colour change; the greys are anchors. Hue-chroma = ΔE2000 with L* set to the truth's. Paths: dng_camera = the DNG's own ColorMatrix1 (the unit's CCM + AWB); dng_cardwb = the same with WB on the V3 greys; dng_calib = WB on the greys + the 2026-10-06 IMX708 bench matrix; display = the 8-bit sRGB image.</p></section>
{"".join(sheets)}</main>"""
    (out / "index.html").write_text(page, encoding="utf-8")
    print("wrote", out)
    return 0 if all(not r.get("error") for r in summary_rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
