"""Spec + images for the 180-message ROI sweep sheet (before-after-report skill).

    python -m compression_study.presets.roi_sweep_sheet --run <runs/sweep_…>
    python ~/.claude/skills/before-after-report/scripts/build_report.py \
        <run>/sheet/spec.json <run>/sheet/index.html --split

Numbers are medians over the 3 frames; slider images are frame 0. Verdicts vs today's pjpg:
colour PASS if card-fit ΔE ≤ pjpg (WARN ≤ +5 %); detail PASS if core SSIM ≥ pjpg and the tag
edge acutance is no further from 1 than pjpg's + 0.02, WARN if only SSIM holds, FAIL otherwise.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics as st
from pathlib import Path

from PIL import Image


def med(rows, k):
    v = [float(r[k]) for r in rows if r.get(k) not in (None, "")]
    return st.median(v) if v else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    run = Path(ap.parse_args(argv).run)
    out = run / "sheet"
    img = out / "img"
    img.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader((run / "sweep.csv").open()))
    pj = list(csv.DictReader((run / "pjpg.csv").open()))
    pi = {}
    for line in (run / "pi_timing.txt").read_text().splitlines():
        if "{" in line:
            j = json.loads(line[line.index("{"):])
            pi[j["name"]] = j
    pst = json.loads((run / "patches.json").read_text())
    p_ssim, p_de, p_acu = med(pj, "ssim"), med(pj, "de_truth"), med(pj, "acutance_rel")
    p_bytes, p_msgs = med(pj, "bytes"), med(pj, "messages")
    raw_de = [float(r["raw_de_truth"]) for r in pj]

    rois = list(dict.fromkeys(r["roi"] for r in rows))
    summary, table = {}, []
    for name in rois:
        rr = [r for r in rows if r["roi"] == name]
        fits = all(r["fits"] == "True" for r in rr)
        p = pi.get(name, {})
        if p.get("rc") == 0:
            pz = f"{p['wall_s']:.1f} s · {p['peak_rss_mib']:.0f} MiB"
        else:
            pz = "guard stops cjxl (rfb=mem); ≈ 260 MiB needed, ESTIMATE"
        if not fits:
            s = {"fits": False, "d": 15.0, "bytes": med(rr, "bytes"), "msgs": med(rr, "messages"),
                 "pi": pz}
            summary[name] = s
            table.append([name, "does not fit at d 15", f"{s['bytes']:,.0f}",
                          f"{s['msgs']:.0f}", "—", "—", "—", "—", "—", "—", "—", pz])
            continue
        s = {"fits": True, "d": med(rr, "distance"), "bytes": med(rr, "bytes"),
             "msgs": med(rr, "messages"), "ssim": med(rr, "ssim"), "ssim_roi": med(rr, "ssim_roi"),
             "de": med(rr, "de_truth"), "de_raw": med(rr, "patch_de"),
             "acu": med(rr, "acutance_rel"), "pi": pz}
        s["colour"] = ("PASS" if s["de"] <= p_de else "WARN" if s["de"] <= p_de * 1.05 else "FAIL")
        acu_ok = abs(s["acu"] - 1) <= abs(p_acu - 1) + 0.02
        s["detail"] = ("PASS" if s["ssim"] >= p_ssim and acu_ok else
                       "WARN" if s["ssim"] >= p_ssim else "FAIL")
        order = {"PASS": 0, "WARN": 1, "FAIL": 2}
        s["overall"] = max(s["colour"], s["detail"], key=order.get)
        summary[name] = s
        table.append([name, f"{s['d']:.2f}", f"{s['bytes']:,.0f}", f"{s['msgs']:.0f}",
                      f"{s['ssim']:.3f}", f"{s['ssim_roi']:.3f}", f"{s['de']:.2f}",
                      f"{s['de_raw']:.2f}", f"{s['acu']:.2f}", s["colour"], s["detail"], pz])
    table.append(["today's pjpg (1600×900 → 1000×562, q80)", "—", f"{p_bytes:,.0f}",
                  f"{p_msgs:.0f}", f"{p_ssim:.3f}", f"{med(pj, 'ssim_roi'):.3f}", f"{p_de:.2f}",
                  "n/a (camera colour)", f"{p_acu:.2f}", "bar", "bar", "camera ISP"])

    passing = [n for n in rois if summary[n].get("overall") == "PASS"]
    best = passing[-1]
    b = summary[best]

    def two(src: Path, dst: str) -> tuple[str, str]:
        im = Image.open(src)
        im.save(img / f"{dst}.png")
        im.resize((im.width * 2, im.height * 2), Image.Resampling.NEAREST).save(
            img / f"{dst}_2x.png")
        return f"img/{dst}_2x.png", f"img/{dst}.png"

    sections = [{"title": "Framing (frame 0, camera JPEG)", "figure": "img/framing.jpg",
                 "note": "4608×2592, IMX708 on nereus002, card ≈ 1.5 m. Boxes: the seven ROIs "
                         "(all centred on the card + checker; 4608×2592 is the whole frame). "
                         "Dashed underline: today's fixed pjpg crop (1504, 846, 1600, 900)."},
                {"title": "Every ROI at 180 messages (medians of 3 frames)",
                 "note": "Core SSIM = luma SSIM vs the RAW render on the 800×450 box around the card, "
                         "which every ROI and today's pjpg contain. ROI SSIM = over the whole ROI. "
                         "ΔE card = mean ΔE00 vs the V1 card truth after each image's own 3×3 card "
                         "fit (10 patches). ΔE vs RAW = same patches, no fit: compression only. "
                         "Tag acutance = edge sharpness on the 2 decodable tags relative to RAW (1 = "
                         "same). Pi Zero = production CLI on nereus002, wall time and peak RSS.",
                 "table": {"columns": ["ROI", "distance", "bytes", "msgs", "core SSIM", "ROI SSIM",
                                       "ΔE card", "ΔE vs RAW", "tag acutance", "colour",
                                       "detail", "Pi Zero encode"], "rows": table}}]
    for name in rois:
        s = summary[name]
        if not s["fits"]:
            continue
        nums = (f"d {s['d']:.2f} · {s['bytes']:,.0f} B · {s['msgs']:.0f} msgs · core SSIM "
                f"{s['ssim']:.3f} (pjpg {p_ssim:.3f}) · ΔE card {s['de']:.2f} (pjpg {p_de:.2f}) · "
                f"ΔE vs RAW {s['de_raw']:.2f} · tag acutance {s['acu']:.2f} (pjpg {p_acu:.2f}) · "
                f"Pi Zero {s['pi']} · colour {s['colour']} · detail {s['detail']}")
        for tag, label in (("card", "card + checker"), ("tex", "texture")):
            bi, bf = two(run / "slides" / f"{name}_{tag}_ref.png", f"{name}_{tag}_ref")
            ai, af = two(run / "slides" / f"{name}_{tag}_nrjxl.png", f"{name}_{tag}_nrjxl")
            where = ("540×350 native px" if tag == "card" else
                     "the most detailed 480×270 window in this ROI away from the card, picked on "
                     "the RAW before any codec result was looked at")
            sections.append({
                "title": f"{name} · {label} — {s['overall']}",
                "note": f"{nums}. View: {where}; inline pixels enlarged 2×, full screen is 1:1.",
                "before": bi, "before_full": bf, "before_label": "RAW reference",
                "pixelated": True,
                "afters": [{"label": f"nrjxl {s['msgs']:.0f} msgs", "image": ai, "image_full": af}]})
    bi, bf = two(run / "slides" / "pjpg_card_ref.png", "pjpg_card_ref")
    ai, af = two(run / "slides" / "pjpg_card.png", "pjpg_card")
    sections.append({
        "title": "Today's pjpg · card + checker (the bar)",
        "note": f"q80, {p_bytes:,.0f} B, {p_msgs:.0f} msgs (its own 195-message cap). UPSAMPLED: the "
                "pjpg is sent at 1000×562 and shown 1.6× up to the sensor grid. Each side gets its "
                "own card matrix (the pjpg is the camera's ISP output, the RAW is not).",
        "before": bi, "before_full": bf, "before_label": "RAW reference", "pixelated": True,
        "afters": [{"label": "today's pjpg (upsampled 1.6×)", "image": ai, "image_full": af}]})

    big = summary["2304x1296"]
    f3 = summary["3072x1728"]
    answer = (f"Largest ROI that fits 180 msgs and is ≥ today's quality: {best.replace('x', '×')} "
              f"(d {b['d']:.2f}, {b['msgs']:.0f} msgs), and the Pi Zero encodes it in {b['pi']}. "
              f"2000×1124 and 2304×1296 also fit and the Zero encodes them, but they are WARN "
              f"(tag edges overshoot / soften vs RAW); 3072×1728 needs {f3['msgs']:.0f} msgs at d 15; "
              f"the full frame does not fit and trips the Zero's memory guard.")
    spec = {
        "title": "ROI sweep at 180 messages",
        "eyebrow": "Nereus Vision · IMX708 on nereus002 · 2026-10-03 · card at ≈ 1.5 m",
        "lede": answer,
        "inline_max_width": 1400,
        "facts": [
            {"value": f"{best.replace('x', '×')}", "label": "largest ROI ≥ today's quality at 180 msgs"},
            {"value": f"ΔE {b['de']:.2f} vs {p_de:.2f}", "label": "card colour error, nrjxl vs today's pjpg (lower is better)"},
            {"value": f"SSIM {b['ssim']:.3f} vs {p_ssim:.3f}", "label": "detail on the card area vs the RAW (1 = identical)"},
            {"value": b["pi"], "label": "production encode on the Pi Zero 2 W"}],
        "sections": sections,
        "notes": [
            "Slider: left of the divider is the RAW reference, right is nrjxl decoded back to raw; "
            "both rendered by one function (bilinear demosaic, the camera's locked WB gains, "
            "clipped at sensor white, the RAW's own card matrix, sRGB). Only today's pjpg is upsampled.",
            f"ΔE reliability: {len(pst['usable'])} of 13 card colour patches are usable (≈ 190 px "
            "each, ≈ 47 per Bayer plane). red_orange sits in glare; dark_brown and magenta are too "
            "noisy (within-patch spread > 8 %). The grey patches are in the plastic-wrap glare, so "
            "white balance uses the camera's locked AWB gains.",
            f"The RAW's own ΔE varies {min(raw_de):.2f}–{max(raw_de):.2f} across three identical "
            "frames, so ΔE differences under about 0.3 are noise. A 3×3 fit on 10 patches is close "
            "to exact and flatters every image; ΔE vs RAW (no fit) is the cleaner compression-only "
            "number.",
            "Tag sharpness uses the 2 right-hand tags (≈ 32 px); glare hides the left two from the "
            "decoder (they were placed from their black borders for the card geometry).",
            "Pi Zero: the production rc_raw_jxl CLI (bm #120, e5, cjxl under its 250 MiB guard), run "
            "with an extra 700 MiB address-space cap and nice 10, while recorder_web / workbench / "
            "field_power_log kept running. Full frame: the guard stops cjxl after 2.7 s (rfb=mem); "
            "≈ 260 MiB is an ESTIMATE scaled from the 2304 and 3072 runs (64 → 113 MiB).",
            "Levers not tried: effort 7 (smaller files, slower), and a 195-message budget (3072×1728 "
            f"fits at {f3['msgs']:.0f} msgs, d 15)."],
        "footer": "Data: compression_study/presets/runs/sweep_20261003T203525Z (3 locked RAW frames, "
                  "86 ms, gain 1.12). Encoder: bm_cam_legacy PR #120 head bd6f38c; identical bytes "
                  "on the Mac and the Pi.",
    }
    (out / "spec.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False))
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
