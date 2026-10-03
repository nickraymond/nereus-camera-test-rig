"""Cut sheets for the Sprint28 preset study: figures + spec for the before-after-report skill.

    python -m compression_study.presets.build_sheets runs/s28_presets_<date> [--pi pi_timing.json]

Per preset: a full-frame thumbnail with the crop (solid) and the ΔE ROI (dashed), a plot of
colour error vs bytes (after the card fit, vs the card's measured truth; RAW floor and today's
pjpg as lines) and of detail (SSIM vs RAW), sliders of the ROI and of the card patches (RAW vs
nrjxl vs today's pjpg), and the recommendation table. Writes <run>/sheet/spec.json; build with
~/.claude/skills/before-after-report/scripts/build_report.py … --split.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

PRESETS = ("WIDE", "MEDIUM", "HIGH")
COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]  # categorical slots 1-3 (validated all-pairs)


def load(run: Path):
    rows = list(csv.DictReader((run / "metrics.csv").open()))
    for r in rows:
        for k in ("bytes", "messages", "ssim", "patch_de", "de_truth", "distance",
                  "acutance_rel", "tags_found", "tags_ref", "patches"):
            try:
                r[k] = float(r[k]) if r.get(k) not in (None, "", "None") else None
            except ValueError:
                pass
    manifest = json.loads((run / "run_manifest.json").read_text())
    return rows, manifest


def thumb(run: Path, preset: str, cands, comp, out: Path):
    ref = run / "renders" / "W-full_ref_region.webp"
    im = Image.open(ref).convert("RGB")
    s = 1152 / im.width
    im = im.resize((1152, round(im.height * s)), Image.Resampling.LANCZOS)
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 15)
    except OSError:
        font = ImageFont.load_default()
    for i, c in enumerate(cands):
        x, y, w, h = c["region"]
        d.rectangle([x * s, y * s, (x + w) * s, (y + h) * s], outline=COLORS[i % 3], width=3)
        d.text((x * s + 5, y * s + 4 + 17 * i), f"{c['id']} [{x},{y},{w},{h}]", fill=COLORS[i % 3],
               font=font)
    x, y, w, h = comp
    for xx in range(int(x * s), int((x + w) * s), 12):
        d.line([xx, y * s, xx + 6, y * s], fill="#ffffff", width=2)
        d.line([xx, (y + h) * s, xx + 6, (y + h) * s], fill="#ffffff", width=2)
    for yy in range(int(y * s), int((y + h) * s), 12):
        d.line([x * s, yy, x * s, yy + 6], fill="#ffffff", width=2)
        d.line([(x + w) * s, yy, (x + w) * s, yy + 6], fill="#ffffff", width=2)
    d.text((x * s + 5, (y + h) * s - 20), f"ΔE ROI [{x},{y},{w},{h}]", fill="#ffffff", font=font)
    im.save(out)


def plot(rows, preset: str, cand_ids, out: Path):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), dpi=110)
    for lamp, ls in (("cool", "-"), ("warm", "--")):
        pr = [r for r in rows if r["preset"] == preset and r["lamp"] == lamp]
        if not pr:
            continue
        pj = next(r for r in pr if r["candidate"] == "today_pjpg")
        raw = next(r for r in pr if r["candidate"] == "raw_reference")
        for ax, key in ((axes[0], "de_truth"), (axes[1], "ssim")):
            ax.axhline(pj[key], color="#6b7682", ls=ls, lw=1.2)
            ax.plot([pj["bytes"] / 1000], [pj[key]], marker="s", color="#6b7682", ms=6)
            if key == "de_truth":
                ax.axhline(raw[key], color="#121820", ls=":", lw=1)
        for i, cid in enumerate(cand_ids):
            pts = sorted({(r["bytes"] / 1000): r for r in pr if r["candidate"] == cid
                          and r["target"] != "today_size"}.items())
            if not pts:
                continue
            xs = [p[0] for p in pts]
            axes[0].plot(xs, [p[1]["de_truth"] for p in pts], ls=ls, marker="o", ms=4,
                         color=COLORS[i % 3], label=f"{cid} ({lamp})")
            axes[1].plot(xs, [p[1]["ssim"] for p in pts], ls=ls, marker="o", ms=4,
                         color=COLORS[i % 3])
    axes[0].set_title("Colour error after the card fit (ΔE00 vs card truth) — lower is better",
                      fontsize=9.5)
    axes[1].set_title("Detail: SSIM vs the RAW reference — higher is better", fontsize=9.5)
    for ax in axes:
        ax.set_xscale("log")
        ax.set_xlabel("transmitted bytes (kB, log)")
        ax.axvline(50, color="#c9ced3", lw=0.8)
        ax.grid(True, color="#e6eaee", lw=0.6)
        ax.set_xticks([17, 25, 50, 75, 112, 200])
        ax.set_xticklabels(["17", "25", "50", "75", "112", "200"])
    axes[0].legend(fontsize=7.5, loc="upper right", frameon=False)
    fig.text(0.01, 0.01, "grey line + square: today's pjpg (solid cool, dashed warm); dotted: RAW "
             "floor; vertical: 50 kB budget", fontsize=7.5, color="#46525e")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out)
    plt.close(fig)


def fmt(v, nd=2):
    return "—" if v is None else f"{v:.{nd}f}"


def main(argv) -> int:
    run = Path(argv[1])
    pi = json.loads(Path(argv[argv.index("--pi") + 1]).read_text()) if "--pi" in argv else {}
    rows, man = load(run)
    sheet = run / "sheet"
    sheet.mkdir(exist_ok=True)
    patch_sets = json.loads((run / "patch_sets.json").read_text())
    cands = {c["id"]: {**c, "region": c["region_native"]} for c in man["candidates"]}
    R = "../renders"
    sections, table = [], []
    for preset in PRESETS:
        cids = [c for c, v in cands.items() if v["preset"] == preset]
        pr = [r for r in rows if r["preset"] == preset and r["lamp"] == "cool"]
        comp = json.loads(pr[0]["compare_box"].replace("'", '"')) if isinstance(
            pr[0]["compare_box"], str) else pr[0]["compare_box"]
        pj = next(r for r in pr if r["candidate"] == "today_pjpg")
        thumb(run, preset, [cands[c] for c in cids], comp, sheet / f"{preset}_thumb.png")
        plot(rows, preset, cids, sheet / f"{preset}_plot.png")
        ids = patch_sets[preset]
        sections.append({"title": f"{preset} · where the crops are",
                         "note": f"Full frame (RAW reference, scaled). Solid: each option's crop; "
                                 f"dashed: the region every variant is measured on. ΔE patch set "
                                 f"({len(ids)}): {', '.join(ids)}.",
                         "figure": f"{preset}_thumb.png"})
        sections.append({"title": f"{preset} · colour error and detail vs bytes",
                         "note": "Cool (solid) and warm (dashed) scenes. Colour error = ΔE00 vs "
                                 "the card's measured values after a colour matrix fitted to the "
                                 "card in each image (your cloud step); the RAW floor is what "
                                 "the uncompressed raw reaches.",
                         "figure": f"{preset}_plot.png"})
        # sliders: the ROI and the card patches, cool scene
        afters_roi, afters_pat = [], []
        pj_lab = (f"today's pjpg q{int(float(pj['quality']))} · {pj['bytes'] / 1000:.0f} kB · "
                  f"{int(pj['messages'])} msgs (upsampled 1.6×)")
        afters_roi.append({"label": pj_lab, "image": f"{R}/{preset}_pjpg_cmp.webp"})
        afters_pat.append({"label": pj_lab, "image": f"{R}/{preset}_pjpg_patches.webp"})
        for cid in cids:
            for t in ("17kB", "50kB", "75kB", "112kB"):
                r = next((x for x in pr if x["candidate"] == cid and x["target"] == t), None)
                img = run / "renders" / f"{preset}_{cid}_{t}_cmp.webp"
                if r is None or not img.exists():
                    continue
                up = " (binned: upsampled 2×)" if "bin" in cid else ""
                lab = (f"{cid} · {r['bytes'] / 1000:.0f} kB · {int(r['messages'])} msgs · "
                       f"d {fmt(r['distance'])}{up}")
                afters_roi.append({"label": lab, "image": f"{R}/{preset}_{cid}_{t}_cmp.webp"})
                afters_pat.append({"label": lab, "image": f"{R}/{preset}_{cid}_{t}_patches.webp"})
        x, y, w, h = comp
        sections.append({"title": f"{preset} · the measured region at sensor resolution",
                         "note": f"[{x},{y},{w},{h}] native px, cool scene. Left: RAW reference. "
                                 "Chips: today's pjpg and each nrjxl option. Click for full "
                                 "screen and zoom.",
                         "before": f"{R}/{preset}_ref_cmp.webp", "before_label": "RAW reference",
                         "afters": afters_roi})
        sections.append({"title": f"{preset} · card patches, zoomed",
                         "note": "Native px enlarged 2×, no smoothing. Same scale for every "
                                 "variant; today's pjpg is upsampled 1.6× from 1000×562.",
                         "before": f"{R}/{preset}_ref_patches.webp", "before_label": "RAW reference",
                         "pixelated": True, "afters": afters_pat})
        # recommendation rows (cool + warm worst)
        for cid in cids:
            for t in ("17kB", "50kB", "75kB", "112kB"):
                rs = [r for r in rows if r["candidate"] == cid and r["target"] == t]
                if not rs:
                    continue
                worst = sorted([r["verdict"] for r in rs], key=["PASS", "WARN", "FAIL"].index)[-1]
                b = max(r["bytes"] for r in rs)
                p = pi.get(f"{cid}@{t}", {})
                table.append([preset, cid, cands[cid]["label"], json.dumps(cands[cid]["region"]),
                              t, f"{b / 1000:.1f}", f"{int(max(r['messages'] for r in rs))}",
                              fmt(max(r["distance"] for r in rs)),
                              fmt(max(r["de_truth"] for r in rs)),
                              fmt(min(r["ssim"] for r in rs), 3),
                              "yes" if any(r.get("container_v2") == "True" for r in rs) else "no",
                              p.get("time", "—"), p.get("rss", "—"), worst])
    pjs = [r for r in rows if r["candidate"] == "today_pjpg" and r["preset"] == "MEDIUM"]
    pj_summary = ", ".join(f"{r['lamp']} q{int(float(r['quality']))} {r['bytes'] / 1000:.1f} kB / "
                           f"{int(r['messages'])} msgs" for r in pjs)
    spec = {
        "title": "Sprint28 Still Presets",
        "eyebrow": "Nereus Vision · IMX708 raw (nereus002) · nrjxl vs today's pjpg · 2026-10-02",
        "lede": "Three customer presets for the RAW JPEG XL stills: WIDE (whole sensor), MEDIUM "
                "(today's view at native density) and HIGH (a small region at very high density). "
                "Each option is encoded with the production Sprint28 encoder at ~50 kB (today's "
                "budget) and at 75 / 112 kB, then compared with the uncompressed RAW and with "
                f"today's production pjpg of the same exposure ({pj_summary}).",
        "facts": [],
        "inline_max_width": 1400,
        "sections": [{"title": "Recommendation table (worse of cool / warm)",
                      "note": "PASS / WARN / FAIL = no worse than today's pjpg on detail (SSIM "
                              "vs RAW, tag edges) and colour (ΔE vs RAW and vs card truth) in "
                              "the same region and patch set. Pi time / memory: measured on "
                              "nereus002 where shown, else —.",
                      "table": {"columns": ["preset", "option", "what", "crop [x,y,w,h]",
                                            "target", "kB", "msgs", "distance", "ΔE vs truth",
                                            "SSIM", "container v2", "Pi s", "Pi MB", "vs today"],
                                "rows": table}}] + sections,
        "notes": [],
        "footer": f"Production code {man['production_code']['branch']} @ "
                  f"{(man['production_code'].get('commit') or '')[:10]}; cjxl {man['encoder']['cjxl']}, "
                  f"modular e{man['encoder']['effort']}, 1 thread. Frames: stop -1 r0, cool and warm "
                  "LED lamps, V1 card + 24-patch checker. RAW reference and nrjxl: bilinear demosaic, "
                  "white balance on grey 128 of the original, gamma 2.2. pjpg: as produced by "
                  "prepare_source + encode_progressive, upsampled for comparison.",
    }
    (sheet / "spec.json").write_text(json.dumps(spec, indent=1))
    print(f"wrote {sheet / 'spec.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
