"""Stage ``report`` v1 — one standard, self-contained HTML page per run (SPEC §4 Phase 8 S1.8).

Reads only saved stage outputs (``ingest``, ``locate``, ``distance``, ``patches``, ``qc``)
after checking they are fresh, so re-running ``report`` never redoes detection. Contents:

1. dataset summary tiles;
2. **"before" scores** — the as-shot camera JPEG scored by the §20 protocol
   (``metrics.score_srgb8``) on the patches qc kept, split **clean card / damaged card**,
   with frame and patch counts; ψ vs depth; a per-frame table (the table view);
3. the QC exclusion tables;
4. the dataset timeline (depth vs time per dive, sweeps joined);
5. per-sweep contact sheets: the card rectified from the camera JPEG, excluded patches
   crossed out, each tile captioned with depth, ``z`` and its before-scores.

The camera JPEG is used here because it *is* the thing being scored (the as-shot baseline,
SPEC §20); all measurements behind the exclusions came from the RAW.
Output: ``report/{index.html, scores_before.csv, summary.json, stage.json}``.
"""

from __future__ import annotations

import base64
import csv
import html
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from .card import Card, load_card
from .metrics import score_srgb8
from .patches import homography
from .stages import run_parallel, verify_fresh, write_stage

SERIES = {"1_reference_A_iso100": ("s1", "Reference (A mode)"),
          "2_underwater_preset": ("s2", "Olympus underwater preset"),
          "3_scene_card_offcenter": ("s3", "Card off-centre in scene")}
OTHER = ("other", "Other (surface card / no card)")
CONDITION = {"clean": ("s1", "Clean card (before the leak)"),
             "damaged": ("s2", "Damaged card")}
TILE_W, TILE_H = 360, 120
SCORE_COLUMNS = ["stem", "dive_id", "sweep_id", "category", "card_condition", "depth_m", "z_m",
                 "usable_patches", "anchor", "n_psi", "psi_median", "n_de", "de2000_median",
                 "de2000_p90"]


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def score_before(patches: dict, qc: dict, card: Card) -> dict[str, dict]:
    """As-shot camera JPEG scores per usable frame, using only the patches qc kept."""
    out = {}
    for stem, q in qc.items():
        jpeg = patches.get(stem, {}).get("jpeg")
        if not q["usable"] or jpeg is None:
            continue
        keep = {pid for pid, p in q["patches"].items() if p["usable"]}
        stats = {pid: s for pid, s in jpeg["patches"].items() if pid in keep and s.get("mean")}
        excluded = [pid for pid in q["patches"] if pid not in keep]
        anchor = "gray_mid" if "gray_mid" in keep else "gray_mid_right"
        s = score_srgb8({k: v["mean"] for k, v in stats.items()}, card, neutralized=(),
                        anchor=anchor, exclude=excluded,
                        stds={k: v["std"] for k, v in stats.items()})
        out[stem] = {**s, "usable_patches": len(keep), "card_condition": q["card_condition"]}
    return out


def _quantiles(values: list) -> str:
    if not values:
        return "–"
    q1, q2, q3 = np.percentile(values, [25, 50, 75])
    return f"{q2:.1f} <span class=\"muted\">({q1:.1f}–{q3:.1f})</span>"


def _score_table(scores: dict, rows: dict, key) -> str:
    groups: dict[Any, list] = {}
    for stem, s in scores.items():
        groups.setdefault(key(stem, s), []).append(s)
    body = []
    for k in sorted(groups, key=str):
        g = groups[k]
        psi = [s["psi_median"] for s in g if s["psi_median"] is not None]
        de = [s["de2000_median"] for s in g if s["de2000_median"] is not None]
        body.append(f"<tr><th>{_e(k)}</th><td>{len(g)}</td>"
                    f"<td>{sum(s['usable_patches'] for s in g)}</td>"
                    f"<td>{sum(s['n_psi'] for s in g)}</td><td>{_quantiles(psi)}</td>"
                    f"<td>{sum(s['n_de'] for s in g)}</td><td>{_quantiles(de)}</td></tr>")
    return ("<table><thead><tr><th></th><th>frames</th><th>usable patches</th><th>n ψ</th>"
            "<th>ψ median (IQR), °</th><th>n ΔE</th><th>ΔE00 median (IQR)</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


def _scatter(scores: dict, rows: dict, dist: dict) -> str:
    """ψ of the as-shot JPEG vs depth, one dot per usable frame, coloured by card condition."""
    pts = [(float(rows[s]["depth_m"]), v["psi_median"], s, v) for s, v in scores.items()
           if v["psi_median"] is not None]
    if not pts:
        return "<p class=\"note\">No frame could be scored.</p>"
    W, H, ML, MR, MT, MB = 720, 300, 44, 12, 12, 34
    xmax = max(p[0] for p in pts) * 1.05 or 1
    ymax = max(10.0, np.ceil(max(p[1] for p in pts) / 10) * 10)
    X = lambda v: ML + v / xmax * (W - ML - MR)  # noqa: E731
    Y = lambda v: H - MB - v / ymax * (H - MT - MB)  # noqa: E731
    parts = []
    for t in range(0, int(ymax) + 1, 10):
        parts.append(f'<line x1="{ML}" x2="{W - MR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" '
                     f'class="gridline"/><text x="{ML - 6}" y="{Y(t) + 4:.1f}" class="tick" '
                     f'text-anchor="end">{t}</text>')
    for d in range(0, int(xmax) + 1, 2):
        parts.append(f'<text x="{X(d):.1f}" y="{H - 14}" class="tick" '
                     f'text-anchor="middle">{d}</text>')
    parts.append(f'<line x1="{ML}" x2="{W - MR}" y1="{H - MB}" y2="{H - MB}" class="axis"/>')
    for depth, psi, stem, v in sorted(pts, key=lambda p: p[3]["card_condition"]):
        cls = CONDITION[v["card_condition"]][0]
        z = dist.get(stem, {}).get("z_m")
        tip = [f"ψ {psi:.1f}°", f"ΔE00 {v['de2000_median'] if v['de2000_median'] is not None else '–'}",
               f"{stem} · dive {rows[stem]['dive_id']}", f"depth {depth} m · z {z} m",
               CONDITION[v["card_condition"]][1]]
        parts.append(f'<circle cx="{X(depth):.1f}" cy="{Y(psi):.1f}" r="4" class="pt {cls}" '
                     f'tabindex="0" data-tip="{_e("|".join(tip))}"/>')
    legend = "".join(f'<span class="key"><i class="sw {c}"></i>{_e(n)}</span>'
                     for c, n in CONDITION.values())
    return (f'<div class="legend">{legend}</div><figure class="panel"><svg viewBox="0 0 {W} {H}" '
            f'role="img" aria-label="As-shot JPEG grey error psi versus depth">'
            f'<text x="4" y="{MT + 4}" class="tick">ψ °</text>{"".join(parts)}'
            f'<text x="{W - MR}" y="{H - 2}" class="tick" text-anchor="end">depth, m</text>'
            f'</svg></figure>')


def _timeline(rows: list, corners: dict, dist: dict, sites: dict) -> str:
    W, H, ML, MR, MT, MB = 560, 230, 44, 12, 12, 30
    t = lambda r: datetime.fromisoformat(r["time_utc"])  # noqa: E731
    maxdepth = max(float(r["depth_m"]) for r in rows) or 1
    panels = []
    for dive in sorted({r["dive_id"] for r in rows}, key=int):
        dr = sorted([r for r in rows if r["dive_id"] == dive], key=t)
        t0 = t(dr[0])
        span = max(1.0, (t(dr[-1]) - t0).total_seconds() / 60)
        x = lambda r: ML + (t(r) - t0).total_seconds() / 60 / span * (W - ML - MR)  # noqa: E731
        y = lambda r: MT + float(r["depth_m"]) / (maxdepth * 1.05) * (H - MT - MB)  # noqa: E731
        parts = []
        for d in range(0, int(maxdepth) + 1, 5):
            yy = MT + d / (maxdepth * 1.05) * (H - MT - MB)
            parts.append(f'<line x1="{ML}" x2="{W - MR}" y1="{yy:.1f}" y2="{yy:.1f}" '
                         f'class="gridline"/><text x="{ML - 6}" y="{yy + 4:.1f}" class="tick" '
                         f'text-anchor="end">{d}</text>')
        for m in range(0, int(span) + 1, 10):
            parts.append(f'<text x="{ML + m / span * (W - ML - MR):.1f}" y="{H - 10}" '
                         f'class="tick" text-anchor="middle">{m}</text>')
        sweeps: dict[str, list] = {}
        for r in dr:
            if r["sweep_id"]:
                sweeps.setdefault(r["sweep_id"], []).append(r)
        for srs in sweeps.values():
            if len(srs) > 1:
                pts = " ".join(f"{x(r):.1f},{y(r):.1f}" for r in srs)
                parts.append(f'<polyline points="{pts}" class="sweep"/>')
        for r in dr:
            cls, name = SERIES.get(r["category"], OTHER)
            loc = corners.get(r["stem"], {})
            z = dist.get(r["stem"], {}).get("z_m")
            tip = [f"{r['stem']} · {name}", f"depth {r['depth_m']} m · "
                   f"{t(r).strftime('%H:%M:%S')} UTC · sun {r['sun_elevation_deg']}°"]
            if r["sweep_id"]:
                tip.append(f"sweep {r['sweep_id']}")
            if loc:
                tip.append(f"card: {loc.get('locate_method') or 'unlocated'}"
                           + (f" · z {z} m" if z is not None else ""))
            parts.append(f'<circle cx="{x(r):.1f}" cy="{y(r):.1f}" r="4" class="pt {cls}" '
                         f'tabindex="0" data-tip="{_e("|".join(tip))}"/>')
        head = (f"Dive {dive} · {sites.get(dr[0]['site'], dr[0]['site'])} · "
                f"{t0.strftime('%H:%M')}–{t(dr[-1]).strftime('%H:%M')} UTC · sun "
                f"{float(dr[0]['sun_elevation_deg']):+.1f}° → "
                f"{float(dr[-1]['sun_elevation_deg']):+.1f}° · {len(sweeps)} sweeps · "
                f"{len(dr)} shots")
        panels.append(f'<figure class="panel"><figcaption>{_e(head)}</figcaption>'
                      f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{_e(head)}">'
                      f'<text x="4" y="{MT + 6}" class="tick">m</text>{"".join(parts)}'
                      f'<text x="{W - MR}" y="{H - 10}" class="tick" text-anchor="end">min'
                      f'</text></svg></figure>')
    legend = "".join(f'<span class="key"><i class="sw {c}"></i>{_e(n)}</span>'
                     for c, n in list(SERIES.values()) + [OTHER])
    return (f'<div class="legend">{legend}<span class="key"><svg width="22" height="10">'
            f'<line x1="0" x2="22" y1="5" y2="5" class="sweep"/></svg>sweep</span></div>'
            f'<div class="panels">{"".join(panels)}</div>')


def thumbnail(jpeg: Optional[Path], quad_jpeg, card: Card,
              excluded: list[str]) -> Optional[str]:
    """Base64 JPEG: the card rectified from the camera JPEG, excluded patches crossed out;
    or, for an unlocated frame, the whole frame."""
    if jpeg is None:
        return None
    if quad_jpeg is None:
        img = cv2.imread(str(jpeg), cv2.IMREAD_REDUCED_COLOR_8)
        if img is None:
            return None
        img = cv2.resize(img, (TILE_W, int(TILE_W * img.shape[0] / img.shape[1])),
                         interpolation=cv2.INTER_AREA)
    else:
        img = cv2.imread(str(jpeg), cv2.IMREAD_COLOR)
        if img is None:
            return None
        H = homography(card, np.asarray(quad_jpeg))
        rect = cv2.warpPerspective(img, H, (card.canonical_w, card.canonical_h),
                                   flags=cv2.WARP_INVERSE_MAP | cv2.INTER_AREA)
        for pid in excluded:
            box = next((p.box for p in (*card.patches, *card.sub_patches) if p.id == pid), None)
            if box is not None:
                a, b = (box.x + 12, box.y + 12), (box.x + box.w - 12, box.y + box.h - 12)
                cv2.line(rect, a, b, (59, 59, 208), 14, cv2.LINE_AA)  # status critical #d03b3b
                cv2.line(rect, (a[0], b[1]), (b[0], a[1]), (59, 59, 208), 14, cv2.LINE_AA)
        img = cv2.resize(rect, (TILE_W, TILE_H), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return base64.b64encode(buf).decode() if ok else None


def _contact_sheets(rows: dict, corners: dict, dist: dict, qc: dict, scores: dict,
                    thumbs: dict) -> str:
    groups: dict[tuple, list] = {}
    for stem in corners:
        r = rows[stem]
        groups.setdefault((int(r["dive_id"]), r["category"] != "1_reference_A_iso100",
                           r["category"], int(r["sweep_id"] or 0)), []).append(stem)
    out = []
    for (dive, _, cat, sweep), stems in sorted(groups.items()):
        tiles = []
        for stem in sorted(stems, key=lambda s: rows[s]["time_utc"]):
            r, rec, q, s = rows[stem], corners[stem], qc.get(stem), scores.get(stem)
            z = dist.get(stem, {}).get("z_m")
            lines = [f"<b>{_e(stem)}</b> · {_e(r['depth_m'])} m"
                     + (f" · z {z:.2f} m" if z is not None else "")]
            if not rec["located"]:
                state = "miss"
                lines.append(f"unlocated: {_e(rec.get('reason', ''))}")
            elif q is not None and not q["usable"]:
                state = "excl"
                lines.append(f"excluded: {_e('; '.join(q['reasons']))}")
            else:
                state = "ok"
                if s is not None:
                    psi = "–" if s["psi_median"] is None else f"{s['psi_median']:.1f}°"
                    de = "–" if s["de2000_median"] is None else f"{s['de2000_median']:.1f}"
                    lines.append(f"ψ {psi} · ΔE00 {de} · {s['usable_patches']} patches")
            img = thumbs.get(stem)
            pic = (f'<img alt="{_e(stem)}" src="data:image/jpeg;base64,{img}">' if img
                   else '<div class="noimg">no JPEG</div>')
            tiles.append(f'<div class="tile {state}">{pic}<div class="cap">'
                         f'{"<br>".join(lines)}</div></div>')
        title = f"sweep {sweep}" if sweep else SERIES.get(cat, OTHER)[1]
        out.append(f"<h3>Dive {dive} · {_e(title)} · {len(stems)} frames</h3>"
                   f'<div class="tiles">{"".join(tiles)}</div>')
    return "".join(out)


def _qc_tables(qsum: dict, card: Card) -> str:
    conds = [c for c in ("clean", "damaged") if c in qsum["by_card_condition"]]
    head = "".join(f"<th>{_e(CONDITION[c][1])}</th>" for c in conds)
    frames = "".join(
        f"<tr><th>{_e(k)}</th><td colspan=\"{len(conds)}\">{v}</td></tr>"
        for k, v in sorted(qsum["frame_exclusions"].items()))
    by = qsum["by_card_condition"]
    rows = [f"<tr><th>frames (usable)</th>" + "".join(
        f"<td>{by[c]['frames']} ({by[c]['usable_frames']})</td>" for c in conds) + "</tr>",
            "<tr><th>usable patches</th>" + "".join(
        f"<td>{by[c]['usable_patches']}</td>" for c in conds) + "</tr>"]
    for state in ("clean", "known", "cells", "unknown"):
        rows.append(f"<tr><th>damage: {state}</th>" + "".join(
            f"<td>{by[c]['damage'][state]}</td>" for c in conds) + "</tr>")
    ids = [p.id for p in card.patches] + [s.id for s in card.sub_patches]
    per = "".join(f"<tr><th>{_e(pid)}</th>" + "".join(
        f"<td>{by[c]['usable_by_patch'].get(pid, 0)} / {by[c]['usable_frames']}</td>"
        for c in conds) + "</tr>" for pid in ids)
    reasons = ", ".join(f"{k} {v}" for k, v in sorted(
        qsum["patch_exclusions_in_usable_frames"].items())) or "none"
    return (f"<div class=\"cols\"><table><thead><tr><th></th>{head}</tr></thead><tbody>"
            f"{''.join(rows)}</tbody></table><table><thead><tr><th>Frames excluded</th>"
            f"<th colspan=\"{len(conds)}\">n</th></tr></thead><tbody>{frames}</tbody></table>"
            f"</div><p class=\"note\">Patch exclusions in usable frames: {_e(reasons)}. "
            f"Cell-test baseline: {len(qsum['clean_reference_frames'])} clean-card frames.</p>"
            f"<details><summary>Usable frames per patch</summary><table><thead><tr><th>patch"
            f"</th>{head}</tr></thead><tbody>{per}</tbody></table></details>")


def report(qc_dir: Path, distance_dir: Path, dataset_config: Path, card_path: Path,
           workers: int | None = None) -> dict[str, Any]:
    from ..config import load_yaml

    verify_fresh(qc_dir)
    verify_fresh(distance_dir)
    root = qc_dir.parent
    ingest_record = verify_fresh(root / "ingest")
    dataset_dir = Path(ingest_record["params"]["dataset_dir"])
    manifest = list(csv.DictReader((root / "ingest" / "manifest.csv").open()))
    rows = {r["stem"]: r for r in manifest}
    ingest = json.loads((root / "ingest" / "summary.json").read_text())
    corners = json.loads((root / "locate" / "corners.json").read_text())
    dist = json.loads((distance_dir / "distances.json").read_text())
    patches = json.loads((root / "patches" / "patches.json").read_text())
    qc = json.loads((qc_dir / "qc.json").read_text())
    qsum = json.loads((qc_dir / "summary.json").read_text())
    card = load_card(card_path)
    cfg = load_yaml(dataset_config)
    sites = {k: k.replace("_", " ").title() for k in (cfg.get("sites") or {})}

    scores = score_before(patches, qc, card)
    jobs = []
    for stem, rec in corners.items():
        jpeg = dataset_dir / rows[stem]["jpeg"] if rows[stem]["jpeg"] else None
        excluded = [pid for pid, p in qc.get(stem, {}).get("patches", {}).items()
                    if not p["usable"]]
        jobs.append((jpeg, rec.get("quad_jpeg") if rec.get("located") else None, card,
                     excluded))
    thumbs = dict(zip(corners, run_parallel(thumbnail, jobs, workers or os.cpu_count() or 1)))

    out_dir = root / "report"
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "scores_before.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SCORE_COLUMNS)
        w.writeheader()
        for stem, s in scores.items():
            r = rows[stem]
            w.writerow({"stem": stem, "dive_id": r["dive_id"], "sweep_id": r["sweep_id"],
                        "category": r["category"], "card_condition": s["card_condition"],
                        "depth_m": r["depth_m"], "z_m": dist.get(stem, {}).get("z_m"),
                        **{k: s[k] for k in SCORE_COLUMNS[7:]}})

    located = sum(r["located"] for r in corners.values())
    with_z = sum(d["z_m"] is not None for d in dist.values())
    by = qsum["by_card_condition"]
    tiles = [(ingest["shots"], f"shots ({ingest['with_raw']} RAW)"), (ingest["dives"], "dives"),
             (ingest["sweeps"], "distance sweeps"), (f"{located}/{len(corners)}",
                                                      "card frames located"),
             (with_z, "frames with distance z (provisional)")]
    tiles += [(f"{by[c]['usable_frames']} · {by[c]['usable_patches']}",
               f"{CONDITION[c][1].lower()}: usable frames · patches")
              for c in ("clean", "damaged") if c in by]
    stat_html = "".join(f'<div class="stat"><b>{_e(v)}</b><span>{_e(k)}</span></div>'
                        for v, k in tiles)
    per_frame = "".join(
        f"<tr><td>{_e(s)}</td><td>{_e(rows[s]['dive_id'])}</td><td>{_e(rows[s]['sweep_id'])}"
        f"</td><td>{_e(v['card_condition'])}</td><td>{_e(rows[s]['depth_m'])}</td>"
        f"<td>{_e(dist.get(s, {}).get('z_m'))}</td><td>{v['usable_patches']}</td>"
        f"<td>{_e(v['psi_median'])}</td><td>{_e(v['de2000_median'])}</td></tr>"
        for s, v in sorted(scores.items()))
    page = PAGE.format(
        dataset_id=_e(ingest["dataset_id"]), stats=stat_html,
        by_condition=_score_table(scores, rows, lambda s, v: CONDITION[v["card_condition"]][1]),
        by_dive=_score_table(scores, rows, lambda s, v: f"dive {rows[s]['dive_id']} · "
                             f"{v['card_condition']}"),
        scatter=_scatter(scores, rows, dist), per_frame=per_frame,
        qc=_qc_tables(qsum, card), timeline=_timeline(manifest, corners, dist, sites),
        sheets=_contact_sheets(rows, corners, dist, qc, scores, thumbs),
        created=datetime.now().strftime("%Y-%m-%d %H:%M"))
    (out_dir / "index.html").write_text(page)

    summary = {"frames_scored": len(scores), "by_condition": {}}
    for c in ("clean", "damaged"):
        g = [s for s in scores.values() if s["card_condition"] == c]
        psi = [s["psi_median"] for s in g if s["psi_median"] is not None]
        de = [s["de2000_median"] for s in g if s["de2000_median"] is not None]
        summary["by_condition"][c] = {
            "frames": len(g), "usable_patches": sum(s["usable_patches"] for s in g),
            "psi_median_deg": round(float(np.median(psi)), 2) if psi else None,
            "de2000_median": round(float(np.median(de)), 2) if de else None}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_stage(out_dir, "report", configs=[dataset_config, card_path],
                upstream=[qc_dir, distance_dir], params={"before": "as-shot camera JPEG"})
    summary["out_dir"] = str(out_dir)
    summary["html_mb"] = round((out_dir / "index.html").stat().st_size / 1e6, 2)
    return summary


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Phase 8 Report</title>
<style>
:root{{color-scheme:light;--surface-1:#fcfcfb;--surface-2:#f1f0ec;--text-primary:#0b0b0b;
--text-secondary:#52514e;--muted:#898781;--gridline:#e1e0d9;--axis:#c3c2b7;--s1:#2a78d6;
--s2:#eb6834;--s3:#1baf7a;--other:#9a9994;--critical:#d03b3b}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{color-scheme:dark;
--surface-1:#1a1a19;--surface-2:#252523;--text-primary:#fff;--text-secondary:#c3c2b7;
--gridline:#2c2c2a;--axis:#383835;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--other:#6f6e69}}}}
:root[data-theme="dark"]{{color-scheme:dark;--surface-1:#1a1a19;--surface-2:#252523;
--text-primary:#fff;--text-secondary:#c3c2b7;--gridline:#2c2c2a;--axis:#383835;--s1:#3987e5;
--s2:#d95926;--s3:#199e70;--other:#6f6e69}}
body{{margin:0;background:var(--surface-1);color:var(--text-primary);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1240px;margin:0 auto;padding:24px 16px 64px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:32px 0 8px}}
h3{{font-size:13px;color:var(--text-secondary);margin:18px 0 6px;font-weight:600}}
.note{{color:var(--text-secondary);margin:0 0 12px;max-width:85ch}} .muted{{color:var(--muted)}}
.stats{{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}}
.stat{{background:var(--surface-2);border-radius:8px;padding:10px 14px;min-width:120px}}
.stat b{{display:block;font-size:22px}} .stat span{{color:var(--text-secondary);font-size:12px}}
table{{border-collapse:collapse;font-size:13px;margin:8px 0;font-variant-numeric:tabular-nums}}
th,td{{padding:4px 10px;text-align:left;border-bottom:1px solid var(--gridline)}}
thead th{{color:var(--text-secondary);font-weight:600}} .cols{{display:flex;flex-wrap:wrap;gap:24px;align-items:flex-start}}
.scroll{{overflow-x:auto;max-height:420px}}
.legend{{display:flex;flex-wrap:wrap;gap:16px;margin:8px 0;color:var(--text-secondary);font-size:12px}}
.key{{display:inline-flex;align-items:center;gap:6px}}
.sw{{width:10px;height:10px;border-radius:50%;display:inline-block}}
.sw.s1{{background:var(--s1)}} .sw.s2{{background:var(--s2)}} .sw.s3{{background:var(--s3)}}
.sw.other{{border:2px solid var(--other);width:6px;height:6px}}
.panels{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:16px}}
.panel{{margin:0;background:var(--surface-2);border-radius:8px;padding:10px;max-width:760px}}
figcaption{{font-size:12px;color:var(--text-secondary);margin-bottom:4px}}
svg{{width:100%;height:auto;display:block}} .gridline{{stroke:var(--gridline);stroke-width:1}}
.axis{{stroke:var(--axis);stroke-width:1}} .tick{{fill:var(--muted);font-size:10px}}
.sweep{{fill:none;stroke:var(--s1);stroke-width:2;opacity:.45}}
.pt{{stroke:var(--surface-2);stroke-width:2}} .pt.s1{{fill:var(--s1)}} .pt.s2{{fill:var(--s2)}}
.pt.s3{{fill:var(--s3)}} .pt.other{{fill:var(--surface-2);stroke:var(--other)}}
.pt:hover,.pt:focus{{r:6;outline:none}}
#tip{{position:fixed;pointer-events:none;background:var(--text-primary);color:var(--surface-1);
font-size:12px;padding:6px 8px;border-radius:6px;opacity:0;max-width:320px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px}}
.tile{{background:var(--surface-2);border-radius:6px;overflow:hidden}}
.tile img{{width:100%;display:block;aspect-ratio:3/1;object-fit:cover}} .noimg{{padding:24px;color:var(--muted)}}
.tile .cap{{font-size:11px;padding:4px 6px;color:var(--text-secondary)}}
.tile.miss{{outline:2px dashed var(--critical);outline-offset:-2px}}
.tile.excl{{outline:2px solid var(--other);outline-offset:-2px;opacity:.75}}
</style></head><body><main>
<h1>Phase 8 · TG-7 dataset report (S1)</h1>
<p class="note">Dataset <code>{dataset_id}</code> · generated {created} by
<code>python -m host_tools.color report</code>. All detection, distances, patch sampling and QC
use the RAW; the camera JPEG appears only as the as-shot "before" being scored.</p>
<div class="stats">{stats}</div>
<h2>"Before" scores — as-shot camera JPEG</h2>
<p class="note">ψ: angle of each usable grey patch from neutral in linear sRGB (no grey was used
to neutralize, so every usable grey counts). ΔE00: the 12 colour patches after one exposure
scale on grey 128 (its right half on the damaged card). Per-frame medians, then median and
IQR across frames. Only patches and frames kept by qc.</p>
{by_condition}
<details><summary>By dive</summary>{by_dive}</details>
<h3>As-shot ψ vs depth</h3>{scatter}
<details><summary>Per-frame table</summary><div class="scroll"><table><thead><tr><th>frame</th>
<th>dive</th><th>sweep</th><th>card</th><th>depth m</th><th>z m</th><th>patches</th><th>ψ °</th>
<th>ΔE00</th></tr></thead><tbody>{per_frame}</tbody></table></div></details>
<h2>QC — excluded, never repaired</h2>{qc}
<h2>Dataset timeline</h2>
<p class="note">Depth vs minutes into each dive; lines join the frames of each distance sweep.
Hover or focus a point for details.</p>{timeline}
<h2>Contact sheets</h2>
<p class="note">Card rectified from the camera JPEG (the as-shot look). A red cross marks a patch
qc excluded. Dashed red: card not located yet (manual clicks pending). Grey outline: frame
excluded by qc.</p>{sheets}
</main><div id="tip" role="tooltip"></div>
<script>
const tip=document.getElementById('tip');
function show(el,x,y){{tip.replaceChildren();el.dataset.tip.split('|').forEach((t,i)=>{{
const d=document.createElement(i?'div':'strong');d.textContent=t;tip.appendChild(d)}});
tip.style.opacity=1;tip.style.left=Math.min(x+12,innerWidth-330)+'px';tip.style.top=(y+12)+'px'}}
document.querySelectorAll('.pt').forEach(c=>{{
c.addEventListener('pointermove',e=>show(c,e.clientX,e.clientY));
c.addEventListener('focus',()=>{{const r=c.getBoundingClientRect();show(c,r.right,r.bottom)}});
['pointerleave','blur'].forEach(ev=>c.addEventListener(ev,()=>tip.style.opacity=0))}});
</script></body></html>"""
