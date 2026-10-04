"""Heat maps + slider spec for Nick's 180 / 250 / 500-message grid (before-after-report skill).

    python -m compression_study.presets.roi_grid_sheet --run <runs/sweep_…>
    python ~/.claude/skills/before-after-report/scripts/build_report.py \
        <run>/grid_sheet/spec.json <run>/grid_sheet/index.html --split

Numbers are medians over the 3 frames; Zero cost is measured on frame 0 with the frame-0
distance. Per cell the shown effort is, among those the Zero can encode: the better verdict, then
a Zero cost inside 30 s / 150 MiB, then the higher card-area SSIM. Zero cost WARN: > 30 s or > 150 MiB;
"can't" = the production 250 MiB guard stops cjxl. Verdicts vs today's pjpg (medians):
colour PASS if card-fit ΔE ≤ pjpg (WARN ≤ +5 %), detail PASS if card SSIM ≥ pjpg and tag
acutance is no further from 1 than pjpg's + 0.02, WARN if only SSIM holds, else FAIL.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics as st
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BUDGETS = (180, 250, 500)
ROIS = ["800x450", "1200x676", "1600x900", "2000x1124", "2304x1296", "3072x1728", "4608x2592"]
COL = {"PASS": (46, 139, 87), "WARN": (214, 158, 46), "FAIL": (196, 69, 54),
       "nofit": (120, 126, 132), "cant": (60, 60, 66)}
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_B = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def med(vals):
    v = [float(x) for x in vals if x not in ("", None)]
    return st.median(v) if v else None


def heat(path: Path, title: str, cells: dict, note: str):
    """cells[(roi, budget)] = (colour key, line1, line2)."""
    cw, ch, lw, th = 250, 78, 170, 90
    W, H = lw + cw * len(BUDGETS) + 20, th + ch * len(ROIS) + 60
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    fb, f, fs = ImageFont.truetype(FONT_B, 22), ImageFont.truetype(FONT, 19), ImageFont.truetype(FONT, 16)
    d.text((10, 12), title, fill=(20, 26, 32), font=fb)
    for j, b in enumerate(BUDGETS):
        d.text((lw + j * cw + 12, th - 34), f"{b} msgs  (≈ {b * 288 / 1000:.0f} kB)",
               fill=(20, 26, 32), font=fb)
    for i, r in enumerate(ROIS):
        y = th + i * ch
        d.text((10, y + 26), r.replace("x", "×"), fill=(20, 26, 32), font=fb)
        for j, b in enumerate(BUDGETS):
            x = lw + j * cw
            key, l1, l2 = cells[(r, b)]
            d.rectangle([x + 3, y + 3, x + cw - 3, y + ch - 3], fill=COL[key])
            d.text((x + 12, y + 10), l1, fill=(255, 255, 255), font=f)
            d.text((x + 12, y + 42), l2, fill=(255, 255, 255), font=fs)
    d.text((10, H - 40), note, fill=(70, 78, 86), font=fs)
    im.save(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    run = Path(ap.parse_args(argv).run)
    out = run / "grid_sheet"
    img = out / "img"
    img.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader((run / "grid.csv").open()))
    pj = list(csv.DictReader((run / "grid_pjpg.csv").open()))
    pz = {}
    for line in (run / "pi_grid.txt").read_text().splitlines():
        if "{" in line:
            j = json.loads(line[line.index("{"):])
            roi_, e, b = j["name"].split("_")
            pz[(roi_, int(b), int(e[1:]))] = j
    p_ssim, p_de, p_acu = med(r["ssim_card"] for r in pj), med(r["de_truth"] for r in pj), \
        med(r["acu"] for r in pj)
    raw_de = [float(r["raw_de_truth"]) for r in pj]

    def zero(roi_, b, e):
        j = pz.get((roi_, b, e))
        if not j:
            return {"state": "n/a", "text": "not run"}
        if j["rc"] != 0:
            return {"state": "cant", "text": "guard stops cjxl (rfb=mem)", "s": j["wall_s"],
                    "mib": j["peak_rss_mib"]}
        warn = j["wall_s"] > 30 or j["peak_rss_mib"] > 150
        return {"state": "WARN" if warn else "ok", "s": j["wall_s"], "mib": j["peak_rss_mib"],
                "text": f"{j['wall_s']:.1f} s · {j['peak_rss_mib']:.0f} MiB"}

    cells, table, summary = {}, [], {}
    for r_ in ROIS:
        for b in BUDGETS:
            opts = []
            for e in (5, 7):
                rr = [x for x in rows if x["roi"] == r_ and int(x["budget"]) == b
                      and int(x["effort"]) == e]
                fits = all(x["fits"] == "True" for x in rr)
                s = {"roi": r_, "budget": b, "effort": e, "fits": fits, "zero": zero(r_, b, e)}
                if fits:
                    for k in ("distance", "bytes", "messages", "fill", "ssim_card", "ssim_tex",
                              "ssim_roi", "acu", "de_raw", "de_truth"):
                        s[k] = med(x[k] for x in rr)
                    s["colour"] = ("PASS" if s["de_truth"] <= p_de else
                                   "WARN" if s["de_truth"] <= p_de * 1.05 else "FAIL")
                    acu_ok = abs(s["acu"] - 1) <= abs(p_acu - 1) + 0.02
                    s["detail"] = ("PASS" if s["ssim_card"] >= p_ssim and acu_ok else
                                   "WARN" if s["ssim_card"] >= p_ssim else "FAIL")
                    order = {"PASS": 0, "WARN": 1, "FAIL": 2}
                    s["verdict"] = max(s["colour"], s["detail"], key=order.get)
                else:
                    s["bytes"] = med(x["bytes"] for x in rr)
                    s["messages"] = med(x["messages"] for x in rr)
                opts.append(s)
            feas = [o for o in opts if o["fits"] and o["zero"]["state"] in ("ok", "WARN")]
            fit = [o for o in opts if o["fits"]]
            if feas:
                rank = {"PASS": 0, "WARN": 1, "FAIL": 2}
                # better verdict first, then a Zero cost inside budget, then card SSIM
                best = min(feas, key=lambda o: (rank[o["verdict"]], o["zero"]["state"] == "WARN",
                                                -o["ssim_card"], o["effort"]))
            elif fit:
                best = dict(max(fit, key=lambda o: o["ssim_card"]), cant=True)
            else:
                best = dict(opts[0], nofit=True)
            summary[(r_, b)] = best
            for o in opts:
                z = o["zero"]
                table.append([r_.replace("x", "×"), str(b), f"e{o['effort']}",
                              "✓" if (not best.get("nofit") and o["effort"] == best["effort"]) else "",
                              f"{o['distance']:.2f}" if o["fits"] else "no fit at d 15",
                              f"{o['bytes']:,.0f}", f"{o['messages']:.0f}",
                              f"{o['ssim_card']:.3f}" if o["fits"] else "—",
                              f"{o['ssim_tex']:.3f}" if o["fits"] else "—",
                              f"{o['acu']:.2f}" if o["fits"] else "—",
                              f"{o['de_raw']:.2f}" if o["fits"] else "—",
                              f"{o['de_truth']:.2f}" if o["fits"] else "—",
                              z["text"] + (" (WARN)" if z["state"] == "WARN" else ""),
                              o.get("detail", "—"), o.get("colour", "—")])
            if best.get("nofit"):
                cells[(r_, b)] = ("nofit", "does not fit", f"{best['messages']:.0f} msgs at d 15")
            elif best.get("cant"):
                cells[(r_, b)] = ("cant", "Zero can't encode", f"(card SSIM {best['ssim_card']:.3f})")
            else:
                cells[(r_, b)] = (best["detail"], f"{best['detail']} · SSIM {best['ssim_card']:.3f}",
                                  f"e{best['effort']} · d {best['distance']:.2f} · tex {best['ssim_tex']:.3f}")
    heat(img / "heat_detail.png", "Detail vs today's pjpg (card-area SSIM; pjpg = "
         f"{p_ssim:.3f})", cells,
         "Green PASS / amber WARN / red FAIL; grey = does not fit at d 15; black = the Zero's "
         "250 MiB guard stops cjxl.")
    zc = {}
    for k, s in summary.items():
        if s.get("nofit"):
            zc[k] = ("nofit", "does not fit", "")
        else:
            z = s["zero"]
            key = {"ok": "PASS", "WARN": "WARN", "cant": "cant"}.get(z["state"], "nofit")
            zc[k] = (key, z["text"] if z["state"] != "cant" else "can't (rfb=mem)",
                     f"e{s['effort']}, frame 0")
    heat(img / "heat_zero.png", "Pi Zero 2 W encode cost (production CLI, measured)", zc,
         "Green ≤ 30 s and ≤ 150 MiB; amber over either; black = guard stops cjxl.")

    def ok_cell(s):
        return (not s.get("nofit") and not s.get("cant") and s.get("verdict") == "PASS")

    answers, picks = {}, {}
    for b in BUDGETS:
        good = [r_ for r_ in ROIS if ok_cell(summary[(r_, b)])]
        feas = [r_ for r_ in ROIS if not summary[(r_, b)].get("nofit")
                and not summary[(r_, b)].get("cant")]
        a = good[-1] if good else None
        if a:
            s = summary[(a, b)]
            answers[b] = (f"{b} msgs: {a.replace('x', '×')} (e{s['effort']}, d {s['distance']:.2f}, card "
                          f"SSIM {s['ssim_card']:.3f} vs {p_ssim:.3f}) — Zero {s['zero']['text']}"
                          + (" (WARN)" if s["zero"]["state"] == "WARN" else ""))
        else:
            answers[b] = f"{b} msgs: no ROI passes"
        picks[b] = list(dict.fromkeys([x for x in ("1600x900", "2304x1296") if not
                                       summary[(x, b)].get("nofit")] + ([a] if a else [])
                                      + ([feas[-1]] if feas else [])))
        picks[b] = [x for x in ROIS if x in picks[b]]

    sections = [
        {"title": "Detail heat map", "figure": "img/heat_detail.png",
         "note": "Best setting per cell, among e5 / e7 runs the Zero can encode: better verdict "
                 "first, then a Zero cost within 30 s / 150 MiB, then higher card SSIM. Medians of 3 frames. Texture SSIM (tex) is on each ROI's most detailed "
                 "480×270 window away from the card."},
        {"title": "Pi Zero cost heat map", "figure": "img/heat_zero.png",
         "note": "Production rc_raw_jxl CLI on nereus002 (bm #120 bd6f38c), frame 0, an extra "
                 "700 MiB address-space cap and nice 10; recorder_web / workbench / field_power_log "
                 "kept running."},
        {"title": "Every cell, both efforts (medians of 3 frames)",
         "note": "✓ = the setting shown in the heat map. ΔE vs RAW = the 10 usable card patches "
                 "with no fit (the clean compression number). ΔE card = vs the card truth after a "
                 f"3×3 fit on the same 10 patches (flattering; the RAW itself scores "
                 f"{min(raw_de):.2f}–{max(raw_de):.2f}). Today's pjpg: card SSIM {p_ssim:.3f}, "
                 f"ΔE card {p_de:.2f}, acutance {p_acu:.2f}.",
         "table": {"columns": ["ROI", "msgs", "effort", "shown", "distance", "bytes", "msgs used",
                               "card SSIM", "texture SSIM", "acutance", "ΔE vs RAW", "ΔE card",
                               "Zero", "detail", "colour"], "rows": table}}]
    full_dir = run / "grid_full"
    if full_dir.exists():  # roi_grid_full.py: whole decoded crops + the framed full frame
        made = json.loads((full_dir / "made.json").read_text())
        shutil.copy2(full_dir / "frame_rois.jpg", img / "frame_rois.jpg")
        sections.insert(2, {
            "title": "The full frame with the seven ROIs",
            "figure": "img/frame_rois.jpg",
            "note": "Frame 0, 4608×2592 RAW rendered like the sliders, DOWNSCALED 2× to 2304×1296 for "
                    "display (a static picture; the crops below open at full resolution)."})
        for r_ in ("1600x900", "2304x1296", "3072x1728"):
            afters = []
            for b in BUDGETS:
                m = made.get(f"{r_}@{b}")
                if not m:
                    continue
                src = f"{r_}_{b}_e{m['effort']}.webp"
                shutil.copy2(full_dir / src, img / src)
                sb = summary[(r_, b)]
                afters.append({"label": f"{b} msgs · e{m['effort']} d {m['distance']:.2f} · "
                                        f"{m['bytes'] / 1000:.1f} kB · {sb.get('verdict', 'n/a')}",
                               "image": f"img/{src}"})
            shutil.copy2(full_dir / f"{r_}_ref.webp", img / f"{r_}_ref.webp")
            missing = [str(b) for b in BUDGETS if f"{r_}@{b}" not in made]
            sections.append({
                "title": f"Whole crop · {r_.replace('x', '×')} — RAW vs nrjxl at each budget",
                "note": "The entire decoded ROI, frame 0. Pick the budget with the chips. The inline "
                        "view is DOWNSCALED to fit the page; click (or Enter) for full screen at the "
                        "crop's full resolution, then 1:1 and beyond."
                        + (f" Not shown: {', '.join(missing)} msgs (does not fit)." if missing else ""),
                "before": f"img/{r_}_ref.webp", "before_label": "RAW reference",
                "afters": afters})
    files_src = run / "grid_slides"
    for b in BUDGETS:
        for r_ in picks[b]:
            s = summary[(r_, b)]
            if s.get("nofit"):
                continue
            e = s["effort"]
            nums = (f"e{e} · d {s['distance']:.2f} · {s['bytes']:,.0f} B · {s['messages']:.0f} msgs · "
                    f"card SSIM {s['ssim_card']:.3f} (pjpg {p_ssim:.3f}) · texture SSIM "
                    f"{s['ssim_tex']:.3f} · acutance {s['acu']:.2f} (pjpg {p_acu:.2f}) · ΔE vs RAW "
                    f"{s['de_raw']:.2f} · ΔE card {s['de_truth']:.2f} (pjpg {p_de:.2f}) · Zero "
                    f"{s['zero']['text'] if not s.get('cant') else 'cannot encode'} · detail "
                    f"{s.get('detail')} · colour {s.get('colour')}")
            for tag, label in (("card", "card + checker"), ("tex", "texture")):
                for src, dst in ((f"{r_}_ref_{tag}.webp", f"{r_}_ref_{tag}.webp"),
                                 (f"{r_}_{b}_e{e}_{tag}.webp", f"{r_}_{b}_e{e}_{tag}.webp")):
                    shutil.copy2(files_src / src, img / dst)
                sections.append({
                    "title": f"{b} msgs · {r_.replace('x', '×')} · {label} — {s.get('verdict', 'n/a')}",
                    "note": nums + ". Native pixels; full screen zooms to 1:1 and beyond.",
                    "before": f"img/{r_}_ref_{tag}.webp", "before_label": "RAW reference",
                    "pixelated": True,
                    "afters": [{"label": f"nrjxl {s['messages']:.0f} msgs (e{e})",
                                "image": f"img/{r_}_{b}_e{e}_{tag}.webp"}]})
    shown = "; ".join(f"{b}: " + ", ".join(x.replace("x", "×") for x in picks[b]) for b in BUDGETS)
    spec = {
        "title": "ROI × budget grid",
        "eyebrow": "Nereus Vision · IMX708 on nereus002 · 2026-10-03 · card ≈ 1.5 m · 3 locked frames",
        "lede": "Largest ROI at or above today's quality that the Pi Zero can encode — "
                + " · ".join(answers[b] for b in BUDGETS),
        "facts": [{"value": answers[b].split(":")[1].split("(")[0].strip(),
                   "label": f"largest ROI ≥ today's quality at {b} msgs"} for b in BUDGETS]
                 + [{"value": f"SSIM {p_ssim:.3f}", "label": "today's pjpg on the card area (the bar)"}],
        "sections": sections,
        "notes": [
            "Whole-crop views: 1600×900, 2304×1296 and 3072×1728 at every budget that fits, downscaled "
            "inline and full resolution in full screen. Zoomed views: the sliders show 1600×900, 2304×1296 and the largest "
            f"Zero-feasible / passing ROI per budget ({shown}). Every cell's numbers are in the table.",
            "Slider: left = RAW reference, right = nrjxl decoded back to raw; both rendered by one "
            "function (bilinear demosaic, the camera's locked WB gains, clipped at sensor white, the "
            "RAW's own card matrix, sRGB). Nothing is upsampled on this sheet.",
            "Distances come from the Step C byte-target search (≤ 3 encodes, no quality floor here), "
            "with an exact bisection when the search ends without a fit below d 15.",
            f"ΔE reliability: 10 of 13 card colour patches usable (glare / noise); RAW ΔE varies "
            f"{min(raw_de):.2f}–{max(raw_de):.2f} across identical frames, so ΔE differences under "
            "~0.3 are noise. Tag acutance uses 2 tags of ~32 px.",
            "Zero cost is measured on frame 0 at frame 0's distance (encode time barely depends on "
            "distance). Full frame: the production guard stops cjxl (rfb=mem).",
            "The 195-message point from Step A sits in levers.csv (not repeated here)."],
        "footer": "Data: compression_study/presets/runs/sweep_20261003T203525Z (grid.csv, grid_pjpg.csv, "
                  "pi_grid.txt). Encoder: bm_cam_legacy #120 (bd6f38c); identical bytes on Mac and Pi.",
    }
    (out / "spec.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False))
    (out / "summary.json").write_text(json.dumps(
        {f"{k[0]}@{k[1]}": {kk: vv for kk, vv in v.items()} for k, v in summary.items()},
        indent=1, default=str))
    for b in BUDGETS:
        print(answers[b])
    print("sliders:", shown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
