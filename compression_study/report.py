"""Build results.csv, the decision gate, versions.json, inventory.json and report.html from the
per-frame-set rows in ``work/rows``.

    python -m compression_study.report --data <primary>/data/s4_20260930

Summary values at a target come from interpolation in log(bpp) between the rows that bracket
it (integer-quality codecs) or from the single row inside ±5 %.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import math
import platform
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "src"), str(REPO)]
from compression_study.common import STUDY_LIB, has_tool, tool  # noqa: E402

sys.path.insert(0, str(STUDY_LIB))
import numpy as np  # noqa: E402

STUDY = REPO / "compression_study"
BPP = {"T1": 0.4, "T2": 0.8, "T3": 1.6}
CAMS = ("imx708", "n6", "ae3")
CAM_NAME = {"imx708": "IMX708 (Pi)", "n6": "OpenMV N6", "ae3": "OpenMV AE3"}
CONDS = ("air", "uw")
COND_NAME = {"air": "In air (as captured)", "uw": "Underwater-sim (red ×0.14 at capture)"}
RAW_FAMILIES = ("N", "D", "D2")  # the spec's "best raw method" pool
PROCESSED = ("M1", "M1-444", "M1j", "M1-fix", "M2", "M2h")
CHART_LINES = [  # (key, label) — ≤ 8 lines, fixed order = fixed colour slot
    ("M1|", "M1 JPEG"), ("M1-fix|", "M1-fix JPEG"), ("M1j|", "M1j jpegli"),
    ("M2h|", "M2h HEIC"), ("D|D", "D sqrt+JPEG"), ("D2|D2/{m}", "D2 sqrt+JXL"),
    ("W|W", "W wl53 (own C)"), ("L|L", "L linear JXL")]
SLOTS_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7",
               "#e34948"]
SLOTS_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9",
              "#e66767"]


# ------------------------------------------------------------------ data

def load_rows(work: Path) -> tuple[list[dict], dict]:
    rows, meta = [], {}
    for f in sorted((work / "rows").glob("*.json")):
        if f.name.endswith(".partial.json"):  # crash-safety copies, superseded by the full file
            continue
        d = json.loads(f.read_text())
        rows += d["rows"]
        if not d["meta"].get("extra"):  # later-added methods keep the Phase 1 meta
            meta[d["meta"]["fsid"]] = d["meta"]
    return rows, meta


def key(r: dict) -> str:
    return f"{r['method']}|{r.get('variant', '')}"


def at_target(rows: list[dict], fsid: str, k: str, target: str, metric: str):
    """Metric at the exact target bytes: the row inside ±5 %, else log-bpp interpolation."""
    cand = [r for r in rows if r.get("fsid") == fsid and key(r) == k and r["target"] == target
            and r.get(metric) is not None and r.get("bytes")]
    if not cand:
        return None
    hit = [r for r in cand if r.get("hit")]
    if hit:
        return float(hit[0][metric])
    if len(cand) == 2:
        tb = cand[0]["target_bytes"]
        (a, b) = sorted(cand, key=lambda r: r["bytes"])
        if a["bytes"] <= tb <= b["bytes"] and a["bytes"] != b["bytes"]:
            la, lb = math.log(a["bytes"]), math.log(b["bytes"])
            w = (math.log(tb) - la) / (lb - la)
            return float(a[metric] + w * (b[metric] - a[metric]))
    return None  # unreachable: no value at this target


def variants(rows, fsid, families) -> list[str]:
    return sorted({key(r) for r in rows if r.get("fsid") == fsid and r["method"] in families
                   and r.get("family") == "raw"})


# ------------------------------------------------------------------ gate

def official_gate(rows, fsid) -> dict:
    """Owner's §8, verbatim: best raw (N/D/D2, any variant) vs M1 at T1 or T2."""
    best = None
    for t in ("T1", "T2"):
        m1_en = at_target(rows, fsid, "M1|", t, "red_err_noise")
        m1_de = at_target(rows, fsid, "M1|", t, "stress_de_mean")
        if m1_en is None:
            continue
        for k in variants(rows, fsid, RAW_FAMILIES):
            en = at_target(rows, fsid, k, t, "red_err_noise")
            de = at_target(rows, fsid, k, t, "stress_de_mean")
            if en is None or de is None:
                continue
            en_ratio = m1_en / max(en, 1e-9)
            de_red = 1 - de / max(m1_de, 1e-9)
            ok = en_ratio >= 2 or de_red >= 0.30
            cand = {"target": t, "variant": k.split("|")[1], "m1_red_en": m1_en, "red_en": en,
                    "en_ratio": en_ratio, "m1_stress_de": m1_de, "stress_de": de,
                    "de_reduction": de_red, "pass": ok}
            score = (ok, de_red)
            if best is None or score > (best["pass"], best["de_reduction"]):
                best = cand
    return best or {"pass": False, "note": "no comparable rows"}


def supporting_gate(rows, fsid, d2_mode) -> dict:
    """Pre-registered D2/eq vs the best processed baseline on block metrics, with the repeat
    floor: block ΔE00 ≥ 30 % lower (or block red error ≥ 2× lower) AND the ΔE gap larger than
    a second exposure's ΔE (M0-r1)."""
    floor = next((r for r in rows if r.get("fsid") == fsid and r["method"] == "M0-r1"), None)
    out = {}
    for t in ("T1", "T2"):
        d2 = {m: at_target(rows, fsid, f"D2|D2/{d2_mode}", t, m)
              for m in ("block_de_med", "bm_err_R_med", "block_de_stress_med")}
        base = {}
        for p in PROCESSED:
            v = at_target(rows, fsid, f"{p}|", t, "block_de_med")
            if v is not None:
                base[p] = (v, at_target(rows, fsid, f"{p}|", t, "bm_err_R_med"))
        if d2["block_de_med"] is None or not base:
            continue
        bp = min(base, key=lambda p: base[p][0])
        bde, bbm = base[bp]
        gap = bde - d2["block_de_med"]
        ok = ((d2["block_de_med"] <= 0.7 * bde or d2["bm_err_R_med"] * 2 <= bbm)
              and gap > (floor["block_de_med"] if floor else 0))
        out[t] = {"best_processed": bp, "base_block_de": bde, "d2_block_de": d2["block_de_med"],
                  "base_bm_err_R": bbm, "d2_bm_err_R": d2["bm_err_R_med"],
                  "repeat_floor_block_de": floor["block_de_med"] if floor else None,
                  "pass": ok}
    return out


# ------------------------------------------------------------------ charts (inline SVG)

def chart_svg(rows, fsid, metric, ylab, d2_mode, rd_floor: bool, width=440, height=270):
    pad_l, pad_r, pad_t, pad_b = 48, 12, 12, 40
    pts = {}
    for i, (k, label) in enumerate(CHART_LINES):
        kk = k.format(m=d2_mode)
        rs = sorted([r for r in rows if r.get("fsid") == fsid and key(r) == kk
                     and r.get("bytes") and r.get(metric) is not None
                     and r["target"] in ("T1", "T2", "T3")], key=lambda r: r["bpp"])
        if rs:
            pts[i] = (label, [(r["bpp"], r[metric], r["target"]) for r in rs])
    if not pts:
        return "<p class='muted'>no data</p>"
    xs = [p[0] for _, ps in pts.values() for p in ps]
    x0, x1 = math.log10(min(xs + [0.3]) * 0.9), math.log10(max(xs + [1.7]) * 1.1)
    # one runaway line (e.g. M1-fix under water) would flatten the rest: put it off-scale
    peaks = sorted(((max(p[1] for p in ps), lbl) for lbl, ps in pts.values()), reverse=True)
    off = []
    y1 = peaks[0][0] * 1.1
    if len(peaks) > 1 and peaks[0][0] > 3 * peaks[1][0]:
        cut = peaks[1][0] * 1.3
        off = [(lbl, v) for v, lbl in peaks if v > cut]
        y1 = cut
    y0 = 0.0
    W, H = width - pad_l - pad_r, height - pad_t - pad_b
    sx = lambda x: pad_l + (math.log10(x) - x0) / (x1 - x0) * W  # noqa: E731
    sy = lambda y: pad_t + H - (min(y, y1) - y0) / (y1 - y0) * H  # noqa: E731
    o = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
         f'aria-label="{html.escape(ylab)} vs bits per pixel">']
    for ty in _ticks(y0, y1):
        o.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{sy(ty):.1f}" y2="{sy(ty):.1f}" '
                 f'class="grid"/><text x="{pad_l - 6}" y="{sy(ty) + 4:.1f}" class="tick" '
                 f'text-anchor="end">{ty:g}</text>')
    for tx in (0.03, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2):
        if x0 <= math.log10(tx) <= x1:
            o.append(f'<line x1="{sx(tx):.1f}" x2="{sx(tx):.1f}" y1="{pad_t}" '
                     f'y2="{pad_t + H}" class="grid"/><text x="{sx(tx):.1f}" '
                     f'y="{pad_t + H + 14}" class="tick" text-anchor="middle">{tx:g}</text>')
    o.append(f'<text x="{pad_l + W / 2}" y="{height - 6}" class="axis" '
             f'text-anchor="middle">bits per Bayer pixel (log)</text>')
    if rd_floor:  # white-noise rate-distortion bound if red gets its equal share of bits
        seg = [(b, 2 ** (-b)) for b in np.geomspace(10 ** x0, 10 ** x1, 40)]
        d = " ".join(f"{'M' if i == 0 else 'L'}{sx(b):.1f},{sy(v):.1f}" for i, (b, v)
                     in enumerate(seg) if v <= y1)
        o.append(f'<path d="{d}" class="bound"/>')
        o.append(f'<text x="{pad_l + 4}" y="{sy(min(2 ** (-10 ** x0), y1)) - 4:.1f}" '
                 f'class="tick">R(D) bound</text>')
        if 0.5 <= y1:
            o.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{sy(0.5):.1f}" '
                     f'y2="{sy(0.5):.1f}" class="goal"/><text x="{width - pad_r - 2}" '
                     f'y="{sy(0.5) - 4:.1f}" class="tick" text-anchor="end">below noise (0.5)'
                     f'</text>')
    for i, (label, ps) in pts.items():
        d = " ".join(f"{'M' if j == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}"
                     for j, (x, y, _) in enumerate(ps))
        o.append(f'<path d="{d}" class="line s{i}"/>')
        for x, y, t in ps:
            tip = html.escape(f"{label} · {t} · {x:.3f} bpp · {ylab} {y:.3g}")
            o.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4" class="pt s{i}" '
                     f'data-tip="{tip}"/>')
    if off:
        txt = "off scale: " + ", ".join(f"{lbl} up to {v:.3g}" for lbl, v in off)
        o.append(f'<text x="{width - pad_r - 2}" y="{pad_t + 10}" class="tick" '
                 f'text-anchor="end">{html.escape(txt)}</text>')
    o.append("</svg>")
    return "".join(o)


def _ticks(lo, hi):
    span = hi - lo
    step = 10 ** math.floor(math.log10(span / 4))
    for m in (1, 2, 5, 10):
        if span / (step * m) <= 6:
            step *= m
            break
    return [round(lo + i * step, 6) for i in range(int(span / step) + 1)]


# ------------------------------------------------------------------ inventory & versions

def versions() -> dict:
    v = {"python": sys.version.split()[0], "platform": platform.platform()}
    for name, args in (("cjxl", ["--version"]), ("cjpeg", ["-version"]),
                       ("ffmpeg", ["-version"]), ("cjpegli", ["-h"])):
        if has_tool(name):
            try:
                r = subprocess.run([tool(name), *args], capture_output=True, text=True,
                                   timeout=20)
                txt = (r.stdout + r.stderr).strip().splitlines()
                v[name] = txt[0] if txt else "?"
            except Exception as exc:  # noqa: BLE001
                v[name] = f"error: {exc}"
    if "cjpegli" in v:
        v["cjpegli"] = "jpegli from libjxl v0.11.1 source (built 2026-09-30, .study-pylib/bin)"
    for mod in ("numpy", "cv2", "imagecodecs", "pillow_heif", "tifffile", "yaml"):
        try:
            m = __import__(mod)
            v[mod] = getattr(m, "__version__", "?")
            if mod == "pillow_heif":
                v["libheif"] = m.libheif_info().get("libheif", "?")
            if mod == "imagecodecs":
                v["charls"] = m.charls_version() if hasattr(m, "charls_version") else "?"
        except Exception as exc:  # noqa: BLE001
            v[mod] = f"missing ({exc})"
    try:
        ff = subprocess.run([tool("ffmpeg"), "-hide_banner", "-h", "encoder=libx264"],
                            capture_output=True, text=True, timeout=20).stdout
        v["x264"] = "libx264 via ffmpeg (" + ("GPL build" if "libx264" in ff else "?") + ")"
    except Exception:  # noqa: BLE001
        pass
    return v


def inventory(data: Path, meta: dict) -> dict:
    from compression_study.common import from_rawframe, split
    from nereus_camera_test_rig.color.raw_io import read_dng, read_openmv_bayer
    out = {}
    for cam, ext, rd in (("imx708", "dng", read_dng), ("n6", "bayer", read_openmv_bayer),
                         ("ae3", "bayer", read_openmv_bayer)):
        per = {}
        for ill in ("cool", "warm"):
            folder = data / f"{ill}_{cam}"
            frames = sorted(folder.glob(f"stop_*_r*.{ext}"))
            stops = {}
            for f in frames:
                stem = f.stem
                if not stem.endswith("_r0"):
                    continue
                fr = rd(f)
                raw = from_rawframe(fr, cam)
                pl = split(raw.mosaic, raw.cfa)
                side = {}
                js = f.with_suffix(".json")
                if js.exists():
                    side = json.loads(js.read_text())
                stops[stem.removesuffix("_r0")] = {
                    "mean_DN": {k: round(float(v.mean()) - raw.black, 1) for k, v in pl.items()},
                    "clipped_pct": {k: round(float((v >= raw.white).mean() * 100), 3)
                                    for k, v in pl.items()},
                    "low_signal": {k: bool(float(v.mean()) - raw.black
                                           < (10 if cam != "imx708" else 2 * 2))
                                   for k, v in pl.items()},
                    "exposure": side.get("ExposureTime") or side.get("exposure_us"),
                    "gain": side.get("AnalogueGain") or side.get("gain_db")}
            per[ill] = {"frames": len(frames), "stops": stops}
        fr0 = rd(next((data / f"cool_{cam}").glob(f"stop_-1_r0.{ext}")))
        out[cam] = {"format": "DNG (rpicam-still --raw)" if cam == "imx708"
                    else "raw Bayer dump + JSON sidecar (OpenMV capture_raw)",
                    "size": f"{fr0.mosaic.shape[1]}x{fr0.mosaic.shape[0]}",
                    "stored": "10-bit values in a 16-bit container" if cam == "imx708"
                    else "8-bit", "cfa": fr0.cfa, "black": fr0.black_level[0],
                    "white": fr0.white_level, "illuminants": per}
    return out


# ------------------------------------------------------------------ csv

CSV_FIRST = ["fsid", "camera", "illuminant", "stop", "condition", "method", "variant",
             "family", "target", "target_bytes", "bytes", "bpp", "hit", "red_err_noise",
             "stress_de_mean", "bm_err_R_med", "block_de_med", "block_de_p95",
             "block_de_stress_med", "patch_de_mean", "ssim_full", "ssim_tex", "enc_s", "dec_s",
             "knob", "note"]


def write_csv(rows: list[dict], path: Path) -> None:
    cols = CSV_FIRST + sorted({k for r in rows for k in r} - set(CSV_FIRST))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.6g}" if isinstance(v, float) else v) for k, v in r.items()})


# ------------------------------------------------------------------ html

def img64(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def fmt(v, nd=2):
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{nd}f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--work", type=Path, default=STUDY / "work")
    ap.add_argument("--out", type=Path, default=STUDY / "results")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    rows, meta = load_rows(args.work)
    modes = json.loads((args.work / "d2_modes.json").read_text())
    write_csv(rows, args.out / "results.csv")
    vers = versions()
    (args.out / "versions.json").write_text(json.dumps(vers, indent=1))
    inv = inventory(args.data, meta)
    (args.out / "inventory.json").write_text(json.dumps(inv, indent=1))
    gate = build_gate(rows, meta, modes)
    (args.out / "gate.json").write_text(json.dumps(gate, indent=1, default=float))
    page = render_html(rows, meta, modes, gate, inv, vers, args.work)
    (args.out / "report.html").write_text(page)
    print(json.dumps(gate["verdict"], indent=1))
    print(f"wrote {args.out / 'report.html'} ({len(page) / 1e6:.1f} MB), "
          f"{len(rows)} rows → results.csv")
    return 0


def build_gate(rows, meta, modes) -> dict:
    out: dict = {"per_frameset": {}, "verdict": {}}
    for cam in CAMS:
        for cond in CONDS:
            res = []
            for ill in ("cool", "warm"):
                fsid = f"{cam}_{ill}_s-1_{cond}"
                if fsid not in meta:
                    continue
                g = official_gate(rows, fsid)
                s = supporting_gate(rows, fsid, meta[fsid]["d2_mode"])
                out["per_frameset"][fsid] = {"official": g, "supporting": s}
                res.append((g.get("pass", False), any(v["pass"] for v in s.values())))
            if res:
                out["verdict"][f"{cam}_{cond}"] = {
                    "official_pass": all(a for a, _ in res),
                    "supporting_pass": all(b for _, b in res), "n_framesets": len(res)}
    # side questions (spec §8)
    out["below_noise_red_at_or_below_T2"] = [
        {"fsid": r["fsid"], "method": key(r), "bpp": r["bpp"],
         "red_err_noise": r["red_err_noise"]}
        for r in rows if r.get("bpp") and r["bpp"] <= 0.84 and r.get("red_err_noise") is not None
        and r["red_err_noise"] <= 0.5 and r["method"] not in ("M0-r1",)]
    margins = {}
    for fsid in sorted({r["fsid"] for r in rows if r.get("fsid") and "_field" not in r["fsid"]}):
        if "_s-1_" not in fsid:
            continue
        for t in ("T1", "T2"):
            best = {}
            # hardware-friendly JPEG family (D, D-j, D-lin) vs the JPEG XL family (D2, D2-lin)
            for fam, methods in (("D", ("D", "D-j", "D-lin")), ("D2", ("D2", "D2-lin"))):
                vals = [at_target(rows, fsid, k, t, "stress_de_mean")
                        for k in variants(rows, fsid, methods)]
                vals = [v for v in vals if v is not None]
                if vals:
                    best[fam] = min(vals)
            if len(best) == 2:
                margins[f"{fsid}@{t}"] = 1 - best["D2"] / best["D"]
    out["d2_vs_d_margin"] = margins  # 1 − best D2-family stress ΔE / best D-family stress ΔE
    sizes = defaultdict(dict)
    for r in rows:
        if r.get("family") in ("lossless", "near-lossless") and "_s-1_air" in r.get("fsid", ""):
            sizes[r["fsid"]][r["variant"]] = r["bpp"]
    out["lossless_vs_near_lossless_bpp"] = sizes
    return out


def render_html(rows, meta, modes, gate, inv, vers, work: Path) -> str:
    css = CSS
    parts = [f"<title>Raw Compression Study</title><style>{css}</style>",
             '<link rel="preconnect" href="https://fonts.googleapis.com">',
             '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family='
             'IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">',
             "<main>"]
    parts.append(HEADER)
    parts.append(gate_section(gate, modes))
    parts.append(summary_section(rows, meta, modes))
    parts.append(chart_section(rows, meta, modes))
    parts.append(lossless_section(rows))
    parts.append(field_section(rows))
    parts.append(phase2_section(work / "phase2"))
    parts.append(crop_section(work, meta))
    parts.append(HOW_IT_WORKS)
    parts.append(inventory_section(inv, meta))
    parts.append(methods_section(vers, modes))
    parts.append("</main><div id='tip' hidden></div>" + JS)
    return "\n".join(parts)


def gate_section(gate, modes) -> str:
    rows = []
    for cam in CAMS:
        for cond in CONDS:
            v = gate["verdict"].get(f"{cam}_{cond}")
            if not v:
                continue
            details = []
            for ill in ("cool", "warm"):
                g = gate["per_frameset"].get(f"{cam}_{ill}_s-1_{cond}", {}).get("official", {})
                if "variant" in g:
                    details.append(f"{ill}: {g['variant']} @ {g['target']} — red err/noise "
                                   f"{g['red_en']:.2f} vs M1 {g['m1_red_en']:.2f} "
                                   f"({g['en_ratio']:.2f}×), stress ΔE {g['stress_de']:.2f} vs "
                                   f"{g['m1_stress_de']:.2f} ({g['de_reduction'] * 100:+.0f} %)")
            sup = []
            for ill in ("cool", "warm"):
                s = gate["per_frameset"].get(f"{cam}_{ill}_s-1_{cond}", {}).get("supporting", {})
                for t, x in s.items():
                    sup.append(f"{ill} {t}: D2 block ΔE {x['d2_block_de']:.2f} vs "
                               f"{x['best_processed']} {x['base_block_de']:.2f} "
                               f"(repeat floor {fmt(x['repeat_floor_block_de'])})")
            rows.append(
                f"<tr><th>{CAM_NAME[cam]}</th><td>{COND_NAME[cond]}</td>"
                f"<td>{_pill(v['official_pass'])}</td><td>{_pill(v['supporting_pass'])}</td>"
                f"<td class='small'>{'<br>'.join(html.escape(d) for d in details)}"
                f"<details><summary>supporting detail</summary>"
                f"{'<br>'.join(html.escape(s) for s in sup)}</details></td></tr>")
    return (f"<section id='gate'><h2>Decision gate</h2><p class='lede'>"
            f"Official = the owner's §8 rule "
            f"verbatim: at 0.4 or 0.8 bpp, the best raw variant (N, D or D2) beats M1 by ≥ 2× on "
            f"per-pixel red error/noise <em>or</em> ≥ 30 % on mean stress ΔE2000. It must hold on "
            f"both illuminants. Supporting = the pre-registered D2/eq (JPEG XL mode "
            f"{html.escape(json.dumps(modes))}) against the best processed baseline on block "
            f"colour error, with the gap larger than a second exposure's own ΔE.</p>"
            f"<div class='scroll'><table><thead><tr><th>Camera</th><th>Condition</th>"
            f"<th>Official</th><th>Supporting</th><th>Best raw vs M1</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div></section>")


def _pill(ok: bool) -> str:
    return (f"<span class='pill {'pass' if ok else 'fail'}'>{'✓ pass' if ok else '✗ fail'}"
            f"</span>")


def summary_section(rows, meta, modes) -> str:
    out = ["<section id='summary'><h2>Best method per camera and size</h2><p class='lede'>"
           "Cool lamp, stop −1. Best = lowest mean stress ΔE2000 among all methods at that "
           "size; M1 shown for reference. Values interpolated at the exact target.</p>"]
    for cond in CONDS:
        out.append(f"<h3>{COND_NAME[cond]}</h3><div class='scroll'><table><thead><tr>"
                   "<th>Camera</th><th>Size</th><th>Best method</th><th class='n'>kB</th>"
                   "<th class='n'>red err/noise</th><th class='n'>stress ΔE</th>"
                   "<th class='n'>block ΔE</th><th class='n'>M1 red err/noise</th>"
                   "<th class='n'>M1 stress ΔE</th><th class='n'>M1 block ΔE</th>"
                   "</tr></thead><tbody>")
        for cam in CAMS:
            fsid = f"{cam}_cool_s-1_{cond}"
            if fsid not in meta:
                continue
            ks = sorted({key(r) for r in rows if r.get("fsid") == fsid
                         and r.get("family") in ("raw", "processed")})
            for t in ("T1", "T2", "T3"):
                cand = []
                for k in ks:
                    de = at_target(rows, fsid, k, t, "stress_de_mean")
                    if de is not None:
                        cand.append((de, k))
                if not cand:
                    continue
                de, k = min(cand)
                npx = next(r["width"] * r["height"] for r in rows if r.get("fsid") == fsid)
                kb = BPP[t] * npx / 8 / 1000
                out.append(
                    f"<tr><th>{CAM_NAME[cam]}</th><td>{t} · {BPP[t]} bpp</td>"
                    f"<td>{html.escape(k.split('|')[1] or k.split('|')[0])}</td>"
                    f"<td class='n'>{kb:.0f}</td>"
                    f"<td class='n'>{fmt(at_target(rows, fsid, k, t, 'red_err_noise'))}</td>"
                    f"<td class='n'>{fmt(de)}</td>"
                    f"<td class='n'>{fmt(at_target(rows, fsid, k, t, 'block_de_med'))}</td>"
                    f"<td class='n'>{fmt(at_target(rows, fsid, 'M1|', t, 'red_err_noise'))}</td>"
                    f"<td class='n'>{fmt(at_target(rows, fsid, 'M1|', t, 'stress_de_mean'))}</td>"
                    f"<td class='n'>{fmt(at_target(rows, fsid, 'M1|', t, 'block_de_med'))}</td>"
                    "</tr>")
        out.append("</tbody></table></div>")
    out.append("</section>")
    return "".join(out)


def chart_section(rows, meta, modes) -> str:
    legend = "".join(f"<span class='key'><i class='sw s{i}'></i>{html.escape(lbl)}</span>"
                     for i, (_, lbl) in enumerate(CHART_LINES))
    out = ["<section id='charts'><h2>Size vs red error</h2><p class='lede'>Cool lamp, stop −1. "
           "Left: per-pixel red error ÷ sensor noise (lower is better; dashed = the "
           "rate-distortion bound for white noise, dotted = the spec's 'below noise' line). "
           "Middle: mean stress ΔE2000 on patch means (red ×4, green ×1.5 before the colour "
           "matrix). Right: median 16×16-block ΔE2000, whole frame, no stress. Hover a point "
           "for its value. The IMX708 50 kB points are in the field-link table, not here.</p>",
           f"<div class='legend'>{legend}</div>"]
    for cam in CAMS:
        m = modes.get(cam, "vardct")
        for cond in CONDS:
            fsid = f"{cam}_cool_s-1_{cond}"
            if fsid not in meta:
                continue
            out.append(f"<h3>{CAM_NAME[cam]} · {COND_NAME[cond]}</h3><div class='grid3'>")
            for metric, lab, rd in (("red_err_noise", "red err/noise", True),
                                    ("stress_de_mean", "stress ΔE00", False),
                                    ("block_de_med", "block ΔE00", False)):
                out.append(f"<figure><figcaption>{lab}</figcaption>"
                           f"{chart_svg(rows, fsid, metric, lab, m, rd)}</figure>")
            out.append("</div>")
    out.append("</section>")
    return "".join(out)


def lossless_section(rows) -> str:
    out = ["<section id='lossless'><h2>Lossless and near-lossless sizes</h2><p class='lede'>"
           "In air, stop −1, both lamps averaged. C = the MCU-portable packer; N = square-root "
           "curve to b bits, then the same packer. Encode time on this Mac, single thread.</p>"
           "<div class='scroll'><table><thead><tr><th>Method</th>"]
    for cam in CAMS:
        out.append(f"<th class='n'>{CAM_NAME[cam]} bpp</th><th class='n'>red err/noise</th>"
                   f"<th class='n'>enc s</th>")
    out.append("</tr></thead><tbody>")
    labels = ["C", "C-jls", "C-png", "C2/e3", "C2/e7", "C2/e7/tiled", "N/b7", "N/b8", "N/b9",
              "N/b10"]
    for lab in labels:
        cells = []
        for cam in CAMS:
            rs = [r for r in rows if r.get("variant") == lab and r.get("camera") == cam
                  and r.get("condition") == "air" and r.get("stop") == -1]
            if rs:
                cells.append(f"<td class='n'>{np.mean([r['bpp'] for r in rs]):.2f}</td>"
                             f"<td class='n'>{np.mean([r['red_err_noise'] for r in rs]):.2f}</td>"
                             f"<td class='n'>{np.mean([r['enc_s'] or 0 for r in rs]):.2f}</td>")
            else:
                cells.append("<td class='n'>—</td>" * 3)
        out.append(f"<tr><th>{lab}</th>{''.join(cells)}</tr>")
    out.append("</tbody></table></div></section>")
    return "".join(out)


def field_section(rows) -> str:
    fr = [r for r in rows if str(r.get("fsid", "")).endswith("_field") and r.get("bytes")]
    if not fr:
        return ""
    out = ["<section id='field'><h2>IMX708 field link: 50 kB</h2><p class='lede'>The deployed "
           "recipe sends a 1600×900 crop, resized to 1000×562, as a ~50 kB JPEG. Here every "
           "method gets the same 1600×900 sensor crop (centred on the card and chart) and 50 kB."
           " D2/bin2 averages each Bayer plane 2×2 first, so it sends a quarter of the pixels "
           "like the resized JPEG does.</p><div class='scroll'><table><thead><tr><th>Lamp</th>"
           "<th>Condition</th><th>Method</th><th class='n'>kB</th><th class='n'>red err/noise"
           "</th><th class='n'>stress ΔE</th><th class='n'>block ΔE</th><th class='n'>SSIM tag"
           "</th></tr></thead><tbody>"]
    for r in sorted(fr, key=lambda r: (r["illuminant"], r["condition"], r["method"])):
        out.append(f"<tr><td>{r['illuminant']}</td><td>{r['condition']}</td>"
                   f"<td>{html.escape(r['method'] + ' ' + r['variant'])}</td>"
                   f"<td class='n'>{r['bytes'] / 1000:.1f}</td>"
                   f"<td class='n'>{fmt(r['red_err_noise'])}</td>"
                   f"<td class='n'>{fmt(r['stress_de_mean'])}</td>"
                   f"<td class='n'>{fmt(r['block_de_med'])}</td>"
                   f"<td class='n'>{fmt(r.get('ssim_tex'), 3)}</td></tr>")
    out.append("</tbody></table></div></section>")
    return "".join(out)


def phase2_section(d: Path) -> str:
    """Phase 2: what the encoders cost on the real devices (nereus002, 2026-10-01)."""
    out = ["<section id='devices'><h2>On the devices (Phase 2)</h2><p class='lede'>Measured on "
           "the rig, nereus002, 2026-10-01: the Pi Zero 2 W encoding last night's 12 MP IMX708 "
           "DNG with the Phase 1 settings (one core, each encoder capped at 250 MB of address "
           "space), and a MicroPython probe on each OpenMV board encoding a live HD Bayer frame "
           "(the room was dark: sizes below are a dark, high-gain scene, not the card). Every "
           "output was decoded on the Mac with the Phase 1 decoder.</p>"]
    bench = d / "pi" / "bench.json"
    if bench.exists():
        b = json.loads(bench.read_text())
        idle = next((r["load_w"] for r in b["steps"] if r["step"] == "idle"), None)
        out.append("<h3>IMX708 on the Pi Zero 2 W</h3><div class='scroll'><table><thead><tr>"
                   "<th>Step</th><th class='n'>seconds</th><th class='n'>peak RSS MB</th>"
                   "<th class='n'>bytes</th><th class='n'>bpp</th><th class='n'>ΔW vs idle</th>"
                   "<th>result</th></tr></thead><tbody>")
        for r in b["steps"]:
            rss = r.get("peak_rss") or r.get("peak_rss_self")
            dw = (r["load_w"] - idle) if (idle and r.get("load_w")) else None
            res = r.get("failed") or r.get("verify", "")
            out.append(f"<tr><th>{html.escape(r['step'])}</th>"
                       f"<td class='n'>{r['seconds']:.2f}</td>"
                       f"<td class='n'>{fmt(rss / 2 ** 20 if rss else None, 1)}</td>"
                       f"<td class='n'>{r.get('bytes') or r.get('dng_bytes') or '—'}</td>"
                       f"<td class='n'>{fmt(r.get('bpp'), 3)}</td><td class='n'>{fmt(dw)}</td>"
                       f"<td class='small'>{html.escape(str(res)[:160])}</td></tr>")
        out.append("</tbody></table></div>")
    ver = d / "verify_openmv.json"
    if ver.exists():
        v = json.loads(ver.read_text())
        out.append("<h3>OpenMV boards (MicroPython, firmware 1.28 / OpenMV v5.0.1)</h3>"
                   "<div class='scroll'><table><thead><tr><th>Step</th>")
        boards = [b for b in ("n6", "ae3") if b in v]
        out.append("".join(f"<th class='n'>{CAM_NAME[b]}</th>" for b in boards))
        out.append("</tr></thead><tbody>")

        def cell(b, f):
            try:
                return f(v[b])
            except (KeyError, IndexError, TypeError, ZeroDivisionError):
                return "—"
        rows = [("Snapshot, HD Bayer (ms)", _snap_ms),
                ("Exposure / gain (dark room)", _expo),
                ("Split into 4 planes, viper (ms)", _split_ms),
                ("sqrt LUT, 4 planes, viper (ms)", _lut_ms),
                ("4 grey JPEGs q70, D-lin (ms)", lambda x: _jp_ms(x, "D-lin", 70)),
                ("4 grey JPEGs q95, D-lin (ms)", lambda x: _jp_ms(x, "D-lin", 95)),
                ("Lossless packer, 4 planes, viper (ms)", _pack_ms),
                ("Lossless packer bits/sample (dark scene)", _pack_bps),
                ("Packer bit-exact + byte-identical to C", _pack_ok),
                ("Packer working memory (bytes)", _pack_mem),
                ("Demosaic + colour JPEG q70 (ms, today's path)", _rgb),
                ("Heap free after encode (MB)", _heap),
                ("LSC regs 0x0820–0x0833 / DENOISE_EN 0x0882", _regs),
                ("UPS power change while encoding (W)", _dw),
                ("Console chunks damaged (of 3 copies each)", lambda x: str(x["chunk_errors"])),
                ("Rig after the probe", lambda x: x["rig_status"])]
        for label, f in rows:
            tds = "".join(f"<td class='n'>{html.escape(str(cell(b, f)))}</td>" for b in boards)
            out.append(f"<tr><th>{html.escape(label)}</th>{tds}</tr>")
        out.append("</tbody></table></div>")
    out.append("</section>")
    return "".join(out)


def _snap_ms(x):
    return f"{x['results']['capture'][0]['snapshot_us'] / 1e3:.1f}"


def _expo(x):
    c = x["results"]["capture"][0]
    return f"{c['exposure_us']} µs / {c['gain_db']:.1f} dB"


def _split_ms(x):
    return f"{x['results']['planes'][0]['split_us'] / 1e3:.1f}"


def _lut_ms(x):
    return f"{x['results']['lut'][0]['us'] / 1e3:.1f}"


def _jp_ms(x, method, q):
    return f"{_jp(x, method, q)['total_us'] / 1e3:.1f}"


def _pack_ms(x):
    return f"{sum(p['us'] for p in x['results']['packer'][0]['planes'].values()) / 1e3:.0f}"


def _pack_bps(x):
    return f"{np.mean([p['bits_per_sample'] for p in x['packer_check'].values()]):.2f}"


def _pack_ok(x):
    ok = all(p.get("bit_exact") and p.get("byte_identical_to_c")
             for p in x["packer_check"].values())
    return "yes, 4/4 planes" if ok else "NO"


def _pack_mem(x):
    return f"{x['results']['packer'][0]['row_state_bytes']} + output"


def _heap(x):
    return f"{x['results']['mem'][-1]['free'] / 2 ** 20:.1f}"


def _jp(x, method, q):
    return next(s for s in x["results"]["jpeg_planes"][0] if s["method"] == method and s["q"] == q)


def _rgb(x):
    r = x["results"].get("rgb_jpeg")
    if not r:
        e = [m for m in x["errors"] if "rgb_jpeg" in m]
        return "not possible: " + (e[0].split(" ", 2)[-1][:60] if e else "?")
    q = next(s for s in r[0]["sweep"] if s["q"] == 70)
    return f"{r[0]['demosaic_us'] / 1e3:.0f} + {q['us'] / 1e3:.0f}"


def _regs(x):
    r = x["results"]["regs"][0]
    lsc = [v for k, v in r.items() if k != "0x0882"]
    return f"{sum(1 for v in lsc if v)} of {len(lsc)} non-zero / {r.get('0x0882')}"


def _dw(x):
    e = x["energy"]
    vals = [w.get("delta_w") for k, w in e.items() if k != "idle" and w.get("delta_w") is not None]
    return "within ±%.2f of idle (below the meter's resolution)" % max(abs(v) for v in vals) \
        if vals else "—"


def crop_section(work: Path, meta) -> str:
    order = ["M0", "M1", "M1-fix", "M1j", "M2h", "D", "D2_modular", "D2_vardct",
             "D2_modular_red+2", "D2_vardct_red+2", "L"]
    out = ["<section id='crops'><h2>What 0.4 bpp looks like</h2><p class='lede'>Cool lamp, "
           "stop −1, every method at T1. Each strip: grey 128 · red-orange patch · tag edge; top "
           "row normal render, bottom row with the red ×4, green ×1.5 stress gain. Pixels are "
           "enlarged without smoothing.</p>"]
    for cam in CAMS:
        for cond in CONDS:
            d = work / "crops" / f"{cam}_cool_s-1_{cond}"
            if not d.exists():
                continue
            out.append(f"<h3>{CAM_NAME[cam]} · {COND_NAME[cond]}</h3><div class='crops'>")
            for name in order:
                p = d / f"{name}.png"
                if p.exists():
                    out.append(f"<figure><img src='{img64(p)}' alt='{name} crop' "
                               f"loading='lazy'><figcaption>{html.escape(name.replace('_', ' '))}"
                               f"</figcaption></figure>")
            out.append("</div>")
    out.append("</section>")
    return "".join(out)


def inventory_section(inv, meta) -> str:
    out = ["<section id='inventory'><h2>Inventory (spec §2)</h2><div class='scroll'><table><thead>"
           "<tr><th>Camera</th><th>Format</th><th>Size</th><th>Stored</th><th>CFA</th>"
           "<th class='n'>Black</th><th class='n'>White</th><th>Frames</th></tr></thead><tbody>"]
    for cam, v in inv.items():
        n = ", ".join(f"{ill} {x['frames']}" for ill, x in v["illuminants"].items())
        out.append(f"<tr><th>{CAM_NAME[cam]}</th><td>{v['format']}</td><td>{v['size']}</td>"
                   f"<td>{v['stored']}</td><td>{v['cfa']}</td><td class='n'>{v['black']:g}</td>"
                   f"<td class='n'>{v['white']:g}</td><td>{n} (3 repeats per stop)</td></tr>")
    out.append("</tbody></table></div>")
    out.append(INVENTORY_NOTES)
    out.append("<h3>Mean signal above black and clipping, r0 per stop</h3><div class='scroll'>"
               "<table><thead><tr><th>Camera</th><th>Lamp</th><th>Stop</th><th class='n'>R</th>"
               "<th class='n'>G1</th><th class='n'>G2</th><th class='n'>B</th>"
               "<th class='n'>clipped G %</th><th class='n'>clipped R %</th></tr></thead><tbody>")
    for cam, v in inv.items():
        for ill, x in v["illuminants"].items():
            for st, s in sorted(x["stops"].items()):
                m, c = s["mean_DN"], s["clipped_pct"]
                out.append(f"<tr><th>{CAM_NAME[cam]}</th><td>{ill}</td><td>{st}</td>"
                           + "".join(f"<td class='n'>{m[k]:.1f}</td>" for k in
                                     ("R", "G1", "G2", "B"))
                           + f"<td class='n'>{c['G1']:.2f}</td><td class='n'>{c['R']:.2f}</td>"
                           "</tr>")
    out.append("</tbody></table></div>")
    noise_rows = []
    for fsid, mm in sorted(meta.items()):
        if "_s-1_" in fsid:
            a, c = mm["noise"]["a"], mm["noise"]["c"]
            noise_rows.append(f"<tr><td>{fsid}</td>" + "".join(
                f"<td class='n'>{a[k]:.4f}</td><td class='n'>{c[k]:.3f}</td>"
                for k in ("R", "G1", "B")) + "</tr>")
    out.append("<h3>Noise fit σ² = a·v + c (DN), from the three repeats</h3><div class='scroll'>"
               "<table><thead><tr><th>Frame set</th><th class='n'>a R</th><th class='n'>c R</th>"
               "<th class='n'>a G1</th><th class='n'>c G1</th><th class='n'>a B</th>"
               f"<th class='n'>c B</th></tr></thead><tbody>{''.join(noise_rows)}</tbody></table>"
               "</div></section>")
    return "".join(out)


def methods_section(vers, modes) -> str:
    v = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(x))}</td></tr>"
                for k, x in vers.items())
    return (f"<section id='methods'><h2>Assumptions and set-up</h2>{ASSUMPTIONS}"
            f"<h3>Tool versions</h3><div class='scroll'><table><tbody>{v}</tbody></table></div>"
            f"<p class='small'>JPEG XL mode chosen per camera on (cool, stop −1, in air) by the "
            f"lowest mean stress ΔE at T1 + T2: {html.escape(json.dumps(modes))}.</p></section>")


HEADER = """<header>
<p class="eyebrow">Nereus camera test rig · Phase 1 offline study · 2026-10-01</p>
<h1>Raw Compression Study</h1>
<p class="lede">Can raw Bayer data from the IMX708, OpenMV N6 and OpenMV AE3 be sent at
today's ~50 kB budget and rebuilt on the backend well enough for colour correction, and does
red survive better than with today's JPEG? Data: last night's S4 session (V1 card and a
24-patch chart at 0.5 m, two LED lamps, three locked repeats per stop), plus the same frames
made red-starved at capture to stand in for water.</p>
<nav><a href="#gate">Gate</a><a href="#summary">Best per size</a><a href="#charts">Charts</a>
<a href="#lossless">Lossless</a><a href="#field">Field 50 kB</a>
<a href="#devices">On the devices</a><a href="#crops">Crops</a>
<a href="#how">How it works</a><a href="#inventory">Inventory</a><a href="#methods">Set-up</a></nav>
</header>"""

HOW_IT_WORKS = """<section id="how"><h2>How each approach works</h2><div class="cards">
<div><h3>M1 · today's JPEG</h3><p>The camera first turns the sensor's mosaic into a normal
colour picture: it fills in the two missing colours at every pixel, scales red and blue so grey
looks grey, applies the sRGB gamma curve and rounds to 8 bits. JPEG then halves the colour
resolution (4:2:0) and throws away fine detail block by block. To compare it with raw, we undo
the known steps exactly and read back the value at each sensor site.</p></div>
<div><h3>M1-fix · JPEG with a fixed camera look</h3><p>Same as M1, but the white balance is fixed
(set in air) and a colour matrix converts to sRGB, as a real camera does. When red is weak the
matrix pushes it below zero and the 8-bit image clips it to black: that information is gone
before compression even starts.</p></div>
<div><h3>M1j · jpegli</h3><p>The same JPEG format written by a smarter encoder (from the JPEG XL
team) that spends bits where the eye notices them. Any JPEG decoder can read it.</p></div>
<div><h3>M2 / M2h · H.264 still and HEIC</h3><p>Video and phone-photo codecs used for a single
picture. They predict each block from its neighbours and code the difference, which beats JPEG
at small sizes. H.264 stands in for the N6's hardware video encoder.</p></div>
<div><h3>C · lossless packer</h3><p>Splits the mosaic into its four colour planes (R, G1, G2, B),
predicts each pixel from the three already-sent neighbours, and writes the small leftover with
a variable-length code that adapts as it goes. Nothing is lost, and it needs only two rows of
memory, so it fits a microcontroller.</p></div>
<div><h3>C-ref, C2 · lossless references</h3><p>JPEG-LS and PNG show what established lossless
formats get from the same planes; JPEG XL lossless (C2) is the strongest modern one, at a
fast and a slow setting.</p></div>
<div><h3>N · near-lossless</h3><p>Takes the square root of each value, rounds it to b bits, then
uses the lossless packer. Sensor noise grows like the square root of the signal, so the
rounding stays smaller than the noise at every brightness. The only loss is that rounding.</p>
</div>
<div><h3>D · square root + grayscale JPEGs</h3><p>The four planes are square-rooted to 8 bits
and each is saved as a grey JPEG. No demosaic, no colour conversion, no colour halving: red
gets its own plane and its own bits. Every camera chip that can make a JPEG can do this.</p>
</div>
<div><h3>D2 · square root + JPEG XL</h3><p>Same idea with 12-bit planes and JPEG XL, a newer
codec that keeps more precision per byte. Variants give red a bigger share of the bytes
(red+), merge the two greens (3pl), or code the planes as one tiled image.</p></div>
<div><h3>L · linear JPEG XL (existing prototype)</h3><p>The repo's September prototype: average
each 2×2 block into one RGB pixel, lift red with the white-balance gains, take the square root
and send 10-bit JPEG XL. Half the resolution, but red is boosted before rounding.</p></div>
</div></section>"""

INVENTORY_NOTES = """<ul class="notes">
<li><strong>IMX708:</strong> true pre-ISP Bayer (DNG CFA, 959 distinct codes 64–1023, no
gamma). Values never go below black (64): the readout clips there.</li>
<li><strong>N6 and AE3 — processed 8-bit Bayer.</strong> Not demosaiced, not white-balanced,
linear above ~20 DN (2.0× per stop), but: 8-bit only from the <code>csi</code> Bayer path;
black over-subtracted by ~1 DN and clipped at 0, so the response bends below ~10 DN; on-chip
<strong>lens-shading correction</strong> (temporal var/mean doubles from centre to corners;
upstream PAG7936 driver writes R_LSC_* 0x0820–0x0833) and <strong>on-chip denoise</strong>
(DENOISE_EN 0x0882 = 0x03 in HD; neighbouring noise correlated +0.4–0.5). The spec says to stop
and ask here; the owner was offline and had asked to continue, so OpenMV runs as its own
"processed 8-bit Bayer" class. Measured block noise on OpenMV is 2–3× the white-noise value
because of the denoise. Turning LSC/denoise off needs sensor register writes, which need the
owner's approval (Phase 2 only reads them).</li>
<li><strong>Clipping:</strong> stop 0 clips the lamp hot spot (IMX708 G 4.7 %, N6 G 10 %, AE3 G
13 %; AE3 warm R 10 %), so stop −1 is the primary frame set and every metric masks highlight
clipping. Clipping at 0 is never masked.</li>
<li><strong>Repeats:</strong> three locked repeats per stop on every camera (read-back exposure
and gain identical, means within 0.3 %). AE3 cool r2 is shifted ~0.1 px; the robust noise
estimator absorbs it. The warm lamp shows row banding on the IMX708 (removed before noise
estimation).</li></ul>"""

ASSUMPTIONS = """<ul class="notes">
<li>Inputs: copies of the S4 session (2026-09-30 02:42 UTC, nereus002) in the primary
checkout's <code>data/s4_20260930/</code>, every file checked against SHA256SUMS before use.
The originals were never touched.</li>
<li>Card and chart: V1 reference card (<code>configs/cards/nereus_v1.yaml</code>) and the Pixel
Perfect 24 chart, located automatically on M0 (<code>config/card_rois.yaml</code>). The colour
matrix in the stress test is fitted on M0 to the card's measured values and applied identically
to every reconstruction, so its absolute accuracy does not affect the comparison.</li>
<li>Noise: temporal, from the three repeats, per patch, robust to edges; the fit σ² = a·v + c
drives the underwater simulation and the bound line. Metrics use each patch's measured σ.</li>
<li>Underwater-sim: binomial thinning at capture (red × 0.14, blue × 0.8, from the TG-7 grey at
15.5 m), shot noise re-drawn at the new level, read noise kept, re-quantized with each sensor's
own clipping. M1 then white-balances on the starved grey (red × ~11–15 before 8-bit rounding),
which flatters it; M1-fix keeps the in-air white balance and matrix like a real camera.</li>
<li>Sizes count every byte needed to decode: payloads plus a compact varint header (method,
size, CFA, black/white, curve bits, WB/matrix parameters).</li>
<li>Rate control: within ±5 %; integer-quality codecs are bracketed and interpolated in
log(bpp). Unreachable targets are recorded, not forced.</li>
<li>JPEG XL takes PGM with maxval 2<sup>b</sup>−1 and runs single-threaded. x264 runs
Main profile, 4:2:0, <code>-tune psnr</code>, version SEI stripped; on the 12 MP IMX708 it
exceeds hardware H.264 levels, so it is a codec proxy only.</li>
<li>GPL tools (ffmpeg/x264, libheif/x265 in pillow-heif) ran only as internal Mac benchmarks
through separate processes or a study-only library path; nothing here ships, and
<code>src/</code> does not import this study.</li></ul>"""

CSS = """
:root{--bg:#f6f8f9;--panel:#ffffff;--ink:#121820;--ink2:#46525e;--muted:#6b7682;--rule:#d8dee4;
--accent:#0f6f7c;--pass:#1a7f3c;--fail:#b3261e;--passbg:#e3f3e8;--failbg:#fbe7e5;
--s0:#2a78d6;--s1:#eb6834;--s2:#1baf7a;--s3:#eda100;--s4:#e87ba4;--s5:#008300;--s6:#4a3aa7;
--s7:#e34948;--grid:#e6eaee;}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;
--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;--ink2:#b4c0c9;--muted:#8b98a3;--rule:#2a343c;
--accent:#4fb3bf;--pass:#5cc883;--fail:#ff8a80;--passbg:#163222;--failbg:#3a1a18;
--s0:#3987e5;--s1:#d95926;--s2:#199e70;--s3:#c98500;--s4:#d55181;--s5:#008300;--s6:#9085e9;
--s7:#e66767;--grid:#222b32;}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0f1418;--panel:#161d22;--ink:#e6ecf0;
--ink2:#b4c0c9;--muted:#8b98a3;--rule:#2a343c;--accent:#4fb3bf;--pass:#5cc883;--fail:#ff8a80;
--passbg:#163222;--failbg:#3a1a18;--s0:#3987e5;--s1:#d95926;--s2:#199e70;--s3:#c98500;
--s4:#d55181;--s5:#008300;--s6:#9085e9;--s7:#e66767;--grid:#222b32;}
body{background:var(--bg);color:var(--ink);font:15px/1.55 "IBM Plex Sans",system-ui,
-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:24px 64px}
main{max-width:1180px;margin:0 auto;display:grid;gap:40px}
h1{font-size:2rem;line-height:1.15;margin:4px 0 8px;font-weight:600;text-wrap:balance}
h2{font-size:1.35rem;margin:0 0 6px;font-weight:600;text-wrap:balance}
h3{font-size:1rem;margin:20px 0 8px;font-weight:600}
.eyebrow{text-transform:uppercase;letter-spacing:.08em;font-size:.75rem;color:var(--accent);
margin:0;font-weight:500}
.lede{color:var(--ink2);max-width:72ch;margin:0 0 12px}
nav{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:12px;font-size:.9rem}
nav a{color:var(--accent);text-decoration:none;border-bottom:1px solid transparent}
nav a:hover,nav a:focus-visible{border-color:var(--accent);outline:none}
.scroll{overflow-x:auto;background:var(--panel);border:1px solid var(--rule);border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:.86rem}
th,td{padding:7px 10px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}
thead th{font-weight:600;color:var(--ink2);background:var(--bg);white-space:nowrap}
tbody tr:last-child>*{border-bottom:none}
.n{text-align:right;font-family:"IBM Plex Mono",ui-monospace,monospace;
font-variant-numeric:tabular-nums;white-space:nowrap}
.small{font-size:.8rem;color:var(--ink2)}
details summary{cursor:pointer;color:var(--accent);margin-top:4px}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:.78rem;font-weight:600;
white-space:nowrap}
.pill.pass{background:var(--passbg);color:var(--pass)}.pill.fail{background:var(--failbg);
color:var(--fail)}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:.84rem;color:var(--ink2);
margin:4px 0 8px}
.key{display:inline-flex;align-items:center;gap:6px}
.sw{width:14px;height:3px;border-radius:2px;display:inline-block}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
figure{margin:0;background:var(--panel);border:1px solid var(--rule);border-radius:6px;
padding:8px}
figcaption{font-size:.8rem;color:var(--ink2);margin:0 0 4px}
.chart{width:100%;height:auto;display:block}
.chart .grid{stroke:var(--grid);stroke-width:1}
.chart .tick{fill:var(--muted);font:11px "IBM Plex Mono",ui-monospace,monospace}
.chart .axis{fill:var(--ink2);font-size:11px}
.chart .line{fill:none;stroke-width:2;stroke-linejoin:round}
.chart .pt{stroke:var(--panel);stroke-width:2}
.chart .bound{fill:none;stroke:var(--muted);stroke-width:1.5;stroke-dasharray:5 4}
.chart .goal{stroke:var(--muted);stroke-width:1;stroke-dasharray:1 3}
""" + "".join(f".s{i}{{stroke:var(--s{i})}}.pt.s{i},.sw.s{i}{{fill:var(--s{i});"
              f"background:var(--s{i})}}" for i in range(8)) + """
.crops{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.crops img{width:100%;image-rendering:pixelated;display:block;border-radius:3px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.cards>div{background:var(--panel);border:1px solid var(--rule);border-radius:6px;
padding:12px 14px}
.cards h3{margin:0 0 6px}.cards p{margin:0;color:var(--ink2);font-size:.9rem}
.notes{max-width:80ch;color:var(--ink2);padding-left:20px;display:grid;gap:6px}
code{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.85em}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--bg);font-size:12px;
padding:4px 8px;border-radius:4px;z-index:10;max-width:320px}
.muted{color:var(--muted)}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

JS = """<script>
(function(){const tip=document.getElementById('tip');
document.addEventListener('mouseover',e=>{const t=e.target.closest('[data-tip]');
if(!t){tip.hidden=true;return;}tip.textContent=t.dataset.tip;tip.hidden=false;});
document.addEventListener('mousemove',e=>{if(!tip.hidden){tip.style.left=(e.clientX+12)+'px';
tip.style.top=(e.clientY+12)+'px';}});})();
</script>"""


if __name__ == "__main__":
    raise SystemExit(main())
